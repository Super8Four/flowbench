"""Actual optional Linux Flent RRUL test, entirely inside an isolated container.

Build scripts/Flent.Dockerfile as flowbench-flent-test, then run this script
mounted read-only at /checks/check-flent.py with --network none. Do not publish
the derivative image. The original runtime image remains free of optional tools.
"""

import json
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

sys.path.insert(0, "/opt/iperf-workspace")
from app import create_app
from app.exports import xml_export
from app.imports import parse_document
from app.runner import RunManager


def groups():
    result = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            result.add(int(stat[2]))
        except (OSError, ValueError, IndexError):
            pass
    return result


def finish(manager, run_id):
    manager.worker.join(timeout=35)
    assert not manager.worker.is_alive(), "Flent worker exceeded bounded loopback check"
    return manager.export(run_id)


with tempfile.TemporaryDirectory(prefix="flowbench-flent-check-") as directory:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        port = handle.getsockname()[1]
    # -D prevents daemonization so this test owns and cleans its peer process.
    peer = subprocess.Popen(["netserver", "-D", "-L", "127.0.0.1", "-p", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    app = create_app({"TESTING": True, "RUNS_DIRECTORY": directory, "RESUME_LISTENER": False})
    manager = app.extensions["iperf_runs"]
    try:
        time.sleep(.5)
        assert peer.poll() is None, "Owned loopback Netserver did not start"
        assert manager.flent_capabilities["available"], manager.flent_capabilities
        assert "2.3.0" in manager.flent_capabilities["version"], manager.flent_capabilities
        assert "2.7.1" in manager.flent_capabilities["netperf_version"], manager.flent_capabilities
        resolver_patch = Path("/usr/local/share/licenses/netperf/netperf-loopback-resolver.patch")
        patch_hash = hashlib.sha256(resolver_patch.read_bytes()).hexdigest()
        assert patch_hash == "1d08ee0d3154fa82af48d3f1ff44218c5a1317f0d95eed32721445656967451f"
        print("Test-only tool provenance: unmodified Flent 2.3.0; Netperf 2.7.1 source afc51ff9764741da4ed6702651fba9d9c23f8557 with numeric-loopback resolver patch SHA256 " + patch_hash, flush=True)
        client = app.test_client()
        payload = {"target": "127.0.0.1", "duration": 3, "netserver_port": port, "workload": "rrul",
                   "confirm_netserver": True, "label": "Synthetic loopback RRUL", "bind": "127.0.0.1", "ip_version": "4"}
        response = client.post("/api/flent", json=payload)
        assert response.status_code == 201, response.get_json()
        record = finish(manager, response.get_json()["id"])
        evidence = record["flent"]
        native = evidence.get("data") or {}
        print("Synthetic loopback RRUL diagnostics: " + json.dumps({
            "status": record["status"], "error": record["error"], "evidence_status": evidence["status"],
            "quality": evidence.get("quality"), "summaries": evidence.get("summaries"),
            "series_metadata": (native.get("metadata") or {}).get("SERIES_META"),
            "raw_value_counts": {key: len(value) if isinstance(value, list) else None for key, value in native.get("raw_values", {}).items()},
            "stdout": record["raw_stdout"][-12000:], "stderr": record["raw_stderr"][-16000:]
        }, ensure_ascii=False), flush=True)
        assert record["status"] == "completed", record
        assert record['preflight']['policy'] == 'once_per_test' and record['preflight']['phase'] == 'before_traffic'
        assert record['preflight']['finished_at'] and record['preflight']['time_quality']['ntp_verified'] is False
        assert record['route']['phase'] == 'before_traffic' and len(record['route']['hops']) <= 16
        assert record["flent"]["status"] == "captured", record["flent"]
        assert record["flent"]["data"]["metadata"]["NAME"] == "rrul"
        rates = [r for r in record["flent"]["summaries"] if r["unit"] == "Mbits/s" and r["mean"] is not None]
        assert any(row["mean"] > 0 for row in rates), "Native Flent throughput missing"
        assert any(row["unit"] == "ms" for row in record["flent"]["series"]), "Native RTT identities missing"
        original_data = record["flent"]["data"]
        for xml in (False, True):
            content = xml_export(record).decode() if xml else json.dumps(record)
            imported = client.post("/api/runs/import", json={"content": content, "filename": "rrul.xml" if xml else "rrul.json"})
            assert imported.status_code == 200, imported.get_json()
            imported_id = (imported.get_json()['imported'] or imported.get_json()['duplicates'])[0]
            assert manager.export(imported_id)['preflight'] == record['preflight']
        source_snapshot = manager.export(record["id"])
        plan = client.get("/api/runs/" + record["id"] + "/rerun-plan")
        assert plan.status_code == 200, plan.get_json()
        intent = {"request_id": uuid4().hex, "source_fingerprint": plan.get_json()["source_fingerprint"],
                  "confirm_netserver": True}
        rerun = client.post("/api/runs/" + record["id"] + "/rerun", json=intent)
        assert rerun.status_code == 201, rerun.get_json()
        rerun_id = rerun.get_json()["run"]["id"]
        assert rerun_id != record["id"]
        retry = client.post("/api/runs/" + record["id"] + "/rerun", json=intent)
        assert retry.status_code == 200 and retry.get_json()["replayed"], retry.get_json()
        assert retry.get_json()["run"]["id"] == rerun_id
        repeated = finish(manager, rerun_id)
        assert repeated["status"] == "completed" and repeated["flent"]["status"] == "captured", repeated
        assert repeated["lineage"]["source_run_id"] == record["id"]
        assert repeated["created_at"] != record["created_at"]
        assert any(row["unit"] == "Mbits/s" and row["mean"] is not None and row["mean"] > 0
                   for row in repeated["flent"]["summaries"]), "Native Run again throughput missing"
        assert manager.export(record["id"]) == source_snapshot, "Run again changed original evidence"
        # Another actual RRUL is cancelled while its TCP children are active.
        payload["duration"] = 30
        response = client.post("/api/flent", json=payload)
        assert response.status_code == 201, response.get_json()
        cancel_id = response.get_json()["id"]
        deadline = time.monotonic() + 5
        while manager.process is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert manager.process is not None, "Cancellation check never launched Flent"
        owned_group = manager.process.pid
        time.sleep(3)
        assert client.post("/api/runs/" + cancel_id + "/stop", json={}).status_code == 200
        cancelled = finish(manager, cancel_id)
        assert cancelled["status"] == "cancelled", cancelled
        # --init reaps terminated descendants asynchronously; allow a bounded
        # drain before asserting ownership cleanup on slower native runners.
        deadline = time.monotonic() + 2
        while owned_group in groups() and time.monotonic() < deadline:
            time.sleep(.05)
        assert owned_group not in groups(), "Cancelled Flent left process-group descendants"
        assert peer.poll() is None, "Flent cancellation stopped the independent Netserver"
        manager.shutdown()
        manager = RunManager(directory)
        assert manager.export(record["id"])["flent"]["data"] == original_data
        assert manager.export(record['id'])['preflight'] == record['preflight']
        assert manager.read(cancel_id)["status"] == "cancelled"
        assert manager.active_id is None
        assert not list(Path(directory).glob("flent-*")), "Owned temporary output leaked"
        assert manager.read(rerun_id)["lineage"]["source_run_id"] == record["id"]
        assert manager.rerun(record["id"], intent)["run"]["id"] == rerun_id, "Restart replay launched another RRUL"
        print("Actual Flent 2.3.0 RRUL checks passed: native throughput/RTT, immutable Run again and retry/restart identity, JSON/XML imports, active cancellation, no owned child leaks, archive restart.")
    finally:
        manager.shutdown()
        try:
            os.killpg(peer.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        peer.wait(timeout=5)
