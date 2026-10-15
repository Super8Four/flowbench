"""Bounded, reported endpoint evidence. No resolution, route probe or inference.

Numeric socket pairs come from native connected rows. Configured targets,
accepted control peers and diagnostic destinations are distinct evidence.
The execution hostname is a process/container name, not a source address.
"""

import copy
from datetime import datetime, timezone
import ipaddress
import json
import re
import socket

FIELDS = {"schema_version", "status", "execution_hostname", "sockets", "control_peers", "destinations"}
STATUSES = {"pending", "captured", "partial", "unavailable", "not_applicable", "not_recorded"}
ACTIVE = {"pending", "starting", "queued", "running"}
POINT_FIELDS = {"address", "port", "family"}
SOCKET_FIELDS = {"reporter", "observer", "socket_id", "protocol", "local", "remote", "source"}
CONTROL_FIELDS = {"reporter", "peer", "source"}
DEST_FIELDS = {"purpose", "endpoint", "reported_host", "transport", "source"}
EMPTY = {"address": None, "port": None, "family": None}


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _text(value, limit, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not 1 <= len(value) <= limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Invalid endpoint text.")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Endpoint text must be valid Unicode.") from None


def _address(value):
    _text(value, 128)
    if "%" in value:
        bare, scope = value.split("%", 1)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", scope):
            raise ValueError("Invalid IPv6 scope.")
        parsed = ipaddress.ip_address(bare)
        if parsed.version != 6:
            raise ValueError("Only IPv6 addresses can have a scope.")
        return str(parsed) + "%" + scope, 6
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("Endpoint addresses must be numeric IP literals.") from None
    return str(parsed), parsed.version


def _point(address=None, port=None):
    result = dict(EMPTY)
    try:
        result["address"], result["family"] = _address(address)
        if ipaddress.ip_address(result["address"].split("%", 1)[0]).is_unspecified:
            result.update(address=None, family=None)
    except (ValueError, TypeError):
        pass
    if _integer(port, 1, 65535):
        result["port"] = port
    return result


def _validate_point(value):
    if not isinstance(value, dict) or set(value) != POINT_FIELDS:
        raise ValueError("Invalid endpoint point fields.")
    if value["address"] is None:
        if value["family"] is not None:
            raise ValueError("An unknown address cannot claim an IP family.")
    else:
        canonical, family = _address(value["address"])
        if canonical != value["address"] or type(value["family"]) is not int or value["family"] != family:
            raise ValueError("Endpoint address and family disagree.")
        if ipaddress.ip_address(canonical.split("%", 1)[0]).is_unspecified:
            raise ValueError("An unspecified bind address is not a measured endpoint address.")
    if value["port"] is not None and not _integer(value["port"], 1, 65535):
        raise ValueError("Endpoint ports must be integers from 1 through 65535.")


def runtime_hostname():
    """Local OS identity only. Never call this from historical derivation."""
    try:
        value = socket.gethostname()
        _text(value, 253)
        return value
    except (OSError, ValueError):
        return None


def request_context(remote_addr):
    return {"schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
            "source": "wsgi_remote_addr", "peer": _point(remote_addr)}


def validate_context(value):
    if not isinstance(value, dict) or set(value) != {"schema_version", "observed_at", "source", "peer"} or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["source"] != "wsgi_remote_addr":
        raise ValueError("Invalid initiating request context.")
    stamp = value["observed_at"]
    if not isinstance(stamp, str) or len(stamp) > 40 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", stamp):
        raise ValueError("Request context must retain an explicit UTC timestamp.")
    try:
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid request context UTC timestamp.") from None
    _validate_point(value["peer"])
    if value["peer"]["port"] is not None:
        raise ValueError("HTTP request ports are not captured endpoint evidence.")
    return value


def initial_endpoints(record, execution_hostname=None):
    _text(execution_hostname, 253, nullable=True)
    return {"schema_version": 1, "status": "not_applicable" if record.get("kind") in ("protocol-job", "matrix-job") else "pending",
            "execution_hostname": execution_hostname, "sockets": [], "control_peers": [], "destinations": []}


