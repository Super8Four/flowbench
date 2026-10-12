"""Normalize iPerf results without fabricating metrics absent from a platform."""

import math
import re


def valid_direction(value):
    return value is None or isinstance(value, str) and 0 < len(value) <= 200


def valid_stream(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 <= value <= 2_147_483_647
    if not isinstance(value, str) or not 0 < len(value) <= 200:
        return False
    if re.fullmatch(r"-\d+", value):
        return False
    if re.fullmatch(r"\d+", value):
        return int(value) <= 2_147_483_647
    return True  # SUM and legitimate custom text labels are retained verbatim.


def identity_issues(record):
    """Diagnose imported/legacy identity shapes without changing raw evidence."""
    problems = set()
    def walk(value, depth=0):
        if depth > 60:
            problems.add("Stored measurement nesting exceeds the supported limit.")
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "direction" and not valid_direction(child):
                    problems.add("Direction must be optional text up to 200 characters.")
                if key == "stream" and not valid_stream(child):
                    problems.add("Stream must be bounded text or an integer identifier from 0 to 2147483647.")
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)
    for key in ("intervals", "summary", "test_parameters"):
        walk(record.get(key))
    return sorted(problems)


def effective_settings(record):
    result = dict(record["settings"])
    result.setdefault("ip_version", "auto")
    params = record.get("test_parameters") or {}
    if params:
        result["protocol"] = params.get("protocol", result["protocol"]).lower()
        result["mode"] = "bidir" if params.get("bidir") else "download" if params.get("reverse") else "upload"
        result["duration"] = params.get("duration", result["duration"])
        result["streams"] = params.get("num_streams", result["streams"])
        result["bandwidth"] = params.get("target_bitrate", result["bandwidth"])
    return result


def measurements(record):
    if record.get("kind") == "flent-run":
        result = {}
        for row in (record.get("flent") or {}).get("summaries", []):
            mean = row.get("mean")
            if isinstance(mean, bool) or not isinstance(mean, (int, float)) or not math.isfinite(mean):
                continue
            # Preserve each actual native series identity and unit. These means
            # never stand in for an iPerf upload/download measurement.
            suffix = "_mbps" if row.get("unit") == "Mbits/s" else "_rtt_ms" if row.get("unit") == "ms" else None
            if suffix:
                result["flent_" + row["name"] + suffix] = mean
        return result
    if identity_issues({"summary": record.get("summary")}):
        return {}  # Invalid stream identity cannot establish aggregation ownership.
    summary = record.get("summary") or {}
    settings = effective_settings(record)
    result = {}
    directions = [("download" if settings["mode"] == "download" else "upload", "")]
    if settings["mode"] == "bidir":
        directions = [("upload", ""), ("download", "_bidir_reverse")]
    for direction, suffix in directions:
        receiver = summary.get("sum_received" + suffix) or summary.get("sum" + suffix) or {}
        sender = summary.get("sum_sent" + suffix) or {}
        server_sender = settings["role"] == "server" and direction == "download"
        rate = sender.get("bits_per_second") if server_sender else receiver.get("bits_per_second", sender.get("bits_per_second"))
        if rate is not None:
            result[direction + "_mbps"] = rate / 1_000_000
        for key in ("jitter_ms", "lost_percent", "lost_packets", "packets"):
            if key in receiver and not server_sender:
                result[direction + "_" + key] = receiver[key]
        if "retransmits" in sender:
            result[direction + "_retransmits"] = sender["retransmits"]
    text_summary = summary.get("text_summaries", {})
    for direction, _ in directions:
        entries = text_summary.get(direction + "_receiver") or text_summary.get(direction + "_sender") or []
        if settings["mode"] != "bidir":
            entries = entries or text_summary.get("receiver") or text_summary.get("sender") or []
        if entries:
            sums = [entry for entry in entries if entry["stream"] == "SUM"]
            rate = sums[-1]["bits_per_second"] if sums else sum(entry["bits_per_second"] for entry in entries)
            result[direction + "_mbps"] = rate / 1_000_000
    return result


