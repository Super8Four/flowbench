"""Canonical synthetic check evidence; no process or network execution."""

from app.checks import initial_result, prepare_check, summarize_check, check_run_status, validate_check_record
from app.time_quality import time_quality_result

START = "2026-01-01T10:00:00+00:00"
END = "2026-01-01T10:00:01+00:00"


def check_record(kind="dns", run_id="a" * 32, outcome="answer", count=1, result_status=None):
    if kind == "loss" and outcome == "answer":
        outcome = "reply"
    _, settings = prepare_check({"check_type": kind, "target": "check.invalid" if kind == "dns" else "127.0.0.1", "count": count})
    result = initial_result(kind, settings)
    result.update(started_at=START, finished_at=END, elapsed_seconds=1.0)
    result["time_quality"] = time_quality_result("unavailable", note="Synthetic clock unavailable.")
    if kind == "dns":
        result["resolver"] = {"mode": "system", "configured_addresses": ["127.0.0.1"], "selected_address": "127.0.0.1", "port": 53, "address_family": 4}
        response = outcome in {"answer", "nodata", "nxdomain", "dns_error"}
        result["attempts"] = [{"sequence": index, "query_index": index, "started_at": START, "finished_at": END,
            "duration_ms": 1.0, "transport": "udp", "resolver_address": "127.0.0.1", "resolver_port": 53,
            "resolver_family": 4, "status": outcome, "rcode": ("NXDOMAIN" if outcome == "nxdomain" else "SERVFAIL" if outcome == "dns_error" else "NOERROR") if response else None,
            "flags": ["QR", "RD", "RA"] if response else [], "question": {"name": settings["target"], "type": "A", "class": "IN"},
            "answers": [{"name": settings["target"], "type": "A", "class": "IN", "ttl": 37, "rdata": "203.0.113.7"}] if outcome == "answer" else [],
            "authority": [], "additional": [], "records_truncated": False, "raw_output": "synthetic <DNS>\n", "output_truncated": False, "error": None} for index in range(1, count + 1)]
        result["status"] = result_status or ("captured" if response else "timeout")
    else:
        result["resolution"] = {"status": "captured", "started_at": START, "finished_at": START, "duration_ms": 0.0,
            "source": "numeric", "candidates": [{"address": "127.0.0.1", "address_family": 4}], "selected_address": "127.0.0.1", "address_family": 4, "error": None}
        result["attempts"] = [{"sequence": index, "started_at": START, "finished_at": END, "duration_ms": 1.0,
            "address": "127.0.0.1", "address_family": 4, "status": "reply" if outcome in {"reply", "upper_bound"} else outcome,
            "rtt_ms": 0.0 if outcome == "reply" else None, "rtt_upper_bound_ms": 1.0 if outcome == "upper_bound" else None,
            "transmitted": None if outcome == "malformed" else 1, "echo_replies": None if outcome == "malformed" else int(outcome in {"reply", "upper_bound"}),
            "exit_code": 0 if outcome in {"reply", "upper_bound"} else 1, "raw_output": "synthetic <echo>\n", "output_truncated": False, "error": None} for index in range(1, count + 1)]
        result["status"] = result_status or ("partial" if outcome == "malformed" else "captured")
    result["summary"] = summarize_check(kind, result, settings)
    record = {"schema_version": 1, "id": run_id, "kind": "diagnostic-check", "check_type": kind,
        "created_at": START, "finished_at": END, "status": check_run_status(result), "settings": settings,
        "result": result, "iperf_version": None, "output_format": "diagnostic", "exit_code": None,
        "command": [], "error": None, "summary": {}, "intervals": [], "test_parameters": None, "event_count": 0, "events": []}
    validate_check_record(record)
    return record