def _previous(record):
    value = record.get("endpoints")
    if value is not None:
        validate_endpoints(value)
        return copy.deepcopy(value)
    return initial_endpoints(record)


def _append_unique(rows, row, limit):
    if "socket_id" in row and row["socket_id"] is not None:
        for old in rows:
            if old["reporter"] == row["reporter"] and old["socket_id"] == row["socket_id"] and not _socket_conflict(old, row):
                for side in ("local", "remote"):
                    for field in POINT_FIELDS:
                        if old[side][field] is None:
                            old[side][field] = row[side][field]
                if old["protocol"] is None:
                    old["protocol"] = row["protocol"]
                if old["observer"] == "unknown":
                    old["observer"] = row["observer"]
                return True
    if row not in rows:
        if len(rows) == limit:
            return False
        rows.append(row)
    return True


def _socket_conflict(left, right):
    return (any(left[side][key] is not None and right[side][key] is not None and left[side][key] != right[side][key]
                for side in ("local", "remote") for key in POINT_FIELDS)
            or left["protocol"] is not None and right["protocol"] is not None and left["protocol"] != right["protocol"])


def _has_address(point):
    return point["address"] is not None and not ipaddress.ip_address(point["address"].split("%", 1)[0]).is_unspecified


def _finish_status(value, record, incomplete=False, observed=()):
    # A complete retained row cannot account for a previously skipped malformed
    # row. Only fresh matching socket evidence can reconcile that warning;
    # end-only output, an empty start or another reporter cannot do so.
    previous_sockets = (record.get("endpoints") or {}).get("sockets", [])
    reconciled = bool(previous_sockets) and all(any(
        row["reporter"] == fresh["reporter"] and row["socket_id"] == fresh["socket_id"]
        and row["local"] == fresh["local"] and row["remote"] == fresh["remote"]
        for fresh in observed) for row in value["sockets"])
    incomplete |= value["status"] == "partial" and (not reconciled or len(value["sockets"]) == 256 or len(value["control_peers"]) == 8 or len(value["destinations"]) == 256)
    if record.get("kind") in ("protocol-job", "matrix-job"):
        value.update(status="not_applicable", sockets=[], control_peers=[], destinations=[])
    elif value["sockets"]:
        complete = all(_has_address(row["local"]) and _has_address(row["remote"]) and row["local"]["port"] is not None and row["remote"]["port"] is not None for row in value["sockets"])
        value["status"] = "captured" if complete and not incomplete else "partial"
    elif value["control_peers"] or any(row["purpose"] != "flent_reported_target" for row in value["destinations"]):
        value["status"] = "partial"
    elif incomplete:
        value["status"] = "partial"
    else:
        value["status"] = "pending" if record.get("status") in ACTIVE else "unavailable"
    return value


def _observe(value, record, start, reporter, observed=None):
    if not isinstance(start, dict):
        return True
    params = start.get("test_start")
    params = params if isinstance(params, dict) else {}
    protocol = params.get("protocol")
    protocol = protocol.lower() if isinstance(protocol, str) and protocol.lower() in {"tcp", "udp"} else None
    observer = ("client" if isinstance(start.get("connecting_to"), dict) and start["connecting_to"] else
                "server" if isinstance(start.get("accepted_connection"), dict) and start["accepted_connection"] else
                (record.get("settings") or {}).get("role", "unknown") if reporter == "local" else "server")
    observer = observer if observer in ("client", "server") else "unknown"
    connected = start.get("connected", [])
    malformed = not isinstance(connected, list)
    if isinstance(connected, list):
        for raw in connected[:256]:
            if not isinstance(raw, dict):
                malformed = True
                continue
            row = {"reporter": reporter, "observer": observer, "socket_id": raw.get("socket") if _integer(raw.get("socket"), 0, 2**31 - 1) else None,
                   "protocol": protocol, "local": _point(raw.get("local_host"), raw.get("local_port")),
                   "remote": _point(raw.get("remote_host"), raw.get("remote_port")), "source": "iperf_start_connected"}
            malformed |= not _append_unique(value["sockets"], row, 256)
            if observed is not None:
                observed.append(copy.deepcopy(row))
        malformed |= len(connected) > 256
    accepted = start.get("accepted_connection")
    if isinstance(accepted, dict) and accepted:
        row = {"reporter": reporter, "peer": _point(accepted.get("host"), accepted.get("port")), "source": "iperf_accepted_connection"}
        malformed |= not _append_unique(value["control_peers"], row, 8)
    return malformed