def series(record):
    if identity_issues(record):
        return []  # Raw evidence remains in the record; overview explains why.
    settings = effective_settings(record)
    points = []
    # Streaming JSON aggregates are preferred to avoid counting parallel streams twice.
    for interval in record.get("intervals", []):
        if "sum" in interval:
            for key in ("sum", "sum_bidir_reverse"):
                value = interval.get(key)
                if not value or value.get("omitted") or "bits_per_second" not in value:
                    continue
                direction = "download" if key.endswith("reverse") or settings["mode"] == "download" else "upload"
                point = {"seconds": value["end"], "mbps": value["bits_per_second"] / 1_000_000,
                         "direction": direction}
                if isinstance(value.get("start"), (int, float)) and not isinstance(value["start"], bool):
                    point["start"] = value["start"]
                points.append(point)
        elif "bits_per_second" in interval:
            point = {"seconds": interval["end"], "mbps": interval["bits_per_second"] / 1_000_000,
                     "direction": interval.get("direction") or settings["mode"], "stream": interval["stream"]}
            if isinstance(interval.get("start"), (int, float)) and not isinstance(interval["start"], bool):
                point["start"] = interval["start"]
            points.append(point)
    if record.get("output_format") == "text":
        has_sums = any(point.get("stream") == "SUM" for point in points)
        points = [point for point in points if not has_sums or point.get("stream") == "SUM"]
        grouped, starts = {}, {}
        for point in points:
            key = (point["seconds"], point["direction"])
            grouped[key] = grouped.get(key, 0) + point["mbps"]
            starts.setdefault(key, []).append(point.get("start"))
        points = [{"seconds": key[0], "direction": key[1], "mbps": value,
                   **({"start": starts[key][0]} if starts[key][0] is not None and all(start == starts[key][0] for start in starts[key]) else {})}
                  for key, value in grouped.items()]
    return points


