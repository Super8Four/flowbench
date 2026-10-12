"""Test a built image with disposable containers and a disposable volume.

Run: python3 scripts/check-container.py [image]
Requires Docker; does not use the production Compose project or its archive.
"""

import json
import io
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZipFile


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    image = sys.argv[1] if len(sys.argv) > 1 else "iperf-workspace:local"
    name = "iperf-check-" + uuid4().hex[:12]
    volume = name + "-data"
    base = ""

    def request(path, data=None, raw=False, method=None):
        payload = None if data is None else json.dumps(data).encode()
        with urlopen(Request(base + path, data=payload, method=method,
                             headers={"Content-Type": "application/json"}), timeout=10) as response:
            body = response.read()
            return body if raw else json.loads(body)

    def start():
        nonlocal base
        docker("run", "--detach", "--name", name, "--init", "--read-only",
               "--tmpfs", "/tmp:size=32m,mode=1777", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges:true", "--publish", "127.0.0.1::5000",
               "--mount", f"type=volume,source={volume},target=/data", image)
        port = docker("port", name, "5000/tcp").split(":")[-1]
        base = "http://127.0.0.1:" + port
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if request("/api/ready")["ready"]:
                    return
            except (URLError, OSError):
                pass
            time.sleep(.2)
        raise RuntimeError("Container did not become ready: " + docker("logs", name))

    def stop_and_remove():
        docker("stop", "--time", "20", name)
        assert docker("inspect", "--format", "{{.State.ExitCode}}", name) == "0", "Container did not stop cleanly"
        docker("rm", name)

    def test_run(duration, mode="bidir"):
        docker("exec", "--detach", name, "iperf3", "-s", "-1", "-B", "127.0.0.1", "-p", "5202")
        time.sleep(.4)
        return request("/api/runs", {"target": "127.0.0.1", "port": 5202,
                                      "duration": duration, "streams": 2, "mode": mode,
                                      "label": "Disposable container verification"})["id"]

    def incoming_runs():
        return [row for row in request('/api/runs')['runs'] if row.get('incoming') and not row.get('imported')]

    def wait_incoming(count):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            rows = incoming_runs()
            if len(rows) >= count and all(row['finished_at'] for row in rows):
                return rows
            time.sleep(.1)
        raise RuntimeError('Incoming sessions did not finish: ' + str(request('/api/listener')))

    docker("volume", "create", volume)
    try:
        # Seed only this disposable volume with the pre-SQLite file layout.
        legacy_id = "f" * 32
        seed = '''
import json
from pathlib import Path
from app.runner import settings_from
from app.presets import normalize
root = Path('/data/runs')
path = root / ('f' * 32)
path.mkdir(parents=True)
record = dict(schema_version=1, id='f'*32, created_at='2026-01-01T00:00:00+00:00',
              finished_at='2026-01-01T00:00:01+00:00', status='completed', exit_code=0,
              settings=settings_from(dict(target='127.0.0.1', notes='Legacy volume evidence')),
              summary={}, intervals=[], command=[], error=None, native_result=None,
              test_parameters=None, event_count=1)
event = dict(sequence=1, timestamp=record['created_at'], source='stdout', message='legacy raw output')
(path / 'run.json').write_text(json.dumps(record), encoding='utf-8')
(path / 'events.jsonl').write_text(json.dumps(event)+'\\n', encoding='utf-8')
catalog = dict(schema_version=1, presets=[normalize(dict(name='Legacy preset', settings=dict(target='127.0.0.1')))])
(root / 'server-presets.json').write_text(json.dumps(catalog), encoding='utf-8')
'''
        docker("run", "--rm", "--mount", f"type=volume,source={volume},target=/data",
               "--entrypoint", "python", image, "-c", seed)
        start()
        status = request("/api/status")
        assert status["iperf"]["full_json"] and status["iperf"]["udp_bidir"]
        assert status["storage"] == "/data/runs"
        assert status["storage_backend"] == "sqlite" and status["database"] == "/data/runs/archive.sqlite3"
        assert request("/api/health")["database"] == "connected"
        legacy = request(f"/api/runs/{legacy_id}/export")
        assert legacy["settings"]["notes"] == "Legacy volume evidence"
        assert legacy["events"][0]["message"] == "legacy raw output"
        assert any(row["name"] == "Legacy preset" for row in request("/api/presets")["presets"])
        # Legacy sources remain intact, but must never revert newer SQLite notes.
        payload = json.dumps({"notes": "Updated after SQLite migration"})
        with urlopen(Request(base + f"/api/runs/{legacy_id}", data=payload.encode(), method="PATCH",
                             headers={"Content-Type": "application/json"}), timeout=5) as response:
            assert response.status == 200
        assert docker("exec", name, "id", "-u") == "10001"
        assert b'data-bs-theme="dark"' in request("/", raw=True)
        assert b'brand/flowbench-logo.svg' in request('/', raw=True)
        assert b'brand/flowbench-logo.png' in request('/', raw=True)
        svg = request('/static/brand/flowbench-logo.svg', raw=True)
        assert b'<path ' in svg and b'<image' not in svg and b'<script' not in svg
        assert request('/static/brand/flowbench-logo.png', raw=True).startswith(b'\x89PNG\r\n\x1a\n')
        version = docker('exec', name, 'python', '-c', 'from app.version import __version__; print(__version__)')
        assert status['version'] == version
        assert docker('image', 'inspect', '--format', '{{index .Config.Labels "org.opencontainers.image.version"}}', image) == version
        assert docker("exec", name, "python", "-m", "app.healthcheck") == ""
        run_id = test_run(2)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            run = request("/api/runs/" + run_id)
            if run["status"] in {"completed", "failed"} and run["finished_at"]:
                break
            time.sleep(.2)
        assert run["status"] == "completed", run
        assert run["metrics"]["upload_mbps"] > 0 and run["metrics"]["download_mbps"] > 0
        assert run["peer"]["available"] and run["events"]
        exported = request(f"/api/runs/{run_id}/export?format=json")
        xml_body = request(f"/api/runs/{run_id}/export?format=xml", raw=True)
        xml = ElementTree.fromstring(xml_body)
        assert xml.find("./field[@name='id']").text == run_id
        imported = request("/api/runs/import", {"filename": "test.json", "content": json.dumps(exported)})["imported"][0]
        assert request("/api/runs/import", {"filename": "test.xml", "content": xml_body.decode()})["duplicates"] == [imported]
        imported_export = request(f"/api/runs/{imported}/export")
        assert imported_export["events"] == exported["events"] and imported_export["summary"] == exported["summary"]
        # Persistent peer: Both creates two sequential connections, not one simultaneous test.
        docker("exec", "--detach", name, "iperf3", "-s", "-B", "127.0.0.1", "-p", "5203")
        time.sleep(.4)
        job_id = request("/api/runs", {"target": "127.0.0.1", "port": 5203, "duration": 1,
                                     "protocol": "both", "bandwidth": "2M", "label": "Both protocol check"})["id"]
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            job = request(f"/api/runs/{job_id}")
            if job["status"] in {"completed", "failed", "partial"} and job["finished_at"]:
                break
            time.sleep(.2)
        assert job["status"] == "completed", job
        assert job["settings"]["mode"] == "bidir" and job["metrics"] == {}
        group_export = request(f"/api/runs/{job_id}/export")
        assert [run["settings"]["protocol"] for run in group_export["runs"]] == ["tcp", "udp"]
        for run in group_export["runs"]:
            assert run["metrics"]["upload_mbps"] > 0 and run["metrics"]["download_mbps"] > 0
            assert {point["direction"] for point in run["series"]} == {"upload", "download"}
        group_import = request("/api/runs/import", {"filename": "both.json", "content": json.dumps(group_export)})
        group_xml = request(f"/api/runs/{job_id}/export?format=xml", raw=True)
        assert request("/api/runs/import", {"filename": "both.xml", "content": group_xml.decode()})["duplicates"] == group_import["imported"]
        # Two bounded sequential children share the disposable persistent peer.
        # Diagnostics run under dropped capabilities: unavailable/blocked evidence
        # is a valid outcome and must never be converted to successful RTT data.
        project = request('/api/projects', {'name': 'Disposable loopback change',
                                           'description': 'Container workflow verification'})
        tags = {'site': 'Synthetic lab', 'device': 'Loopback', 'circuit': 'Disposable', 'ticket': 'CI'}
        request(f'/api/runs/{run_id}', {'project_id': project['id'], 'tags': tags}, method='PATCH')
        request('/api/projects/' + project['id'], {'baseline_id': run_id}, method='PATCH')
        exported = request(f'/api/runs/{run_id}/export')
        matrix = request('/api/matrices', {'settings': {
            'target': '127.0.0.1', 'port': 5203, 'duration': 1, 'bandwidth': '2M',
            'label': 'Bounded native diagnostics', 'project_id': project['id'], 'tags': tags,
            'latency': True, 'endpoint_health': True, 'mtu': True, 'traceroute': True},
            'protocols': ['tcp', 'udp'], 'modes': ['upload'], 'streams': [1]})
        assert len(matrix['children']) == 2
        first_child = matrix['children'][0]['id']
        live = request(f'/api/live-comparison?baseline={run_id}&active={first_child}')
        assert live['baseline_id'] == run_id and live['active_id'] == first_child
        assert {'mode', 'duration', 'streams'} <= set(live['settings_differences'])
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            matrix_run = request('/api/runs/' + matrix['id'])
            if matrix_run['finished_at']:
                break
            time.sleep(.2)
        assert matrix_run['status'] == 'completed', matrix_run
        matrix_export = request('/api/runs/' + matrix['id'] + '/export')
        assert [row['settings']['protocol'] for row in matrix_export['runs']] == ['tcp', 'udp']
        assert matrix_run['metrics'] == {}, 'Different matrix tests were combined into a throughput value'
        for child in matrix_export['runs']:
            assert child['metrics']['upload_mbps'] > 0, child
            evidence = child['diagnostics']
            assert evidence['schema_version'] == 1
            assert evidence['latency']['metric'] == 'ICMP echo RTT'
            assert len(evidence['latency']['baseline']['samples']) <= 3
            assert len(evidence['latency']['loaded']['samples']) <= 120
            assert evidence['endpoint_health']['remote']['status'] == 'unavailable'
            assert evidence['endpoint_health']['local']['before']['source'] == 'Linux /proc'
            assert len(evidence['path_context']['mtu']['tested_packet_sizes']) <= 3
            assert child['route']['target'] == '127.0.0.1' and len(child['route']['hops']) <= 16
            assert child['settings']['tags'] == tags
        request('/api/runs/' + first_child, {'annotations': [
            {'seconds': .5, 'text': 'Synthetic observation during loopback test'}]}, method='PATCH')
        matrix_export = request('/api/runs/' + matrix['id'] + '/export')
        assert matrix_export['runs'][0]['annotations'][0]['seconds'] == .5
        matrix_xml = request('/api/runs/' + matrix['id'] + '/export?format=xml', raw=True)
        matrix_import = request('/api/runs/import', {'filename': 'matrix.json', 'content': json.dumps(matrix_export)})
        assert len(matrix_import['imported']) == 3
        assert request('/api/runs/import', {'filename': 'matrix.xml', 'content': matrix_xml.decode()})['duplicates'] == matrix_import['imported']
        imported_matrix_export = request('/api/runs/' + matrix_import['imported'][0] + '/export')
        for original_child, imported_child in zip(matrix_export['runs'], imported_matrix_export['runs']):
            for field in ('diagnostics', 'route', 'annotations', 'events'):
                assert imported_child.get(field) == original_child.get(field), field
        project_report = request('/api/projects/' + project['id'])
        assert project_report['project']['baseline_id'] == run_id
        assert project_report['comparisons'] and all(not row['comparable'] for row in project_report['comparisons'])
        project_export = request('/api/projects/' + project['id'] + '/export')
        project_xml = request('/api/projects/' + project['id'] + '/export?format=xml', raw=True)
        project_import = request('/api/projects/import', {'filename': 'project.json', 'content': json.dumps(project_export)})
        assert project_import['project']['id'] != project['id']
        assert project_import['project']['baseline_id'] != run_id
        assert request('/api/projects/import', {'filename': 'project.xml', 'content': project_xml.decode()})['duplicates'] == [project_import['project']['id']]
        project_imported_export = request('/api/projects/' + project_import['project']['id'] + '/export')
        # Read actual SQLite documents, rather than only trusting an HTTP cache.
        database_evidence = docker('exec', name, 'python', '-c',
            "import json,sqlite3; c=sqlite3.connect('/data/runs/archive.sqlite3'); "
            f"r=json.loads(c.execute('SELECT document FROM runs WHERE id=?', ('{first_child}',)).fetchone()[0]); "
            "print(json.dumps({key:r[key] for key in ('diagnostics','annotations','settings')})); c.close()")
        database_evidence = json.loads(database_evidence)
        assert database_evidence['diagnostics'] == matrix_export['runs'][0]['diagnostics']
        assert database_evidence['annotations'] == matrix_export['runs'][0]['annotations']
        assert database_evidence['settings']['tags'] == tags
        # Reports consume committed individual measurements, never a combined
        # parent or invented probe value. These are structural/redaction checks;
        # they do not replace the separate rendered-page visual verification.
        report_request = {'ids': [run_id, first_child], 'redact': True, 'include_raw': False}
        preview = request('/api/reports/preview', report_request)
        assert preview['kind'] == 'flowbench-report' and preview['schema_version'] == 1
        assert preview['redacted'] and not preview['include_raw'] and len(preview['runs']) == 2
        assert '127.0.0.1' not in json.dumps(preview), 'Default report leaked the selected endpoint'
        for _ in range(2):
            pdf_body = request('/api/reports/download?format=pdf', report_request, raw=True)
            assert pdf_body.startswith(b'%PDF-') and b'%%EOF' in pdf_body[-1024:]
            docx_body = request('/api/reports/download?format=docx', report_request, raw=True)
            with ZipFile(io.BytesIO(docx_body)) as document:
                assert document.testzip() is None
                assert '[Content_Types].xml' in document.namelist()
                document_xml = document.read('word/document.xml')
                ElementTree.fromstring(document_xml)
                assert b'FlowBench' in document_xml and b'127.0.0.1' not in document_xml
        for invalid in ({'ids': [matrix['id']]}, {'ids': [run_id, run_id]}):
            try:
                request('/api/reports/preview', invalid)
            except HTTPError as error:
                assert error.code == 400
                error.close()
            else:
                raise AssertionError('Report accepted a job parent or duplicate IDs')
        # An independent listener captures ordinary raw clients without a browser/API-created run.
        request('/api/listener', {'bind': '127.0.0.1', 'port': 5204, 'max_duration': 10})
        time.sleep(.4)
        for flags in [[], ['-R'], ['-u', '--bidir']]:
            native = json.loads(docker('exec', name, 'iperf3', '-c', '127.0.0.1', '-p', '5204', '-t', '1', '-b', '2M', '-J', *flags))
            assert not native.get('error'), native
        captures = wait_incoming(3)
        assert len({row['id'] for row in captures}) == 3
        assert all(row['status'] == 'completed' for row in captures), captures
        incoming_export = request('/api/runs/' + captures[0]['id'] + '/export')
        assert incoming_export['incoming']['remote_host'] == '127.0.0.1'
        assert incoming_export['events'] and incoming_export['native_result']
        docker('exec', '--detach', name, 'iperf3', '-c', '127.0.0.1', '-p', '5204', '-t', '4', '-b', '2M', '-J')
        deadline = time.monotonic() + 5
        while not request('/api/listener')['active_run'] and time.monotonic() < deadline:
            time.sleep(.1)
        overlap_incoming = request('/api/listener')['active_run']
        assert overlap_incoming
        overlap_outgoing = test_run(1)
        wait_incoming(4)
        assert overlap_outgoing in request('/api/runs/' + overlap_incoming)['concurrency']['overlap_run_ids']
        assert overlap_incoming in request('/api/runs/' + overlap_outgoing)['concurrency']['overlap_run_ids']
        preset = request("/api/presets", {"name": "Disposable loopback preset", "settings": {"target": "127.0.0.1", "port": 5202}})
        memory = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", name)
        stop_and_remove()
        start()
        assert request('/api/listener')['state'] == 'listening', 'Enabled listener did not resume'
        assert len(incoming_runs()) == 4, 'Incoming evidence was duplicated on restart'
        assert request('/api/runs/' + captures[0]['id'] + '/export') == incoming_export
        docker('exec', name, 'iperf3', '-c', '127.0.0.1', '-p', '5204', '-t', '1', '-b', '2M', '-J')
        assert len(wait_incoming(5)) == 5
        assert request(f"/api/runs/{run_id}/export?format=json") == exported, "Evidence changed after replacement"
        assert request(f"/api/runs/{imported}/export") == imported_export, "Import changed after replacement"
        assert request(f"/api/runs/{job_id}/export") == group_export, "Both evidence changed after replacement"
        assert request('/api/runs/' + matrix['id'] + '/export') == matrix_export, 'Matrix diagnostics changed after replacement'
        assert request('/api/runs/' + matrix_import['imported'][0] + '/export') == imported_matrix_export, 'Imported matrix changed after replacement'
        assert request('/api/projects/' + project['id'] + '/export') == project_export, 'Project baseline or evidence changed after replacement'
        assert request('/api/projects/' + project_import['project']['id'] + '/export') == project_imported_export, 'Imported project changed after replacement'
        assert request(f"/api/runs/{legacy_id}")["settings"]["notes"] == "Updated after SQLite migration"
        original = docker("exec", name, "python", "-c",
                          f"import json; print(json.load(open('/data/runs/{legacy_id}/run.json'))['settings']['notes'])")
        assert original == "Legacy volume evidence", "Legacy source was modified"
        assert docker("exec", name, "python", "-c",
                      "import sqlite3; c=sqlite3.connect('/data/runs/archive.sqlite3'); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()") == "ok"
        assert any(row["id"] == preset["id"] for row in request("/api/presets")["presets"]), "Preset was lost"
        docker('exec', '--detach', name, 'iperf3', '-s', '-B', '127.0.0.1', '-p', '5203')
        time.sleep(.4)
        active_matrix = request('/api/matrices', {'settings': {
            'target': '127.0.0.1', 'port': 5203, 'duration': 30, 'label': 'Shutdown matrix'},
            'protocols': ['tcp'], 'modes': ['upload'], 'streams': [1, 2]})
        active_id = active_matrix['children'][0]['id']
        docker('exec', '--detach', name, 'iperf3', '-c', '127.0.0.1', '-p', '5204', '-t', '8', '-b', '2M', '-J')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if request("/api/runs/" + active_id)["series"]:
                break
            time.sleep(.2)
        else:
            raise RuntimeError("No live interval received before stop")
        stop_and_remove()
        start()
        stopped = request("/api/runs/" + active_id)
        assert stopped["status"] == "cancelled", stopped["status"]
        assert stopped["series"] and stopped["events"] and stopped["finished_at"]
        cancelled_matrix = request('/api/runs/' + active_matrix['id'])
        assert cancelled_matrix['status'] == 'cancelled' and cancelled_matrix['finished_at']
        unstarted = request('/api/runs/' + active_matrix['children'][1]['id'])
        assert unstarted['status'] == 'skipped' and unstarted['finished_at'] and not unstarted['series']
        assert request('/api/status')['active_run'] is None, 'Matrix resumed traffic after replacement'
        interrupted = [row for row in incoming_runs() if row['status'] == 'cancelled']
        assert len(interrupted) == 1 and request('/api/runs/' + interrupted[0]['id'])['events']
        request('/api/listener/stop', {})
        assert request('/api/listener')['settings']['enabled'] is False
        size = int(docker("image", "inspect", "--format", "{{.Size}}", image)) / (1024 * 1024)
        result = f"Container checks passed: TCP/UDP Both bidirectional, remote output, bounded sequential matrix with native loopback diagnostics, local/remote health provenance, tags/timeline/project baseline, live mismatch overlay, JSON/XML run/matrix/project import/export and deduplication, repeated PDF/DOCX structure and report redaction/gating, direct SQLite evidence reads and restart persistence, managed raw-client listener, independent inbound/outbound overlap, listener resume without duplicate history, graceful incoming/matrix stop with unstarted children skipped, non-root/read-only runtime, SQLite integrity, legacy migration and volume/preset persistence. Image: {size:.1f} MiB; idle memory: {memory}."
        print(result)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as file:
                file.write(result + "\n")
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=False)
        subprocess.run(["docker", "volume", "rm", volume], check=False)


if __name__ == "__main__":
    main()
