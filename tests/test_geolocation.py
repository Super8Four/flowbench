import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.geolocation import classify_address, enrich_route, validate_route_geolocation
from app import create_app
from app.exports import xml_export
from app.runner import settings_from


def route_fixture():
    return {"status": "captured", "hops": [
        {"hop": 1, "address": "10.0.0.1", "addresses": ["10.0.0.1"], "rtt_ms": [1]},
        {"hop": 2, "address": "8.8.8.8", "addresses": ["8.8.8.8", "1.1.1.1"], "rtt_ms": [2]},
        {"hop": 3, "address": None, "addresses": [], "rtt_ms": []}]}


class FakeReader:
    def __init__(self, value=None, failure=False, database_type="DBIP-City-Lite"):
        self.value = value if value is not None else {"location": {"latitude": 10.5, "longitude": 20.25},
            "country": {"iso_code": "ZZ", "names": {"en": "Synthetic country"}},
            "city": {"names": {"en": "Synthetic fixture"}}}
        self.failure, self.database_type, self.lookups, self.closed = failure, database_type, [], False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def metadata(self):
        return SimpleNamespace(database_type=self.database_type, build_epoch=1790812800)

    def get(self, address):
        self.lookups.append(address)
        if self.failure:
            raise RuntimeError("private/database/path must not escape")
        return self.value


class GeolocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'fixture.mmdb'
        self.path.write_bytes(b'not a real dataset; mocked reader fixture')

    def tearDown(self):
        self.temp.cleanup()

    def locate(self, route=None, reader=None):
        reader = reader or FakeReader()
        with patch('app.geolocation.maxminddb.open_database', return_value=reader) as opened:
            result = enrich_route(route or route_fixture(), self.path)
        return result, reader, opened

    def test_scope_policy_blocks_internal_special_mapped_and_transition_ranges(self):
        internal = ('10.0.0.1', '172.16.0.1', '192.168.255.1', 'fc00::1', 'fd12::1')
        special = ('0.0.0.0', '127.0.0.1', '169.254.1.1', '100.64.0.1', '192.0.0.9', '192.0.2.1',
                   '192.88.99.1', '198.18.0.1', '198.51.100.1', '203.0.113.1', '224.0.0.1', '255.255.255.255',
                   '::', '::1', '::192.168.1.1', '::ffff:192.168.1.1', '::ffff:8.8.8.8', '::ffff:0:192.168.1.1',
                   '64:ff9b::a00:1', '64:ff9b::808:808', '64:ff9b:1::a00:1', '100::1',
                   '2001::1', '2002:0a00:0001::1', '2002:0808:0808::1', '2001:db8::1', '3fff::1',
                   '5f00::1', 'fe80::1', 'fe80::1%test', 'fec0::1', 'ff02::1',
                   '2001:4860::5efe:a00:1', '2001:4860::200:5efe:808:808', '2001:4860::1%test')
        for address in internal:
            self.assertEqual(classify_address(address), 'internal', address)
        for address in special:
            self.assertEqual(classify_address(address), 'special', address)
        for value in (None, {}, [], 10, 'localhost', 'name.example', '012.8.8.8'):
            self.assertEqual(classify_address(value), 'unknown')
        for address in ('8.8.8.8', '1.1.1.1', '2001:4860:4860::8888', '2606:4700:4700::1111'):
            self.assertEqual(classify_address(address), 'public', address)

    def test_every_alternate_public_address_located_once_and_reader_closed(self):
        original = route_fixture(); snapshot = copy.deepcopy(original)
        result, reader, opened = self.locate(original)
        self.assertEqual(original, snapshot)
        self.assertEqual(reader.lookups, ['1.1.1.1', '8.8.8.8'])
        self.assertTrue(reader.closed)
        opened.assert_called_once()
        self.assertEqual(len(result['hops'][1]['locations']), 2)
        self.assertEqual(result['hops'][0]['locations'][0]['status'], 'internal')
        self.assertIsNone(result['hops'][0]['locations'][0]['latitude'])
        self.assertEqual(result['hops'][2]['locations'][0]['status'], 'missing')
        self.assertEqual(result['geolocation']['status'], 'available')
        self.assertEqual(result['geolocation']['build_epoch'], 1790812800)
        self.assertNotIn(str(self.path), str(result))
        validate_route_geolocation(result)

    def test_internal_and_special_only_routes_never_open_a_database(self):
        route = {'hops': [{'hop': index + 1, 'addresses': [address], 'address': address}
                         for index, address in enumerate(('10.0.0.1', '100.64.0.1', '127.0.0.1', '::ffff:8.8.8.8', '2002:0808:0808::1'))]}
        result, reader, opened = self.locate(route)
        opened.assert_not_called()
        self.assertEqual(reader.lookups, [])
        self.assertEqual(result['geolocation']['status'], 'unavailable')
        self.assertTrue(all(row['latitude'] is None for hop in result['hops'] for row in hop['locations']))
        validate_route_geolocation(result)

    def test_missing_corrupt_disabled_and_reader_failures_remain_neutral(self):
        missing = enrich_route(route_fixture(), Path(self.temp.name) / 'missing.mmdb')
        self.assertEqual(missing['geolocation']['status'], 'unavailable')
        corrupt = enrich_route(route_fixture(), self.path)  # Real pure-Python mmap decoder rejects fake data.
        self.assertEqual(corrupt['geolocation']['status'], 'error')
        for selected in ('', 'https://example.test/database.mmdb', '//server/share/file.mmdb', r'\\server\share\file.mmdb'):
            result = enrich_route(route_fixture(), selected)
            self.assertEqual(result['geolocation']['status'], 'unavailable')
        self.assertEqual(enrich_route(route_fixture(), {})['geolocation']['status'], 'unavailable')
        with patch('app.geolocation.maxminddb', None):
            result = enrich_route(route_fixture(), self.path)
            self.assertEqual(result['geolocation']['status'], 'unavailable')
        result, reader, _ = self.locate(reader=FakeReader(failure=True))
        self.assertEqual(result['geolocation']['status'], 'error')
        self.assertTrue(reader.closed)
        self.assertNotIn('private/database/path', str(result))
        for value in (missing, corrupt, result):
            validate_route_geolocation(value)

    def test_unsupported_metadata_and_malformed_provider_coordinates_not_invented(self):
        result, reader, _ = self.locate(reader=FakeReader(database_type='Other-City'))
        self.assertEqual(result['geolocation']['status'], 'unavailable')
        self.assertEqual(reader.lookups, [])
        for value in (float('nan'), float('inf'), True, '10', 100, 10**1000):
            result, _, _ = self.locate(reader=FakeReader(value={'location': {'latitude': value, 'longitude': 20}}))
            self.assertTrue(all(row['status'] == 'unavailable' for row in result['hops'][1]['locations']))
            self.assertTrue(all(row['latitude'] is None for row in result['hops'][1]['locations']))
            validate_route_geolocation(result)
        bad_name = {'location': {'latitude': 10, 'longitude': 20}, 'city': {'names': {'en': '\ud800'}}}
        result, _, _ = self.locate(reader=FakeReader(value=bad_name))
        self.assertEqual(result['hops'][1]['locations'][0]['status'], 'unavailable')

    def test_maximum_256_addresses_bounded_and_deduplicated(self):
        route = {'hops': [{'hop': index + 1, 'address': f'8.8.{index}.1',
                           'addresses': [f'8.8.{index}.{number}' for number in range(1, 17)]} for index in range(16)]}
        result, reader, _ = self.locate(route)
        self.assertEqual(len(reader.lookups), 256)
        self.assertEqual(sum(len(hop['locations']) for hop in result['hops']), 256)
        validate_route_geolocation(result)
        route['hops'].append(copy.deepcopy(route['hops'][0]))
        result, _, opened = self.locate(route)
        opened.assert_not_called()
        self.assertEqual(result['geolocation']['status'], 'error')

    def test_import_validation_rejects_private_locations_bad_provenance_and_nonfinite_coordinates(self):
        result, _, _ = self.locate()
        variants = []
        private = copy.deepcopy(result)
        private['hops'][0]['locations'][0].update(status='located', latitude=10, longitude=20, city='Fabricated')
        variants.append(private)
        for field, value in (('latitude', float('nan')), ('latitude', True), ('latitude', 91),
                             ('longitude', 181), ('country_code', 'XYZ'), ('city', 'x' * 201), ('status', [])):
            bad = copy.deepcopy(result); bad['hops'][1]['locations'][0][field] = value; variants.append(bad)
        for field, value in (('status', []), ('schema_version', True), ('license', 'unknown'),
                             ('attribution_url', 'javascript:alert(1)'), ('database_type', 'Other-City'), ('build_epoch', True)):
            bad = copy.deepcopy(result); bad['geolocation'][field] = value; variants.append(bad)
        bad = copy.deepcopy(result); bad['hops'][1]['locations'].pop(); variants.append(bad)
        bad = copy.deepcopy(result); bad['hops'][1]['locations'][0]['address'] = '9.9.9.9'; variants.append(bad)
        for value in variants:
            with self.assertRaises(ValueError):
                validate_route_geolocation(value)
        legacy = route_fixture()
        self.assertEqual(validate_route_geolocation(legacy), legacy)

    def test_json_xml_project_import_bad_geo_status_rejects_good_and_bad_atomically(self):
        app = create_app({'TESTING': True, 'RUNS_DIRECTORY': str(Path(self.temp.name) / 'archive'), 'IPERF_PATH': 'missing-iperf'})
        manager, projects = app.extensions['iperf_runs'], app.extensions['iperf_projects']
        client = app.test_client()
        try:
            project = projects.create({'name': 'Synthetic geo import'})
            route, _, _ = self.locate()
            record = {'schema_version': 1, 'id': 'a' * 32, 'created_at': '2026-01-01T10:00:00+00:00',
                      'finished_at': '2026-01-01T10:00:10+00:00', 'status': 'completed',
                      'settings': settings_from({'target': '127.0.0.1', 'project_id': project['id']}),
                      'iperf_version': 'iperf 3.22', 'output_format': 'json', 'exit_code': 0, 'command': [], 'error': None,
                      'summary': {'sum_received': {'bits_per_second': 100e6}}, 'intervals': [],
                      'test_parameters': None, 'event_count': 0, 'route': route}
            manager.save(record)
            original_runs, original_projects = manager.storage.records(), projects.catalog()
            for field in ('metadata', 'location'):
                bundle = projects.export(project['id'])
                bad = copy.deepcopy(bundle['runs'][0]); bad['id'] = 'b' * 32
                if field == 'metadata':
                    bad['route']['geolocation']['status'] = []
                else:
                    bad['route']['hops'][1]['locations'][0]['status'] = {}
                bundle['runs'].append(bad)
                for xml in (False, True):
                    content = xml_export(bundle).decode() if xml else json.dumps(bundle)
                    response = client.post('/api/projects/import', json={'filename': 'geo.xml' if xml else 'geo.json', 'content': content})
                    self.assertEqual(response.status_code, 400, response.get_json())
                    self.assertEqual(manager.storage.records(), original_runs)
                    self.assertEqual(projects.catalog(), original_projects)
        finally:
            manager.shutdown()


if __name__ == '__main__':
    unittest.main()
