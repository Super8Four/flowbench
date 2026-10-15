"""Display policy and actual chart regression checks; synthetic evidence only."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from PIL import Image, ImageColor
from app.chart_styles import comparison_colors, comparison_styles, contrast, series_style
from app.peer import peer_results
from app.reports import _chart, _charts
from app.runner import settings_from


class ChartColorsTests(unittest.TestCase):
    def test_session_shades_patterns_and_client_perspective(self):
        styles = [series_style(s, d) for s in ('local', 'remote') for d in ('upload', 'download')]
        self.assertEqual(len({s['color'] for s in styles}), 4)
        self.assertEqual(len({s['dash'] for s in styles}), 4)
        self.assertTrue(all(contrast(s['color']) >= 4.5 for s in styles))
        peer = peer_results({'settings': settings_from({'target': 'synthetic.example', 'mode': 'bidir'}),
                            'native_result': {'server_output_text': '[SUM][RX-S] 0.00-1.00 sec 12 MBytes 100 Mbits/sec\n[SUM][TX-S] 0.00-1.00 sec 24 MBytes 200 Mbits/sec'}})
        for point, direction in zip(peer['series'], ('upload', 'download')):
            self.assertEqual(point['direction'], direction)
            self.assertEqual(series_style('remote', point['direction'])['color'], styles[2 if direction == 'upload' else 3]['color'])
        self.assertIn('client to server', series_style('remote', 'upload')['label'])
        self.assertIn('server to client', series_style('remote', 'download')['label'])

    def test_large_palette_stable_reorder_and_concurrent_reports(self):
        runs = [{'id': f'{i:032x}'} for i in range(40)]  # Same first eight hex digits.
        self.assertEqual(comparison_styles(runs), comparison_styles(list(reversed(runs))))
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(comparison_colors, [800] * 16))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(set(results[0])), 800)
        self.assertTrue(all(contrast(color) >= 4.5 for color in results[0]))

    def test_report_legend_pixels_and_missing_spans_use_same_policy(self):
        runs = [{'id': f'{i:032x}', 'display_index': i+1, 'label': f'Test {i+1}', 'color':'#123456',
                 'series':[{'start':0,'seconds':1,'mbps':100,'direction':'upload'},
                           {'start':3,'seconds':4,'mbps':120,'direction':'upload'},
                           {'start':0,'seconds':1,'mbps':80,'direction':'download'}]} for i in range(12)]
        charts = _charts(runs)
        styles = comparison_styles(runs)
        self.assertEqual(len(charts),2)
        self.assertEqual(charts[1][3][0][1], styles[(runs[10]['id'],'local','download')]['color'])
        self.assertIn('Local report', charts[0][3][0][2])
        image = Image.open(BytesIO(_chart(runs[:1])))
        pixels = set(image.getdata())
        for direction in ('upload','download'):
            color=ImageColor.getrgb(series_style('local',direction)['color'])
            self.assertIn(color,pixels)
            self.assertTrue(all(image.getpixel((782,y)) != color for y in range(80,460)))
        self.assertEqual(runs[0]['color'],'#123456')
        unknown = dict(runs[0], series=[{'seconds':1,'mbps':20,'direction':'custom'}])
        self.assertTrue(_charts([unknown])[0][3][0][2].endswith('/ points'))
        self.assertEqual(_charts([runs[0]], styles)[0][3][0][1], styles[(runs[0]['id'],'local','download')]['color'])

    @unittest.skipUnless(shutil.which('node'), 'Node absent; source CI runs the isolated JavaScript fixture.')
    def test_browser_renderer_and_cross_language_palette_parity(self):
        root = Path(__file__).resolve().parents[1]
        command = [shutil.which('node'),str(root/'tests/chart-colors-behavior.mjs'),str(root/'app/static')]
        result = subprocess.run(command,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result = subprocess.run(command+['--palette'],text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),comparison_colors(800))