def observe_iperf_start(record, start, reporter="local"):
    if reporter not in ("local", "peer"):
        raise ValueError("Invalid endpoint reporter.")
    value = _previous(record)
    observed = []
    incomplete = _observe(value, record, start, reporter, observed)
    return _finish_status(value, record, incomplete, observed)


def _structured_starts(record):
    native = record.get("native_result")
    native = native if isinstance(native, dict) else {}
    if isinstance(native.get("start"), dict):
        yield native["start"], "local"
    incoming = record.get("incoming")
    if isinstance(incoming, dict) and isinstance(incoming.get("start"), dict):
        yield incoming["start"], "local"
    peer = native.get("server_output_json") or record.get("server_output_json")
    if isinstance(peer, str):
        try:
            peer = json.loads(peer) if len(peer.encode("utf-8")) <= 1024 * 1024 else None
        except (ValueError, UnicodeError):
            peer = None
    if isinstance(peer, dict) and isinstance(peer.get("start"), dict):
        yield peer["start"], "peer"


def derive_endpoints(record):
    value = _previous(record)
    incomplete = False
    observed = []
    for start, reporter in _structured_starts(record):
        incomplete |= _observe(value, record, start, reporter, observed)
    if record.get("kind") == "diagnostic-check":
        result = record.get("result")
        result = result if isinstance(result, dict) else {}
        attempts = result.get("attempts")
        if record.get("check_type") == "dns" and isinstance(attempts, list):
            for attempt in attempts[:10]:
                if isinstance(attempt, dict):
                    point = _point(attempt.get("resolver_address"), attempt.get("resolver_port"))
                    transport = attempt.get("transport")
                    if point["address"] and transport in ("udp", "tcp"):
                        incomplete |= not _append_unique(value["destinations"], {"purpose": "dns_resolver", "endpoint": point,
                            "reported_host": None, "transport": transport, "source": "attempt_destination"}, 256)
        elif record.get("check_type") == "loss":
            resolution = result.get("resolution")
            if isinstance(resolution, dict) and resolution.get("status") == "captured":
                point = _point(resolution.get("selected_address"))
                if point["address"]:
                    _append_unique(value["destinations"], {"purpose": "icmp_target", "endpoint": point,
                        "reported_host": None, "transport": "icmp", "source": "pinned_destination"}, 256)
    elif record.get("kind") == "flent-run":
        flent = record.get("flent")
        data = flent.get("data") if isinstance(flent, dict) else None
        meta = data.get("metadata") if isinstance(data, dict) else None
        host = meta.get("HOST") if isinstance(meta, dict) else None
        try:
            _text(host, 253)
            _append_unique(value["destinations"], {"purpose": "flent_reported_target", "endpoint": _point(host),
                "reported_host": host, "transport": None, "source": "tool_metadata"}, 256)
        except ValueError:
            pass
    return _finish_status(value, record, incomplete, observed)


