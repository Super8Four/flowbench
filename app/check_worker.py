"""Fixed owned DNS/resolution worker. No shell, subprocesses or arbitrary code.

One JSON request on stdin. Completed DNS transport observations are flushed as
bounded JSON lines before fallback; a final JSON object completes the response.
The caller owns deadlines and cancellation, including blocked OS resolution.
"""

import ipaddress
import json
import socket
import sys
import time

from .checks import _clip, prepare_check, utc_now


def dns_attempt(settings, resolver, query_index, transport):
    name = ipaddress.ip_address(settings["target"]).reverse_pointer + "." if settings["record_type"] == "PTR" else settings["target"]
    stamp = utc_now()
    return {"sequence": 1, "query_index": query_index, "started_at": stamp, "finished_at": stamp, "duration_ms": 0.0,
            "transport": transport, "resolver_address": resolver["selected_address"], "resolver_port": resolver["port"], "resolver_family": resolver["address_family"],
            "status": "error", "rcode": None, "flags": [], "question": {"name": name, "type": settings["record_type"], "class": "IN"},
            "answers": [], "authority": [], "additional": [], "records_truncated": False,
            "raw_output": "", "output_truncated": False, "error": None}


def query_dns(settings, resolver, query_index, emit=None):
    import dns.exception
    import dns.flags
    import dns.message
    import dns.query
    import dns.rcode
    import dns.rdataclass
    import dns.rdatatype
    query = dns.message.make_query(dns_attempt(settings, resolver, query_index, settings["transport"])["question"]["name"], settings["record_type"], use_edns=0, payload=1232)
    deadline = time.monotonic() + settings["timeout_ms"] / 1000
    attempts = []
    transport = settings["transport"]
    while True:
        row = dns_attempt(settings, resolver, query_index, transport)
        started = time.monotonic()
        try:
            timeout = max(.001, deadline - started)
            response = (dns.query.udp if transport == "udp" else dns.query.tcp)(query, resolver["selected_address"], port=resolver["port"], timeout=timeout)
            if not query.is_response(response):
                raise dns.query.BadResponse("DNS reply does not match the measured question and transaction.")
            row["rcode"] = dns.rcode.to_text(response.rcode())
            row["flags"] = [flag for flag in ("QR", "AA", "TC", "RD", "RA", "AD", "CD") if response.flags & getattr(dns.flags, flag)]
            row["status"] = "truncated" if response.flags & dns.flags.TC else "nxdomain" if response.rcode() == dns.rcode.NXDOMAIN else "dns_error" if response.rcode() != dns.rcode.NOERROR else "answer" if response.answer else "nodata"
            count, text_bytes = 0, 0
            for key, section in (("answers", response.answer), ("authority", response.authority), ("additional", response.additional)):
                for rrset in section:
                    for item in rrset:
                        record = {"name": rrset.name.to_text(), "type": dns.rdatatype.to_text(rrset.rdtype), "class": dns.rdataclass.to_text(rrset.rdclass), "ttl": rrset.ttl, "rdata": item.to_text()}
                        size = sum(len(record[field].encode("utf-8")) for field in ("name", "type", "class", "rdata"))
                        if count >= 128 or text_bytes + size > 32768 or len(record["rdata"].encode("utf-8")) > 4096:
                            row["records_truncated"] = True
                            continue
                        row[key].append(record)
                        count += 1
                        text_bytes += size
            if row["status"] == "answer" and not row["answers"]:
                # A huge first answer is omitted, not silently called NODATA.
                row["status"] = "dns_error"
            row["raw_output"], row["output_truncated"] = _clip(response.to_text(), 32768)
        except dns.exception.Timeout:
            row.update(status="timeout", error="DNS transport exceeded the logical query deadline.")
        except PermissionError as exc:
            row.update(status="permission_blocked", error=str(exc)[:2048])
        except (OSError, EOFError, dns.exception.DNSException, ValueError) as exc:
            row.update(status="malformed" if isinstance(exc, dns.exception.FormError) else "error",
                       error=(str(exc) or "A DNS TCP peer closed before returning a complete response.")[:2048])
        row.update(finished_at=utc_now(), duration_ms=(time.monotonic() - started) * 1000)
        attempts.append(row)
        if emit is not None:
            emit(row)
        if transport == "udp" and row["status"] == "truncated" and time.monotonic() < deadline:
            transport = "tcp"
        else:
            break
    return {"attempts": attempts}


def resolve(target, family):
    candidates = []
    try:
        af = socket.AF_INET if family == "4" else socket.AF_INET6 if family == "6" else socket.AF_UNSPEC
        for row in socket.getaddrinfo(target, None, family=af, type=socket.SOCK_DGRAM):
            address = str(ipaddress.ip_address(row[4][0]))
            candidate = {"address": address, "address_family": ipaddress.ip_address(address).version}
            if candidate not in candidates:
                candidates.append(candidate)
            if len(candidates) == 16:
                break
        return {"status": "captured" if candidates else "error", "candidates": candidates, "error": None if candidates else "No address returned by the OS resolver."}
    except (OSError, ValueError) as exc:
        return {"status": "error", "error": str(exc)[:2048]}


def main():
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError("Oversized worker input.")
        payload = json.loads(raw)
        if payload["operation"] == "resolve":
            _, settings = prepare_check({"check_type": "loss", "settings": {"target": payload["target"], "address_family": payload["address_family"], "count": 1}})
            result = resolve(settings["target"], settings["address_family"])
        elif payload["operation"] == "dns":
            _, settings = prepare_check({"check_type": "dns", "settings": payload["settings"]})
            from .checks import _validate_resolver, _integer
            _validate_resolver(payload["resolver"], settings)
            _integer(payload["query_index"], 1, settings["count"], "query index")
            query_dns(settings, payload["resolver"], payload["query_index"],
                      emit=lambda row: print(json.dumps({"attempt": row}, allow_nan=False), flush=True))
            result = {"status": "done"}
        else:
            raise ValueError("Unknown fixed worker operation.")
    except Exception as exc:
        result = {"status": "error", "error": str(exc)[:2048]}
    sys.stdout.write(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
