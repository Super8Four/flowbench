"""Display-only iPerf styles, mirrored in static/chart-styles.js with parity tests."""
from threading import Lock

_PALETTE_LOCK = Lock()

COLORS = {"local": {"upload": "#42d392", "download": "#ffd43b"},
          "remote": {"upload": "#a3e6bd", "download": "#cfae36"}}


def series_style(source="local", direction="upload", color=None):
    dash = ("8 3 2 3" if direction == "download" else "2 4") if source == "remote" else "7 5" if direction == "download" else ""
    label = ("Remote server report" if source == "remote" else "Local report") + " · " + {
        "upload": "Upload (client to server)", "download": "Download (server to client)",
        "bidir": "Bidirectional aggregate"}.get(direction, "Other direction")
    return {"color": color or COLORS.get(source, {}).get(direction, "#b79cff"), "dash": dash,
            "label": label, "pattern": {"": "solid", "7 5": "dashed", "2 4": "dotted", "8 3 2 3": "dash-dot"}[dash]}


def contrast(color, background="#212629"):
    def luminance(hex_color):
        values = [int(hex_color[n:n+2], 16) / 255 for n in (1, 3, 5)]
        return sum((v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4) * weight
                   for v, weight in zip(values, (.2126, .7152, .0722)))
    a, b = luminance(color), luminance(background)
    return (max(a, b) + .05) / (min(a, b) + .05)


def _comparison_colors(count):
    # Cache the deterministic prefix, matching the browser's incremental generation.
    while len(_generated) < count:
        if len(_generated) < 128:
            last = [int(_generated[-1][n:n+2], 16) for n in (1, 3, 5)]
            best = None
            for candidate in _candidates:
                candidate[2] = min(candidate[2], sum((v-last[i])**2 * (.3, .59, .11)[i] for i, v in enumerate(candidate[1])))
                if candidate[0] not in _used and (best is None or candidate[2] > best[2]):
                    best = candidate
            if best:
                _generated.append(best[0])
                _used.add(best[0])
                continue
        global _cursor
        _cursor += 1
        color = f"#{(_cursor * 2654435761) % 16777216:06x}"
        if color not in _used and contrast(color) >= 4.5:
            _generated.append(color)
            _used.add(color)
    return _generated[:count]


_candidates = []
for _r in range(64, 245, 30):
    for _g in range(64, 245, 30):
        for _b in range(64, 245, 30):
            _color = f"#{_r:02x}{_g:02x}{_b:02x}"
            if contrast(_color) >= 4.5:
                _candidates.append([_color, [_r, _g, _b], float("inf")])
_generated, _used, _cursor = ["#6db5ff"], {"#6db5ff"}, 0


def comparison_colors(count):
    with _PALETTE_LOCK:
        return _comparison_colors(count)


def comparison_styles(runs):
    ids = sorted({run["id"] for run in runs})
    palette = comparison_colors(len(ids) * 4)
    return {(identifier, source, direction): series_style(source, direction, palette[index*4+s*2+d])
            for index, identifier in enumerate(ids) for s, source in enumerate(("local", "remote"))
            for d, direction in enumerate(("upload", "download"))}
