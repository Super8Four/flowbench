import copy
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.imports import import_runs
from app.presets import normalize
from app.runner import RunManager
from app.storage import SQLiteArchive
from test_imports import saved_run


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.app = create_app({'TESTING': True, 'RUNS_DIRECTORY': self.temp.name, 'IPERF_PATH': 'missing-iperf'})
        self.manager = self.app.extensions['iperf_runs']
        self.store = self.manager.storage
        self.client = self.app.test_client()

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def restart(self):
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, 'missing-iperf')
        self.store = self.manager.storage

    def seed_legacy(self, directory, record=None):
        record = copy.deepcopy(record or saved_run())
        events = record.pop('events')
        record['event_count'] = len(events)
        path = directory / record['id']
        path.mkdir(parents=True)
        (path / 'run.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        (path / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
        catalog = {'schema_version': 1, 'presets': [normalize({'name': 'Private café', 'settings': {'target': 'example.test'}})]}
        (directory / 'server-presets.json').write_text(json.dumps(catalog), encoding='utf-8')
        return record, events, catalog

    def test_legacy_migration_rollback_export_roundtrip_and_idempotence(self):
        self.manager.shutdown()
        # Use a fresh archive, as on first upgrade; migration is intentionally once-only.
        source = self.directory / 'old'
        record, events, catalog = self.seed_legacy(source)
        before = {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}
        manager = RunManager(source, 'missing-iperf')
        try:
            self.assertEqual(manager.read(record['id']), record)
            self.assertEqual(manager.read_events(record['id']), events)
            self.assertEqual(manager.storage.catalog(), catalog)
            exported = manager.export(record['id'])
            for name, body in before.items():
                self.assertEqual((source / name).read_bytes(), body)
            # Notes saved in SQLite must not be reverted by the untouched legacy copy.
            manager.annotate(record['id'], {'notes': 'After upgrade'})
            expected = manager.export(record['id'])
            manager.shutdown()
            manager = RunManager(source, 'missing-iperf')
            self.assertEqual(manager.export(record['id']), expected)
            rollback = self.directory / 'rollback'
            manager.storage.export_legacy(rollback)
            with self.assertRaises(FileExistsError):
                manager.storage.export_legacy(rollback)
            restored = RunManager(rollback, 'missing-iperf')
            try:
                self.assertEqual(restored.export(record['id']), expected)
                self.assertEqual(restored.storage.catalog(), catalog)
            finally:
                restored.shutdown()
            self.assertEqual(exported['events'], events)
        finally:
            manager.shutdown()

    def test_invalid_legacy_migration_is_atomic_retryable_and_keeps_source(self):
        source = self.directory / 'invalid'
        record, _, _ = self.seed_legacy(source)
        broken = source / ('b' * 32)
        broken.mkdir()
        bad_file = broken / 'run.json'
        bad_file.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            RunManager(source, 'missing-iperf')
        store = SQLiteArchive(source)
        self.assertEqual(store.records(), [])
        self.assertIsNone(store.catalog())
        self.assertEqual(bad_file.read_text(), '{broken')
        # Repair only the disposable fixture and retry; failed startup released its lock.
        repaired = dict(record, id='b' * 32)
        bad_file.write_text(json.dumps(repaired), encoding='utf-8')
        reopened = RunManager(source, 'missing-iperf')
        try:
            self.assertEqual(len(reopened.list_runs()), 2)
        finally:
            reopened.shutdown()

    def test_legacy_partial_final_event_and_interrupted_recovery(self):
        source = self.directory / 'crashed'
        record = saved_run()
        record.update(status='running', finished_at=None)
        original, events, _ = self.seed_legacy(source, record)
        path = source / record['id'] / 'events.jsonl'
        with path.open('a', encoding='utf-8') as output:
            output.write('{"sequence":2')
        before = path.read_bytes()
        manager = RunManager(source, 'missing-iperf')
        try:
            self.assertEqual(manager.read(original['id'])['status'], 'interrupted')
            self.assertEqual(manager.read_events(original['id']), events)
            self.assertEqual(path.read_bytes(), before)
        finally:
            manager.shutdown()

    def test_legacy_terminal_checkpoint_without_finish_date_is_interrupted(self):
        source = self.directory / 'terminal-checkpoint'
        record = saved_run()
        record['finished_at'] = None
        self.seed_legacy(source, record)
        manager = RunManager(source, 'missing-iperf')
        try:
            restored = manager.export(record['id'])
            self.assertEqual(restored['status'], 'interrupted')
            self.assertIsNotNone(restored['finished_at'])
            self.assertEqual(restored['summary'], record['summary'])
            self.assertEqual(restored['events'], record['events'])
        finally:
            manager.shutdown()

    def test_comparison_import_rolls_back_every_run_on_write_failure(self):
        left, right = saved_run(), saved_run()
        right['id'] = 'b' * 32
        original = self.store.insert_events
        calls = []
        def fail_second(*args):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('Simulated disk exhaustion')
            return original(*args)
        with patch.object(self.store, 'insert_events', side_effect=fail_second):
            response = self.client.post('/api/runs/import', json={'content': json.dumps({'before': left, 'after': right})})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.store.records(), [])
        result = import_runs(self.manager, {'before': left, 'after': right}, 'comparison.json')
        self.assertEqual(len(result['imported']), 2)
        self.assertEqual(import_runs(self.manager, {'before': left, 'after': right}, 'comparison.xml')['duplicates'], result['imported'])
        self.restart()
        self.assertEqual(len(self.manager.list_runs()), 2)

    def test_log_transaction_failure_retains_truthful_memory_and_disk(self):
        record = saved_run()
        record.pop('events')
        record['event_count'] = 0
        self.manager.save(record)
        self.manager.record = copy.deepcopy(record)
        self.manager.active = record['id']
        try:
            with patch.object(self.store, 'insert_events', side_effect=sqlite3.OperationalError('database or disk is full')):
                with self.assertRaises(OSError):
                    self.manager.log('stdout', '{"event":"interval","data":{"sum":{"end":2,"bits_per_second":123}}}')
            self.assertEqual(self.store.read(record['id']), record)
            self.assertEqual(self.store.events(record['id']), [])
            self.assertEqual(self.manager.record, record)
            self.assertEqual(self.manager.events, [])
            self.assertTrue(self.manager.storage_error)
            self.manager.capabilities['available'] = True
            self.assertEqual(self.client.get('/api/ready').status_code, 503)
            self.assertEqual(self.client.get('/api/health').status_code, 503)
            with self.assertRaises(RuntimeError):
                self.manager.start({'target': '127.0.0.1'})
        finally:
            self.manager.active = None

    def test_lock_timeout_and_release_preserve_existing_evidence(self):
        run_id = import_runs(self.manager, saved_run(), 'test.json')['imported'][0]
        previous = self.manager.export(run_id)
        blocker = sqlite3.connect(self.store.path, isolation_level=None)
        self.store.timeout = .05
        try:
            blocker.execute('BEGIN IMMEDIATE')
            response = self.client.patch('/api/runs/' + run_id, json={'notes': 'Must not commit'})
            self.assertEqual(response.status_code, 503)
            self.manager.capabilities['available'] = True
            self.assertEqual(self.client.get('/api/ready').status_code, 503)
            self.assertEqual(self.manager.export(run_id), previous)  # WAL reads still work.
        finally:
            blocker.rollback()
            blocker.close()
        self.assertEqual(self.client.get('/api/ready').status_code, 200)
        self.assertEqual(self.client.patch('/api/runs/' + run_id, json={'notes': 'Now committed'}).status_code, 200)
        self.restart()
        self.assertEqual(self.manager.read(run_id)['settings']['notes'], 'Now committed')

    def test_sqlite_page_limit_reports_full_and_rolls_back_captured_event(self):
        record = saved_run()
        record.pop('events')
        record['event_count'] = 0
        self.manager.save(record)
        self.manager.record = copy.deepcopy(record)
        self.manager.active = record['id']
        original = self.store.connection
        @contextmanager
        def limited_connection():
            with original() as connection:
                pages = connection.execute('PRAGMA page_count').fetchone()[0]
                connection.execute(f'PRAGMA max_page_count={pages}')
                yield connection
        try:
            with patch.object(self.store, 'connection', limited_connection):
                with self.assertRaisesRegex(OSError, 'full'):
                    self.manager.log('stderr', 'x' * (2 * 1024 * 1024))
            self.assertEqual(self.store.read(record['id']), record)
            self.assertEqual(self.store.events(record['id']), [])
            self.assertEqual(self.manager.events, [])
            self.assertTrue(self.manager.storage_error)
        finally:
            self.manager.active = None

    def test_storage_failure_after_process_start_cleans_up_child(self):
        process = Mock(stdout=io.StringIO(), stderr=io.StringIO())
        process.poll.return_value = None
        process.wait.return_value = -1
        original = self.manager.save
        def fail_running(record):
            if record['status'] == 'running':
                raise OSError('Simulated storage failure after Popen')
            return original(record)
        self.manager.capabilities['available'] = True
        with patch('app.runner.subprocess.Popen', return_value=process), patch.object(self.manager, 'save', side_effect=fail_running):
            record = self.manager.start({'target': '127.0.0.1'})
            self.manager.worker.join(5)
        self.assertFalse(self.manager.worker.is_alive())
        process.kill.assert_called_once()
        self.assertTrue(process.stdout.closed and process.stderr.closed)
        self.assertIsNone(self.manager.process)
        self.assertIsNone(self.manager.active)
        self.assertEqual(self.manager.read(record['id'])['status'], 'failed')

    def test_stop_still_terminates_child_when_event_write_fails(self):
        record = saved_run()
        record.pop('events')
        self.manager.save(record)
        self.manager.record = record
        self.manager.active = record['id']
        self.manager.process = Mock()
        self.manager.process.poll.return_value = None
        try:
            with patch.object(self.store, 'append', side_effect=OSError('Permission denied')):
                with self.assertRaises(OSError):
                    self.manager.stop(record['id'])
            self.manager.process.terminate.assert_called_once()
            self.assertTrue(self.manager.cancel_requested)
        finally:
            self.manager.process = None
            self.manager.active = None

    def test_cli_initialization_and_rollback_export_checks(self):
        runner = self.app.test_cli_runner()
        self.assertEqual(runner.invoke(args=['init-db']).exit_code, 0)
        run_id = import_runs(self.manager, saved_run(), 'test.json')['imported'][0]
        destination = self.directory / 'cli-rollback'
        self.manager.active = run_id
        result = runner.invoke(args=['export-legacy', str(destination)])
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(destination.exists())
        self.manager.active = None
        result = runner.invoke(args=['export-legacy', str(destination)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue((destination / run_id / 'run.json').exists())
        self.assertNotEqual(runner.invoke(args=['export-legacy', str(destination)]).exit_code, 0)

    def test_concurrent_capture_notes_imports_and_exports(self):
        record = saved_run()
        record.pop('events')
        record['event_count'] = 0
        self.manager.save(record)
        self.manager.record = copy.deepcopy(record)
        self.manager.active = record['id']
        def capture():
            for index in range(20):
                self.manager.log('stdout', f'line {index}')
        def annotate():
            for index in range(20):
                self.manager.annotate(record['id'], {'notes': f'note {index}'})
        def read():
            for _ in range(20):
                exported = self.manager.export(record['id'])
                self.assertEqual(exported['event_count'], len(exported['events']))
        def imports():
            for _ in range(5):
                import_runs(self.manager, saved_run(), 'repeat.json')
        try:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(task) for task in (capture, annotate, read, imports)]
                for future in futures:
                    future.result(timeout=30)
            self.assertEqual([event['sequence'] for event in self.store.events(record['id'])], list(range(1, 21)))
            self.assertEqual(self.manager.read(record['id'])['settings']['notes'], 'note 19')
            self.assertEqual(len(self.store.records()), 2)
        finally:
            self.manager.active = None
        self.restart()
        self.assertEqual(len(self.manager.read_events(record['id'])), 20)

    def test_killed_transaction_and_integrity(self):
        run_id = import_runs(self.manager, saved_run(), 'before.json')['imported'][0]
        original = self.manager.export(run_id)
        script = "import sqlite3,sys,os; c=sqlite3.connect(sys.argv[1], isolation_level=None); c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE runs SET document=?', ('{}',)); os._exit(17)"
        process = subprocess.run([sys.executable, '-c', script, str(self.store.path)], timeout=10)
        self.assertEqual(process.returncode, 17)
        self.restart()
        self.assertEqual(self.manager.export(run_id), original)
        with self.store.connection() as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(connection.execute('PRAGMA synchronous').fetchone()[0], 2)

    def test_corrupt_or_future_database_fails_startup_without_replacing_bytes(self):
        for name, content in [('corrupt', b'not a database'), ('future', None)]:
            directory = self.directory / name
            directory.mkdir()
            path = directory / 'archive.sqlite3'
            if content:
                path.write_bytes(content)
            else:
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute('PRAGMA user_version=99')
            before = path.read_bytes()
            with self.assertRaises(OSError):
                RunManager(directory, 'missing-iperf')
            self.assertEqual(path.read_bytes(), before)

    def test_failed_annotation_and_preset_change_do_not_publish_unsaved_values(self):
        record = saved_run()
        record.pop('events')
        self.manager.save(record)
        self.manager.record = copy.deepcopy(record)
        with patch.object(self.store, 'save', side_effect=OSError('Permission denied')):
            self.assertEqual(self.client.patch('/api/runs/' + record['id'], json={'notes': 'Unsaved'}).status_code, 503)
        self.assertEqual(self.manager.record, record)
        catalog = self.app.extensions['iperf_presets'].read()
        with patch.object(self.store, 'save_catalog', side_effect=OSError('Permission denied')):
            self.assertEqual(self.client.post('/api/presets', json={'name': 'Unsaved', 'settings': {'target': 'example.test'}}).status_code, 503)
        self.assertEqual(self.app.extensions['iperf_presets'].read(), catalog)


if __name__ == '__main__':
    unittest.main()
