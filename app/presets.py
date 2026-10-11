"""Offline server catalog with validated, additive imports and custom settings."""

import copy
import hashlib
import json
import re
import shlex
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .runner import now, settings_from

SOURCE_URL = "https://export.iperf3serverlist.net/listed_iperf3_servers.json"
SOURCE_PAGE = "https://iperf3serverlist.net/"
MAX_CATALOG_BYTES = 8 * 1024 * 1024


def text_field(data, key, limit=300):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Preset {key} must be text up to {limit} characters.")
    return value.strip()


def port_range(value):
    if isinstance(value, bool) or not re.fullmatch(r"\d+(?:-\d+)?", str(value)):
        raise ValueError("Port must be a number or range such as 5201-5209.")
    parts = [int(part) for part in str(value).split("-")]
    low, high = parts[0], parts[-1]
    if not 1 <= low <= high <= 65535:
        raise ValueError("Port range must be within 1–65535.")
    return str(low) if low == high else f"{low}-{high}"


def normalize(data):
    if not isinstance(data, dict):
        raise ValueError("Each server preset must be an object.")
    # Both official export variants: parsed IP/HOST/PORT and raw COMMAND.
    external = "IP/HOST" in data or "COMMAND" in data
    if external:
        target, ports = data.get("IP/HOST"), data.get("PORT", "5201")
        if target is None:
            tokens = shlex.split(text_field(data, "COMMAND", 1000))
            if len(tokens) < 3 or tokens[:2] != ["iperf3", "-c"]:
                raise ValueError("Unrecognized server command; use iperf3 -c HOST [-p PORT].")
            target, ports = tokens[2], "5201"
            tail = tokens[3:]
            if tail:
                if len(tail) != 2 or tail[0] != "-p":
                    raise ValueError("Only host and port are imported from a server command.")
                ports = tail[1]
        ports = port_range(ports or "5201")
        options = text_field(data, "OPTIONS").replace(",", " ").split()
        if set(options) - {"-R", "-6", "-u", "Upload"}:
            raise ValueError("Unsupported public server options.")
        settings = settings_from({"target": target, "port": int(ports.split("-")[0])})
        country, city = text_field(data, "COUNTRY"), text_field(data, "SITE")
        provider = text_field(data, "PROVIDER")
        label = " · ".join(filter(None, [country, city, provider or target]))[:200]
        item = {"name": label, "kind": "public", "ports": ports, "settings": settings,
                "country": country, "city": city, "provider": provider,
                "continent": text_field(data, "CONTINENT"), "capacity": text_field(data, "GB/S"),
                "protocols": ["tcp", "udp"] if "-u" in options else ["tcp"],
                "modes": ["upload", "download"] if "-R" in options else ["upload"],
                "ipv6": "-6" in options, "source": SOURCE_PAGE, "notes": ""}
    else:
        settings = settings_from(data.get("settings"))
        if settings["role"] != "client":
            raise ValueError("Server presets must contain client settings.")
        ports = port_range(data.get("ports", settings["port"]))
        low, high = [int(part) for part in (ports.split("-") if "-" in ports else [ports, ports])]
        if not low <= settings["port"] <= high:
            raise ValueError("The default port must be inside the preset's port range.")
        kind = data.get("kind", "custom")
        if kind not in {"public", "custom"}:
            raise ValueError("Preset kind must be public or custom.")
        item = {key: text_field(data, key, 4000 if key == "notes" else 300)
                for key in ("country", "city", "provider", "continent", "capacity", "source", "notes")}
        if item["source"] and urlsplit(item["source"]).scheme not in {"http", "https"}:
            raise ValueError("Preset source must be an HTTP or HTTPS URL.")
        item.update(name=text_field(data, "name", 200), kind=kind, ports=ports, settings=settings)
        for key, choices, default in (("protocols", {"tcp", "udp"}, ["tcp", "udp"]),
                                      ("modes", {"upload", "download", "bidir"}, ["upload", "download", "bidir"])):
            value = data.get(key, default)
            if not isinstance(value, list) or not value or any(not isinstance(v, str) or v not in choices for v in value):
                raise ValueError(f"Invalid supported {key}.")
            item[key] = list(dict.fromkeys(value))
        item["ipv6"] = data.get("ipv6", True)
        if not isinstance(item["ipv6"], bool):
            raise ValueError("ipv6 must be true or false.")
    if not item["name"]:
        raise ValueError("Give the preset a name.")
    if settings["protocol"] not in item["protocols"] or settings["mode"] not in item["modes"]:
        raise ValueError("Default protocol/direction must be supported by the preset.")
    identity = [item["kind"], item["name"], settings["target"].lower(), ports]
    item["id"] = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]
    return item


