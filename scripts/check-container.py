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
        start()
        status = request("/api/status")
        assert status["iperf"]["full_json"] and status["iperf"]["udp_bidir"]
        assert status["storage"] == "/data/runs"
        assert docker("exec", name, "id", "-u") == "10001"
        assert b'data-bs-theme="dark"' in request("/", raw=True)
        assert docker("exec", name, "python", "-m", "app.healthcheck") == ""
        run_id = test_run(2)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            run = request("/api/runs/" + run_id)
            if run["status"] in {"completed", "failed"}:
                break
            time.sleep(.2)
        assert run["status"] == "completed", run
        assert run["metrics"]["upload_mbps"] > 0 and run["metrics"]["download_mbps"] > 0
        assert run["peer"]["available"] and run["events"]
        exported = request(f"/api/runs/{run_id}/export?format=json")
        xml = ElementTree.fromstring(request(f"/api/runs/{run_id}/export?format=xml", raw=True))
        assert xml.find("./field[@name='id']").text == run_id
        preset = request("/api/presets", {"name": "Disposable loopback preset", "settings": {"target": "127.0.0.1", "port": 5202}})
        memory = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", name)
        stop_and_remove()
        start()
        assert request(f"/api/runs/{run_id}/export?format=json") == exported, "Evidence changed after replacement"
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
        result = f"Container checks passed: TCP bidirectional, remote output, JSON/XML exports, non-root/read-only runtime, volume persistence, preset persistence, graceful active-test stop. Image: {size:.1f} MiB; idle memory: {memory}."
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
