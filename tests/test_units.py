import math
import unittest

from app.units import format_rate, format_mbps, rate_scale, run_color, PALETTE


class UnitTests(unittest.TestCase):
    def test_decimal_boundaries_zero_missing_and_large_rates(self):
        cases = [(0, '0 bps'), (999, '999 bps'), (1000, '1 kbps'), (1e6, '1 Mbps'),
                 (1e9, '1 Gbps'), (1e12, '1 Tbps'), (1.234e15, '1,234 Tbps'), (-1e9, '-1 Gbps')]
        for value, expected in cases:
            self.assertEqual(format_rate(value), expected)
        for value in (None, True, float('nan'), float('inf')):
            self.assertEqual(format_rate(value), '—')
        self.assertEqual(format_rate(.004), '4.00e-03 bps')
        self.assertEqual(format_mbps(2300), '2.3 Gbps')

    def test_shared_axis_scale_and_raw_values_are_unchanged(self):
        rates = [0, None, 10e6, 3e9, 7e6]
        scale = rate_scale(rates)
        self.assertEqual(scale, {'unit': 'Gbps', 'divisor': 1000000000})
        self.assertEqual(format_rate(10e6, scale=scale), '0.01 Gbps')
        self.assertEqual(rates, [0, None, 10e6, 3e9, 7e6])
        self.assertEqual(rate_scale([None, math.nan]), {'unit': 'bps', 'divisor': 1})

    def test_persistent_colors_take_precedence_and_import_origin_is_stable(self):
        record = {'id': '1' * 32, 'settings': {}}
        color = run_color(record)
        self.assertIn(color, PALETTE)
        self.assertEqual(run_color({'id': '2' * 32, 'settings': {}, 'imported': {'source_id': record['id']}}), color)
        record['settings']['color'] = '#ABCDEF'
        self.assertEqual(run_color(record), '#abcdef')
