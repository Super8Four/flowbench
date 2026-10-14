"""Remote iPerf output, kept separate and expressed from the client's perspective."""

import json
import math
import re

from .metrics import effective_settings, measurements, series


def peer_results(record):
    if record.get("kind") == "diagnostic-check":
        return {"available": False, "format": None, "metrics": {}, "series": [],
                "raw_output": "", "note": "Diagnostic checks retain their typed DNS or ICMP evidence; no remote iPerf output applies."}
    native = record.get("native_result") or {}
    if not isinstance(native, dict):
        native = {}
    json_data = native.get("server_output_json") or record.get("server_output_json")
    text = native.get("server_output_text") or record.get("server_output_text") or ""
    if isinstance(json_data, str):
        try:
            json_data = json.loads(json_data)
        except json.JSONDecodeError:
            json_data = None
    result = {"available": False, "format": None, "metrics": {}, "series": [],
              "raw_output": "", "note": "Remote output is returned after a client test finishes, when the server supplies it."}
    if record["settings"]["role"] != "client":
        result["note"] = "This session ran as the server. Its server measurements are already shown as local output."
        return result
    if isinstance(json_data, dict):
        start = json_data.get("start")
        start = start if isinstance(start, dict) else {}
        remote = {"settings": {**effective_settings(record), "role": "server"},
                  "test_parameters": start.get("test_start"),
                  "summary": json_data.get("end") or {}, "intervals": json_data.get("intervals") or []}
        try:
            metrics, points = measurements(remote), series(remote)
            if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in metrics.values()):
                raise ValueError("Invalid remote metrics.")
            if any(not isinstance(p[k], (int, float)) or not math.isfinite(p[k]) for p in points for k in ("seconds", "mbps")):
                raise ValueError("Invalid remote intervals.")
            result.update(available=True, format="json", metrics=metrics, series=points,
                          raw_output=json.dumps(json_data, indent=2, ensure_ascii=False))
        except (TypeError, KeyError, AttributeError, ValueError):
            result.update(available=True, format="json", raw_output=json.dumps(json_data, indent=2, ensure_ascii=False))
    elif isinstance(text, str) and text:
        settings = effective_settings(record)
        metrics, intervals, summaries = {}, [], []
        pattern = re.compile(r"\[\s*(?P<stream>\d+|SUM)\s*\](?:\[(?P<role>RX-S|TX-S)\])?\s*"
                             r"(?P<start>\d+(?:\.\d+)?)-\s*(?P<end>\d+(?:\.\d+)?)\s+sec.*?"
                             r"(?P<rate>\d+(?:\.\d+)?)\s+(?P<unit>[KMGT]?)bits/sec(?P<tail>.*)")
        for line in text.splitlines():
            match = pattern.search(line)
            if not match:
                continue
            direction = ("upload" if match["role"] == "RX-S" else "download" if match["role"] == "TX-S"
                         else settings["mode"] if settings["mode"] != "bidir" else None)
            if not direction:
                continue  # Unlabelled bidirectional output cannot be assigned reliably.
            rate = float(match["rate"]) * 1000 ** (" KMGT".index(match["unit"]) if match["unit"] else 0)
            row = {"seconds": float(match["end"]), "mbps": rate / 1e6, "direction": direction,
                   "stream": match["stream"]}
            tail = match["tail"]
            if "sender" in tail or "receiver" in tail:
                row["reporter"] = "receiver" if "receiver" in tail else "sender"
                udp = re.search(r"(\d+(?:\.\d+)?)\s+ms\s+(\d+)\s*/\s*(\d+)\s+\((\d+(?:\.\d+)?)%\)", tail)
                if udp and row["reporter"] == "receiver":
                    row.update(jitter_ms=float(udp[1]), lost_packets=int(udp[2]), packets=int(udp[3]), lost_percent=float(udp[4]))
                elif not udp and direction == "download" and row["reporter"] == "sender":
                    retransmits = re.match(r"\s+(\d+)\s+sender", tail)
                    if retransmits:
                        row["retransmits"] = int(retransmits[1])
                summaries.append(row)
            else:
                intervals.append(row)
        points = []
        for direction in ("upload", "download"):
            rows = [r for r in intervals if r["direction"] == direction]
            sums = [r for r in rows if r["stream"] == "SUM"]
            rows = sums or rows
            grouped = {}
            for row in rows:
                grouped[row["seconds"]] = grouped.get(row["seconds"], 0) + row["mbps"]
            points.extend({"seconds": seconds, "mbps": mbps, "direction": direction} for seconds, mbps in sorted(grouped.items()))
            rows = [r for r in summaries if r["direction"] == direction]
            # The server is receiver on uploads, sender on downloads.
            reporter = "receiver" if direction == "upload" else "sender"
            rows = [r for r in rows if r["reporter"] == reporter] or rows
            sums = [r for r in rows if r["stream"] == "SUM"]
            rows = sums[-1:] or rows
            if rows:
                metrics[direction + "_mbps"] = sum(r["mbps"] for r in rows)
                for key in ("retransmits", "lost_packets", "packets"):
                    if all(key in r for r in rows):
                        metrics[direction + "_" + key] = sum(r[key] for r in rows)
                # Aggregate jitter/loss percentages only when the server supplied
                # a single aggregate (or a single stream), never average streams.
                if len(rows) == 1:
                    for key in ("jitter_ms", "lost_percent"):
                        if key in rows[0]:
                            metrics[direction + "_" + key] = rows[0][key]
        result.update(available=True, format="text", metrics=metrics, series=points, raw_output=text)
    if result["available"]:
        result["note"] = "Remote intervals and local intervals start at their own test clocks. A difference alone does not prove packet loss. Server text output has rounded values."
        if not result["metrics"] and not result["series"]:
            result["note"] += " This server output has no recognizable measurements; its raw output is retained."
    return result