def catalog_from(data):
    if isinstance(data, dict):
        if data.get("schema_version") != 1:
            raise ValueError("Unsupported preset schema version.")
        data = data.get("presets")
    if not isinstance(data, list) or not data or len(data) > 5000:
        raise ValueError("Upload a non-empty JSON server list (maximum 5000 presets).")
    items = []
    for index, row in enumerate(data, 1):
        try:
            items.append(normalize(row))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Server {index}: {exc}") from None
    return items


class PresetStore:
    def __init__(self, manager):
        self.manager = manager
        self.path = manager.directory / "server-presets.json"
        self.defaults = Path(__file__).resolve().parent / "data" / "public-servers.json"

    def read(self):
        with self.manager.lock:
            path = self.path if self.path.exists() else self.defaults
            return json.loads(path.read_text(encoding="utf-8"))

    def write(self, catalog):
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(catalog, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        deadline = time.monotonic() + 5
        while True:
            try:
                temp.replace(self.path)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)

    def merge(self, data):
        items = catalog_from(data)  # Validate the entire file before changing storage.
        with self.manager.lock:
            catalog = self.read()
            existing = {row["id"]: row for row in catalog["presets"]}
            added = sum(row["id"] not in existing for row in {r["id"]: r for r in items}.values())
            existing.update({row["id"]: row for row in items})
            catalog.update(presets=list(existing.values()), updated_at=now())
            self.write(catalog)
            return {"added": added, "updated": len({r['id'] for r in items}) - added, "total": len(existing)}

    def save_custom(self, data, preset_id=None):
        if not isinstance(data, dict):
            raise ValueError("Send a custom preset object.")
        item = normalize({**data, "kind": "custom"})
        with self.manager.lock:
            catalog = self.read()
            if preset_id:
                original = next((r for r in catalog["presets"] if r["id"] == preset_id), None)
                if not original or original["kind"] != "custom":
                    raise ValueError("Choose an existing custom preset to update.")
                catalog["presets"] = [r for r in catalog["presets"] if r["id"] != preset_id]
            catalog["presets"] = [r for r in catalog["presets"] if r["id"] != item["id"]] + [item]
            catalog["updated_at"] = now()
            self.write(catalog)
        return copy.deepcopy(item)

    def delete_custom(self, preset_id):
        with self.manager.lock:
            catalog = self.read()
            original = next((r for r in catalog["presets"] if r["id"] == preset_id), None)
            if not original or original["kind"] != "custom":
                raise ValueError("Only custom presets can be removed.")
            catalog["presets"] = [r for r in catalog["presets"] if r["id"] != preset_id]
            catalog["updated_at"] = now()
            self.write(catalog)

    def refresh(self):
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "iPerf-workspace/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            content = response.read(MAX_CATALOG_BYTES + 1)
        if len(content) > MAX_CATALOG_BYTES:
            raise ValueError("The public server list exceeds 8 MiB.")
        from .imports import parse_document
        items = catalog_from(parse_document(content))
        with self.manager.lock:
            catalog = self.read()
            custom = [row for row in catalog["presets"] if row["kind"] == "custom"]
            catalog.update(presets=list({r["id"]: r for r in items + custom}.values()),
                           source=SOURCE_PAGE, source_url=SOURCE_URL, source_checked_at=now(), updated_at=now())
            self.write(catalog)
        return {"public": len(items), "custom": len(custom)}
