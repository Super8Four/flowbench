"""Presentation-only decimal SI rates and persistent run colors.

Captured bits_per_second and *_mbps values are never replaced by display text.
"""
import math
import re

RATE_UNITS = (("bps", 1), ("kbps", 1000), ("Mbps", 1000 ** 2), ("Gbps", 1000 ** 3), ("Tbps", 1000 ** 4))
PALETTE = ("#6db5ff", "#ffc857", "#59c9a5", "#ef7f98", "#b79cff", "#ffa76b", "#66d1df", "#c4d77b")
RATE_CONTRACT = {"native_rate": "bits_per_second", "derived_rate": "*_mbps uses 1000000 bits/second",
                 "display": "decimal SI bps/kbps/Mbps/Gbps/Tbps", "bytes": "byte counts are not bit rates"}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def rate_scale(values):
    maximum = max((abs(value) for value in values if finite(value)), default=0)
    unit, divisor = RATE_UNITS[0]
    for candidate, threshold in RATE_UNITS[1:]:
        if maximum < threshold:
            break
        unit, divisor = candidate, threshold
    return {"unit": unit, "divisor": divisor}


def format_rate(bits_per_second, digits=2, scale=None):
    if not finite(bits_per_second):
        return "—"
    if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 6:
        raise ValueError("Rate precision must be from 0 to 6 decimal places.")
    scale = scale or rate_scale([bits_per_second])
    value = bits_per_second / scale["divisor"]
    if value and (abs(value) < 10 ** -digits or abs(value) >= 1e12):
        text = format(value, f".{digits}e")
    else:
        text = format(value, f",.{digits}f").rstrip("0").rstrip(".") if digits else format(value, ",.0f")
    return f"{text} {scale['unit']}"


def format_mbps(value, digits=2, scale=None):
    return format_rate(value * 1_000_000, digits, scale) if finite(value) else "—"


def run_color(record):
    color = record.get("settings", {}).get("color") or record.get("color")
    if isinstance(color, str) and re.fullmatch(r"#[a-fA-F0-9]{6}", color):
        return color.lower()
    source_id = (record.get("imported") or {}).get("source_id")
    identifier = source_id if isinstance(source_id, str) and re.fullmatch(r"[a-f0-9]{32}", source_id) else record.get("id", "")
    return PALETTE[int(identifier[:8], 16) % len(PALETTE)] if isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{32}", identifier) else PALETTE[0]
