import unittest

from app.peer import peer_results
from app.runner import settings_from


class PeerTests(unittest.TestCase):
    def test_diagnostic_checks_never_parse_copied_native_iperf_output(self):
        for check_type in ('dns', 'loss'):
            record = {'kind': 'diagnostic-check', 'check_type': check_type,
                      'settings': {'target': 'example.test'},
                      'native_result': {'server_output_text': '[SUM] 0.00-1.00 sec 12 MBytes 100 Mbits/sec receiver'}}
            peer = peer_results(record)
            self.assertFalse(peer['available'])
            self.assertEqual(peer['metrics'], {})
            self.assertEqual(peer['series'], [])
            self.assertEqual(peer['raw_output'], '')

    def record(self, mode='upload', **native):
        return {'settings': settings_from({'target': 'remote.example', 'mode': mode}), 'native_result': native}

    def test_parallel_bidir_server_text_uses_sum_and_client_directions(self):
        text = ('[  5][RX-S] 0.00-1.00 sec 6 MBytes 50 Mbits/sec\n'
                '[  8][RX-S] 0.00-1.00 sec 6 MBytes 50 Mbits/sec\n'
                '[SUM][RX-S] 0.00-1.00 sec 12 MBytes 100 Mbits/sec\n'
                '[ 10][TX-S] 0.00-1.00 sec 12 MBytes 100 Mbits/sec 5 100 KBytes\n'
                '[ 12][TX-S] 0.00-1.00 sec 12 MBytes 100 Mbits/sec 2 100 KBytes\n'
                '[SUM][TX-S] 0.00-1.00 sec 24 MBytes 200 Mbits/sec 7\n'
                '[SUM][RX-S] 0.00-10.00 sec 120 MBytes 100 Mbits/sec receiver\n'
                '[SUM][TX-S] 0.00-10.00 sec 240 MBytes 200 Mbits/sec 7 sender\n')
        peer = peer_results(self.record('bidir', server_output_text=text))
        self.assertEqual(peer['metrics'], {'upload_mbps': 100, 'download_mbps': 200, 'download_retransmits': 7})
        self.assertEqual(peer['series'], [{'seconds': 1., 'mbps': 100., 'direction': 'upload'},
                                          {'seconds': 1., 'mbps': 200., 'direction': 'download'}])
        self.assertEqual(peer['raw_output'], text)

    def test_udp_remote_receiver_loss_and_jitter_are_retained(self):
        text = ('[  5] 0.00-1.00 sec 600 KBytes 5 Mbits/sec 0.2 ms 1/100 (1%)\n'
                '[  5] 0.00-10.00 sec 6 MBytes 5 Mbits/sec 0.5 ms 10/1000 (1%) receiver\n')
        peer = peer_results(self.record(server_output_text=text))
        self.assertEqual(peer['metrics']['upload_jitter_ms'], .5)
        self.assertEqual(peer['metrics']['upload_lost_percent'], 1)
        self.assertNotIn('upload_retransmits', peer['metrics'])

    def test_remote_json_maps_server_send_to_client_download(self):
        remote = {'start': {'test_start': {'protocol': 'TCP', 'reverse': 1}},
                  'end': {'sum_sent': {'bits_per_second': 200e6, 'retransmits': 7},
                          'sum_received': {'bits_per_second': 190e6}},
                  'intervals': [{'sum': {'end': 1, 'bits_per_second': 210e6}}]}
        peer = peer_results(self.record('download', server_output_json=remote))
        self.assertEqual(peer['metrics']['download_mbps'], 200)
        self.assertEqual(peer['metrics']['download_retransmits'], 7)
        self.assertEqual(peer['series'][0]['direction'], 'download')

    def test_udp_sender_placeholder_loss_is_not_receiver_loss(self):
        text = '[ 5] 0.00-10.00 sec 6 MBytes 5 Mbits/sec 0.0 ms 0/1000 (0%) sender'
        peer = peer_results(self.record('download', server_output_text=text))
        self.assertEqual(peer['metrics'], {'download_mbps': 5})

    def test_missing_unparseable_and_unlabelled_bidir_do_not_fabricate_results(self):
        self.assertFalse(peer_results(self.record())['available'])
        self.assertFalse(peer_results({'settings': settings_from({'role': 'server'})})['available'])
        peer = peer_results(self.record(server_output_text='Server busy; output unavailable.'))
        self.assertTrue(peer['available'])
        self.assertFalse(peer['metrics'])
        self.assertFalse(peer['series'])
        peer = peer_results(self.record('bidir', server_output_text='[ 5] 0.00-1.00 sec 12 MBytes 100 Mbits/sec'))
        self.assertFalse(peer['series'])
        peer = peer_results(self.record(server_output_json={'start': [], 'end': {'sum_received': 'broken'}}))
        self.assertFalse(peer['metrics'])


if __name__ == '__main__':
    unittest.main()