def overview(record):
    """Interval peaks and measured runtime, with a bounded trend for history rows."""
    issues = identity_issues(record)
    if issues:
        return {"runtime_seconds": None, "trend": [], "status": "unavailable", "issues": issues,
                "note": "Stored measurement identities are invalid. Derived interval charts are unavailable; raw captured evidence is preserved."}
    def valid(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0

    points = [point for point in series(record) if point["direction"] in {"upload", "download"}
              and valid(point["seconds"]) and valid(point["mbps"])]
    result = {"runtime_seconds": None, "trend": []}
    ends = [point["seconds"] for point in points]
    summary = record.get("summary") or {}
    # A summary-only import can report runtime, but cannot establish interval peaks.
    for key in ("sum", "sum_sent", "sum_received", "sum_bidir_reverse",
                "sum_sent_bidir_reverse", "sum_received_bidir_reverse"):
        value = summary.get(key) or {}
        end = value.get("end", value.get("seconds"))
        if valid(end):
            ends.append(end)
    for entries in summary.get("text_summaries", {}).values():
        ends.extend(entry["end"] for entry in entries if valid(entry.get("end")))
    if ends:
        result["runtime_seconds"] = max(ends)
    for direction in ("upload", "download"):
        values = sorted((point for point in points if point["direction"] == direction), key=lambda point: point["seconds"])
        if not values:
            continue
        result["max_" + direction + "_mbps"] = max(point["mbps"] for point in values)
        if len(values) > 60:
            # Keep endpoints and each bucket's extrema so short peaks remain visible.
            middle = values[1:-1]
            reduced = [values[0]]
            for index in range(29):
                bucket = middle[index * len(middle) // 29:(index + 1) * len(middle) // 29]
                extrema = {min(range(len(bucket)), key=lambda i: bucket[i]["mbps"]),
                           max(range(len(bucket)), key=lambda i: bucket[i]["mbps"])}
                reduced.extend(bucket[i] for i in sorted(extrema))
            values = reduced + [values[-1]]
        result["trend"].extend({key: point[key] for key in ("seconds", "mbps", "direction")} for point in values)
    return result


def settings_differences(before, after):
    """Differences in measured or requested conditions; no causal inference."""
    left, right = effective_settings(before), effective_settings(after)
    differences = [key for key in ("role", "target", "port", "bind", "protocol", "mode", "duration", "streams", "ip_version")
                   if left[key] != right[key]]
    if "udp" in {left["protocol"], right["protocol"]} and left["bandwidth"] != right["bandwidth"]:
        differences.append("bandwidth")
    if before.get("iperf_version") != after.get("iperf_version"):
        differences.append("iperf_version")
    if (before.get("kind") == "flent-run") != (after.get("kind") == "flent-run"):
        differences.append("tool")
    if left.get("flent_workload") != right.get("flent_workload"):
        differences.append("flent_workload")
    if (before.get("flent") or {}).get("version") != (after.get("flent") or {}).get("version"):
        differences.append("flent_version")
    return differences


def live_comparison(baseline, active):
    if baseline["id"] == active["id"]:
        raise ValueError("Choose a saved baseline different from the displayed test.")
    if baseline["status"] != "completed" or not measurements(baseline):
        raise ValueError("The baseline must be a completed test with measurements.")
    if baseline.get("kind") in {"protocol-job", "matrix-job"} or active.get("kind") in {"protocol-job", "matrix-job"}:
        raise ValueError("Choose an individual protocol or matrix test for the live overlay.")
    if baseline.get("kind") == "flent-run" or active.get("kind") == "flent-run":
        raise ValueError("The live overlay uses iPerf intervals. Inspect Flent's separately labeled native series.")
    def row(record):
        return {"id": record["id"], "status": record["status"], "settings": record["settings"],
                "effective_settings": effective_settings(record), "series": series(record),
                "metrics": measurements(record), "concurrency": record.get("concurrency")}
    return {"schema_version": 1, "kind": "live-comparison", "baseline_id": baseline["id"],
            "active_id": active["id"], "runs": [row(baseline), row(active)],
            "settings_differences": settings_differences(baseline, active),
            "interpretation": "Elapsed seconds start at each test's traffic start. Only measured intervals are shown. Matching settings do not establish identical conditions; overlapping traffic may affect results. ICMP RTT is separate from throughput."}


def comparison(before, after):
    if before["id"] == after["id"]:
        raise ValueError("Choose two different completed tests.")
    if before["status"] != "completed" or after["status"] != "completed":
        raise ValueError("Only completed tests can be compared.")
    left, right = measurements(before), measurements(after)
    if not left or not right:
        raise ValueError("Both tests must contain performance measurements.")
    differences = settings_differences(before, after)
    rows = []
    for key in sorted(left.keys() & right.keys()):
        baseline, current = left[key], right[key]
        delta = current - baseline
        # A zero baseline has no defined percentage change.
        percent = delta / baseline * 100 if baseline else None
        volume = key.endswith("_packets") and not key.endswith("lost_packets")
        better = delta > 0 if key.endswith("mbps") else delta < 0
        rows.append({"metric": key, "before": baseline, "after": current, "delta": delta,
                     "percent_change": percent, "direction": "unchanged" if delta == 0 else "volume change" if volume else "improved" if better else "worse"})
    if not rows:
        raise ValueError("These tests have no comparable metrics in common.")
    from .comparisons import common_window
    window = common_window([before, after])
    return {"schema_version": 1, "before_id": before["id"], "after_id": after["id"],
            "settings_differences": differences, "metrics": rows,
            "aggregate_scope": "whole_run", "common_window_seconds": window["seconds"], "common_window": window,
            "interpretation": "A test comparison records a measured change. Repeat tests under matching conditions to establish an improvement."}
