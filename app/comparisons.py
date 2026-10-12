"""Shared elapsed windows without stretching, filling gaps or rescaling evidence."""

import math

MAX_COMPARISON_BYTES = 32 * 1024 * 1024


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def common_window(records):
    """Whole-run summaries stay whole-run; exact bins can add window throughput.

    A weighted common-window throughput is available only when captured bins
    completely cover [0, end] without gaps/overlaps and end falls on a captured
    bin boundary. We never divide a partially intersecting bin or infer starts.
    """
    from .metrics import series

    evidence = []
    for record in records:
        points = [row for row in series(record) if finite(row.get("seconds")) and row["seconds"] >= 0
                  and finite(row.get("mbps")) and row["mbps"] >= 0]
        measured_end = max((row["seconds"] for row in points), default=None)
        if record.get("kind") == "flent-run":
            native_times = [row["seconds"] for native in (record.get("flent") or {}).get("series", [])
                            for row in native.get("points", []) if finite(row.get("seconds")) and row["seconds"] >= 0
                            and finite(row.get("value"))]
            measured_end = max(native_times, default=None)
        evidence.append((record, points, measured_end))
    ends = [end for _, _, end in evidence]
    end = min(ends) if ends and all(value is not None for value in ends) else None
    runs = []
    for record, points, measured_end in evidence:
        directions = []
        for direction in sorted({row["direction"] for row in points}):
            selected = sorted((row for row in points if row["direction"] == direction), key=lambda row: row["seconds"])
            result = {"direction": direction, "status": "unavailable", "mean_mbps": None}
            if end is None or end <= 0:
                result["reason"] = "A positive measured elapsed window is unavailable for one or more tests."
            elif any(not finite(row.get("start")) or row["start"] < 0 or row["start"] >= row["seconds"] for row in selected):
                result["reason"] = "Exact interval starts are unavailable or ambiguous; end-only samples cannot establish window coverage."
            else:
                cursor, weighted, reason = 0.0, 0.0, None
                for row in selected:
                    if row["start"] >= end:
                        break
                    if not math.isclose(row["start"], cursor, abs_tol=1e-6):
                        reason = "Missing or overlapping intervals prevent a complete common-window mean."
                        break
                    if row["seconds"] > end + 1e-6:
                        reason = "The common window cuts a captured interval; its partial throughput is unknown."
                        break
                    weighted += row["mbps"] * ((row["seconds"] - row["start"]) / end)
                    cursor = row["seconds"]
                if not reason and not math.isclose(cursor, end, abs_tol=1e-6):
                    reason = "Missing intervals prevent complete common-window coverage."
                if not reason and not finite(weighted):
                    reason = "The common-window rate exceeds the supported numeric range."
                if reason:
                    result["reason"] = reason
                else:
                    result.update(status="complete", mean_mbps=weighted,
                                  reason="Duration-weighted throughput from fully captured intervals in this window.")
            directions.append(result)
        runs.append({"id": record["id"], "measured_end_seconds": measured_end,
                     "directions": directions,
                     "note": ("Flent native points have elapsed times but cannot establish exact iPerf throughput interval coverage."
                              if record.get("kind") == "flent-run" else
                              "No captured interval series." if not points else "Only actual elapsed measurements are used.")})
    return {"start_seconds": 0, "end_seconds": end, "seconds": end,
            "aggregate_scope": "whole_run", "runs": runs,
            "note": "Comparison metrics are whole-run aggregates, including each test's full measured duration. The common elapsed window is [0, the shortest captured series end]; it does not imply uninterrupted coverage. Separate window throughput is available only for exact, complete interval bins. Longer tests continue beyond that window; missing spans are not zero values."}
