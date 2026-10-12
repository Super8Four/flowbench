"""Normalize iPerf results without fabricating metrics absent from a platform."""

import math


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
            points.append({"seconds": interval["end"], "mbps": interval["bits_per_second"] / 1_000_000,
                           "direction": interval.get("direction") or settings["mode"], "stream": interval["stream"]})
    if record.get("output_format") == "text":
        has_sums = any(point.get("stream") == "SUM" for point in points)
        points = [point for point in points if not has_sums or point.get("stream") == "SUM"]
        grouped = {}
        for point in points:
            key = (point["seconds"], point["direction"])
            grouped[key] = grouped.get(key, 0) + point["mbps"]
        points = [{"seconds": key[0], "direction": key[1], "mbps": value} for key, value in grouped.items()]
    return points


def overview(record):
    """Interval peaks and measured runtime, with a bounded trend for history rows."""
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


def comparison(before, after):
    if before["id"] == after["id"]:
        raise ValueError("Choose two different completed tests.")
    if before["status"] != "completed" or after["status"] != "completed":
        raise ValueError("Only completed tests can be compared.")
    left, right = measurements(before), measurements(after)
    if not left or not right:
        raise ValueError("Both tests must contain performance measurements.")
    settings_left, settings_right = effective_settings(before), effective_settings(after)
    differences = [key for key in ("role", "target", "port", "bind", "protocol", "mode", "duration", "streams", "ip_version")
                   if settings_left[key] != settings_right[key]]
    if settings_left["protocol"] == "udp" and settings_left["bandwidth"] != settings_right["bandwidth"]:
        differences.append("bandwidth")
    if before["iperf_version"] != after["iperf_version"]:
        differences.append("iperf_version")
    rows = []
    for key in sorted(left.keys() & right.keys()):
        baseline, current = left[key], right[key]
        delta = current - baseline
        # A zero baseline has no defined percentage change.
        percent = delta / baseline * 100 if baseline else None
        better = delta > 0 if key.endswith(("mbps", "_packets")) and not key.endswith("lost_packets") else delta < 0
        rows.append({"metric": key, "before": baseline, "after": current, "delta": delta,
                     "percent_change": percent, "direction": "unchanged" if delta == 0 else "improved" if better else "worse"})
    if not rows:
        raise ValueError("These tests have no comparable metrics in common.")
    return {"schema_version": 1, "before_id": before["id"], "after_id": after["id"],
            "settings_differences": differences, "metrics": rows,
            "interpretation": "A test comparison records a measured change. Repeat tests under matching conditions to establish an improvement."}
