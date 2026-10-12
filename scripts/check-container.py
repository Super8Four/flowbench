"""Test a built image with disposable containers and a disposable volume.

Run: python3 scripts/check-container.py [image]
Requires Docker; does not use the production Compose project or its archive.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4
from xml.etree import ElementTree


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    image = sys.argv[1] if len(sys.argv) > 1 else "iperf-workspace:local"
    name = "iperf-check-" + uuid4().hex[:12]
    volume = name + "-data"
    base = ""

    def request(path, data=None, raw=False):
        payload = None if data is None else json.dumps(data).encode()
        with urlopen(Request(base + path, data=payload, headers={"Content-Type": "application/json"}), timeout=5) as response:
            body = response.read()
            return body if raw else json.loads(body)

    def start():
        nonlocal base
        docker("run", "--detach", "--name", name, "--init", "--read-only",
               "--tmpfs", "/tmp:size=32m,mode=1777", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges:true", "--publish", "127.0.0.1::5000",
               "--mount", f"type=volume,source={volume},target=/data", image)
        port = docker("port", name, "5000/tcp").split(":")[-1]
        base = "http://127.0.0.1:" + port
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if request("/api/ready")["ready"]:
                    return
            except (URLError, OSError):
                pass
            time.sleep(.2)
        raise RuntimeError("Container did not become ready: " + docker("logs", name))

    def stop_and_remove():
        docker("stop", "--time", "20", name)
        assert docker("inspect", "--format", "{{.State.ExitCode}}", name) == "0", "Container did not stop cleanly"
        docker("rm", name)

    def test_run(duration, mode="bidir"):
        docker("exec", "--detach", name, "iperf3", "-s", "-1", "-B", "127.0.0.1", "-p", "5202")
        time.sleep(.4)
        return request("/api/runs", {"target": "127.0.0.1", "port": 5202,
                                      "duration": duration, "streams": 2, "mode": mode,
                                      "label": "Disposable container verification"})["id"]

    docker("volume", "create", volume)
    try:
        # Seed only this disposable volume with the pre-SQLite file layout.
        legacy_id = "f" * 32
        seed = '''
import json
from pathlib import Path
from app.runner import settings_from
from app.presets import normalize
root = Path('/data/runs')
path = root / ('f' * 32)
path.mkdir(parents=True)
record = dict(schema_version=1, id='f'*32, created_at='2026-01-01T00:00:00+00:00',
              finished_at='2026-01-01T00:00:01+00:00', status='completed', exit_code=0,
              settings=settings_from(dict(target='127.0.0.1', notes='Legacy volume evidence')),
              summary={}, intervals=[], command=[], error=None, native_result=None,
              test_parameters=None, event_count=1)
event = dict(sequence=1, timestamp=record['created_at'], source='stdout', message='legacy raw output')
(path / 'run.json').write_text(json.dumps(record), encoding='utf-8')
(path / 'events.jsonl').write_text(json.dumps(event)+'\\n', encoding='utf-8')
catalog = dict(schema_version=1, presets=[normalize(dict(name='Legacy preset', settings=dict(target='127.0.0.1')))])
(root / 'server-presets.json').write_text(json.dumps(catalog), encoding='utf-8')
'''
        docker("run", "--rm", "--mount", f"type=volume,source={volume},target=/data",
               "--entrypoint", "python", image, "-c", seed)
        start()
        status = request("/api/status")
        assert status["iperf"]["full_json"] and status["iperf"]["udp_bidir"]
        assert status["storage"] == "/data/runs"
        assert status["storage_backend"] == "sqlite" and status["database"] == "/data/runs/archive.sqlite3"
        assert request("/api/health")["database"] == "connected"
        legacy = request(f"/api/runs/{legacy_id}/export")
        assert legacy["settings"]["notes"] == "Legacy volume evidence"
        assert legacy["events"][0]["message"] == "legacy raw output"
        assert any(row["name"] == "Legacy preset" for row in request("/api/presets")["presets"])
        # Legacy sources remain intact, but must never revert newer SQLite notes.
        payload = json.dumps({"notes": "Updated after SQLite migration"})
        with urlopen(Request(base + f"/api/runs/{legacy_id}", data=payload.encode(), method="PATCH",
                             headers={"Content-Type": "application/json"}), timeout=5) as response:
            assert response.status == 200
        assert docker("exec", name, "id", "-u") == "10001"
        assert b'data-bs-theme="dark"' in request("/", raw=True)
        assert docker("exec", name, "python", "-m", "app.healthcheck") == ""
        run_id = test_run(2)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            run = request("/api/runs/" + run_id)
            if run["status"] in {"completed", "failed"} and run["finished_at"]:
                break
            time.sleep(.2)
        assert run["status"] == "completed", run
        assert run["metrics"]["upload_mbps"] > 0 and run["metrics"]["download_mbps"] > 0
        assert run["peer"]["available"] and run["events"]
        exported = request(f"/api/runs/{run_id}/export?format=json")
        xml_body = request(f"/api/runs/{run_id}/export?format=xml", raw=True)
        xml = ElementTree.fromstring(xml_body)
        assert xml.find("./field[@name='id']").text == run_id
        imported = request("/api/runs/import", {"filename": "test.json", "content": json.dumps(exported)})["imported"][0]
        assert request("/api/runs/import", {"filename": "test.xml", "content": xml_body.decode()})["duplicates"] == [imported]
        imported_export = request(f"/api/runs/{imported}/export")
        assert imported_export["events"] == exported["events"] and imported_export["summary"] == exported["summary"]
        # Persistent peer: Both creates two sequential connections, not one simultaneous test.
        docker("exec", "--detach", name, "iperf3", "-s", "-B", "127.0.0.1", "-p", "5203")
        time.sleep(.4)
        job_id = request("/api/runs", {"target": "127.0.0.1", "port": 5203, "duration": 1,
                                     "protocol": "both", "bandwidth": "2M", "label": "Both protocol check"})["id"]
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            job = request(f"/api/runs/{job_id}")
            if job["status"] in {"completed", "failed", "partial"} and job["finished_at"]:
                break
            time.sleep(.2)
        assert job["status"] == "completed", job
        assert job["settings"]["mode"] == "bidir" and job["metrics"] == {}
        group_export = request(f"/api/runs/{job_id}/export")
        assert [run["settings"]["protocol"] for run in group_export["runs"]] == ["tcp", "udp"]
        for run in group_export["runs"]:
            assert run["metrics"]["upload_mbps"] > 0 and run["metrics"]["download_mbps"] > 0
            assert {point["direction"] for point in run["series"]} == {"upload", "download"}
        group_import = request("/api/runs/import", {"filename": "both.json", "content": json.dumps(group_export)})
        group_xml = request(f"/api/runs/{job_id}/export?format=xml", raw=True)
        assert request("/api/runs/import", {"filename": "both.xml", "content": group_xml.decode()})["duplicates"] == group_import["imported"]
        preset = request("/api/presets", {"name": "Disposable loopback preset", "settings": {"target": "127.0.0.1", "port": 5202}})
        memory = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", name)
        stop_and_remove()
        start()
        assert request(f"/api/runs/{run_id}/export?format=json") == exported, "Evidence changed after replacement"
        assert request(f"/api/runs/{imported}/export") == imported_export, "Import changed after replacement"
        assert request(f"/api/runs/{job_id}/export") == group_export, "Both evidence changed after replacement"
        assert request(f"/api/runs/{legacy_id}")["settings"]["notes"] == "Updated after SQLite migration"
        original = docker("exec", name, "python", "-c",
                          f"import json; print(json.load(open('/data/runs/{legacy_id}/run.json'))['settings']['notes'])")
        assert original == "Legacy volume evidence", "Legacy source was modified"
        assert docker("exec", name, "python", "-c",
                      "import sqlite3; c=sqlite3.connect('/data/runs/archive.sqlite3'); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()") == "ok"
        assert any(row["id"] == preset["id"] for row in request("/api/presets")["presets"]), "Preset was lost"
        active_id = test_run(30, "upload")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if request("/api/runs/" + active_id)["series"]:
                break
            time.sleep(.2)
        else:
            raise RuntimeError("No live interval received before stop")
        stop_and_remove()
        start()
        stopped = request("/api/runs/" + active_id)
        assert stopped["status"] == "cancelled", stopped["status"]
        assert stopped["series"] and stopped["events"] and stopped["finished_at"]
        size = int(docker("image", "inspect", "--format", "{{.Size}}", image)) / (1024 * 1024)
        result = f"Container checks passed: TCP bidirectional, remote output, JSON/XML import/export and deduplication, non-root/read-only runtime, SQLite integrity, legacy volume migration, volume/preset persistence, graceful active-test stop. Image: {size:.1f} MiB; idle memory: {memory}."
        print(result)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as file:
                file.write(result + "\n")
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=False)
        subprocess.run(["docker", "volume", "rm", volume], check=False)


if __name__ == "__main__":
    main()
