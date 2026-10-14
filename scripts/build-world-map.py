"""Reproduce the bundled public-domain world SVG from a verified local GeoJSON.

No network access. Download the pinned source documented in licenses/natural-earth.txt
separately, then run: python scripts/build-world-map.py /path/ne_110m_land.geojson
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

SOURCE_SHA256 = "9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9"


def build(source):
    payload = source.read_bytes()
    if hashlib.sha256(payload).hexdigest() != SOURCE_SHA256:
        raise ValueError("Natural Earth source checksum differs from the pinned source.")
    data = json.loads(payload)
    paths = []
    points = 0
    for feature in data['features']:
        geometry = feature['geometry']
        polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
        if geometry['type'] not in {'Polygon', 'MultiPolygon'}:
            raise ValueError('Only land polygons are supported.')
        for polygon in polygons:
            rings = []
            for ring in polygon:
                projected = []
                for longitude, latitude, *_ in ring:
                    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (longitude, latitude)):
                        raise ValueError('Nonfinite map coordinate.')
                    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                        raise ValueError('Map coordinate outside world bounds.')
                    points += 1
                    if points > 100000:
                        raise ValueError('Map exceeds the point budget.')
                    projected.append(f'{(longitude + 180) * 2.5:.2f},{(90 - latitude) * 2.5:.2f}')
                rings.append('M' + 'L'.join(projected) + 'Z')
            paths.append('<path d="' + ''.join(rings) + '"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 450" role="img" '
            'aria-labelledby="world-title world-desc">\n'
            '<title id="world-title">World land outline</title>\n'
            '<desc id="world-desc">Public-domain Natural Earth land at 1 to 110 million scale. '
            'Equirectangular projection; no router locations or route paths are inferred.</desc>\n'
            '<g fill="#293840" stroke="#52616a" stroke-width="0.35" fill-rule="evenodd">\n' +
            '\n'.join(paths) + '\n</g>\n</svg>\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'app/static/maps/world-land.svg')
    args = parser.parse_args()
    svg = build(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding='utf-8', newline='\n')
    print('Generated world land SVG:', hashlib.sha256(svg.encode()).hexdigest())


if __name__ == '__main__':
    main()