def validate_endpoints(value, record=None):
    if not isinstance(value, dict) or set(value) != FIELDS or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Invalid endpoint evidence schema.")
    if not isinstance(value["status"], str) or value["status"] not in STATUSES:
        raise ValueError("Invalid endpoint availability.")
    _text(value["execution_hostname"], 253, nullable=True)
    for key, limit in (("sockets", 256), ("control_peers", 8), ("destinations", 256)):
        if not isinstance(value[key], list) or len(value[key]) > limit:
            raise ValueError("Endpoint evidence exceeds its row bounds.")
    for row in value["sockets"]:
        if not isinstance(row, dict) or set(row) != SOCKET_FIELDS or row["reporter"] not in ("local", "peer") or row["observer"] not in ("client", "server", "unknown") or row["protocol"] not in (None, "tcp", "udp") or row["source"] != "iperf_start_connected":
            raise ValueError("Invalid native socket evidence.")
        if row["socket_id"] is not None and not _integer(row["socket_id"], 0, 2**31 - 1):
            raise ValueError("Invalid native socket identifier.")
        _validate_point(row["local"]); _validate_point(row["remote"])
    for row in value["control_peers"]:
        if not isinstance(row, dict) or set(row) != CONTROL_FIELDS or row["reporter"] not in ("local", "peer") or row["source"] != "iperf_accepted_connection":
            raise ValueError("Invalid accepted control-peer evidence.")
        _validate_point(row["peer"])
    for row in value["destinations"]:
        if not isinstance(row, dict) or set(row) != DEST_FIELDS:
            raise ValueError("Invalid diagnostic destination fields.")
        _validate_point(row["endpoint"])
        _text(row["reported_host"], 253, nullable=True)
        if row["purpose"] == "dns_resolver":
            valid = row["source"] == "attempt_destination" and row["transport"] in ("udp", "tcp") and row["endpoint"]["address"] is not None and row["endpoint"]["port"] is not None and row["reported_host"] is None
        elif row["purpose"] == "icmp_target":
            valid = row["source"] == "pinned_destination" and row["transport"] == "icmp" and row["endpoint"]["address"] is not None and row["endpoint"]["port"] is None and row["reported_host"] is None
        elif row["purpose"] == "flent_reported_target":
            valid = row["source"] == "tool_metadata" and row["transport"] is None and row["endpoint"]["port"] is None and row["reported_host"] is not None
        else:
            valid = False
        if not valid:
            raise ValueError("Diagnostic destination purpose, source and transport disagree.")
    if value["status"] == "captured" and (not value["sockets"] or any(not _has_address(row["local"]) or not _has_address(row["remote"]) or row["local"]["port"] is None or row["remote"]["port"] is None for row in value["sockets"])):
        raise ValueError("Captured endpoints require complete native socket pairs.")
    if value["status"] in {"not_applicable", "not_recorded"} and any(value[k] for k in ("sockets", "control_peers", "destinations")):
        raise ValueError("Absent endpoint evidence cannot contain socket observations.")
    if record is not None:
        # Correlate only explicit preserved native starts. End-only output must
        # not invalidate earlier streaming observations in an interrupted file.
        source = initial_endpoints(record)
        for start, reporter in _structured_starts(record):
            _observe(source, record, start, reporter)
        for row in value["sockets"]:
            matches = [raw for raw in source["sockets"] if raw["reporter"] == row["reporter"] and raw["socket_id"] is not None and raw["socket_id"] == row["socket_id"]]
            if matches and all(_socket_conflict(raw, row) for raw in matches):
                raise ValueError("Normalized endpoints contradict their preserved native socket.")
    return value


def endpoint_view(record):
    if "endpoints" in record:
        provenance = "imported_archive" if record.get("imported") else "captured_archive"
        try:
            validate_endpoints(record["endpoints"], record)
            value = copy.deepcopy(record["endpoints"])
        except ValueError:
            value = initial_endpoints(record)
            value["status"] = "unavailable"
    else:
        value = derive_endpoints(record)
        value["execution_hostname"] = None
        present = any(value[k] for k in ("sockets", "control_peers", "destinations"))
        provenance = "preserved_native" if present else "not_recorded"
        if not present:
            value["status"] = "not_recorded"
    if value["status"] == "pending" and record.get("status") not in ACTIVE:
        value["status"] = "unavailable"
    return dict(value, provenance=provenance)


def endpoints_summary(record):
    try:
        view = endpoint_view(record)
    except ValueError:
        return {"status": "unavailable", "socket_count": 0, "destination_count": 0, "families": []}
    points = [point for row in view["sockets"] for point in (row["local"], row["remote"])] + [row["endpoint"] for row in view["destinations"]]
    return {"status": view["status"], "socket_count": len(view["sockets"]), "destination_count": len(view["destinations"]),
            "families": sorted({point["family"] for point in points if point["family"] is not None})}
