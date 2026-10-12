"""Bounded, sequential test matrices; all validation precedes archive writes."""

from decimal import Decimal
from itertools import product
import re


MAX_CHILDREN = 12
MAX_DURATION = 60
MAX_PLANNED_SECONDS = 360
MAX_STREAMS = 8
MAX_UDP_BITS_PER_SECOND = 100_000_000


def choices(value, field, allowed):
    if not isinstance(value, list) or not value or len(value) > len(allowed):
        raise ValueError(f"{field} must be a nonempty list of distinct supported values.")
    if any(not isinstance(item, str) or item not in allowed for item in value) or len(set(value)) != len(value):
        raise ValueError(f"Invalid or duplicate {field}.")
    return value


def matrix_plan(data, normalize):
    if not isinstance(data, dict) or not isinstance(data.get("settings"), dict):
        raise ValueError("Send matrix settings, protocols, modes and streams as JSON.")
    protocols = choices(data.get("protocols"), "protocols", {"tcp", "udp"})
    modes = choices(data.get("modes"), "modes", {"upload", "download", "bidir"})
    streams = data.get("streams")
    if not isinstance(streams, list) or not streams or len(streams) > MAX_STREAMS:
        raise ValueError("streams must be a nonempty list of distinct whole numbers from 1 to 8.")
    if any(isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_STREAMS for value in streams) or len(set(streams)) != len(streams):
        raise ValueError("streams must contain distinct whole numbers from 1 to 8.")
    settings = normalize(dict(data["settings"], protocol=protocols[0], mode=modes[0], streams=streams[0]))
    if settings["role"] != "client":
        raise ValueError("Matrices are outgoing client tests only.")
    count = len(protocols) * len(modes) * len(streams)
    if count > MAX_CHILDREN:
        raise ValueError("A matrix is limited to 12 sequential tests.")
    if settings["duration"] > MAX_DURATION:
        raise ValueError("Matrix tests are limited to 60 seconds each.")
    planned = count * settings["duration"]
    if planned > MAX_PLANNED_SECONDS:
        raise ValueError("Matrix planned traffic time is limited to 360 seconds.")
    children = [dict(settings, protocol=protocol, mode=mode, streams=stream)
                for protocol, mode, stream in product(protocols, modes, streams)]
    for child in children:
        if child["protocol"] == "udp":
            # iPerf applies -b per stream and independently in each direction.
            bandwidth = child["bandwidth"]
            match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMG]?)", bandwidth, re.I)
            rate = Decimal(match[1]) * (1000 ** " KMG".index(match[2].upper()))
            aggregate = rate * child["streams"] * (2 if child["mode"] == "bidir" else 1)
            if not 0 < aggregate <= MAX_UDP_BITS_PER_SECOND:
                raise ValueError("Matrix UDP bandwidth must be finite and aggregate across streams/directions at most 100M bits/sec.")
    return settings, children, {"protocols": protocols, "modes": modes, "streams": streams,
                                "planned_seconds": planned, "max_udp_bits_per_second": MAX_UDP_BITS_PER_SECOND}
