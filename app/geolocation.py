"""Optional local DB-IP Lite lookups. No DNS, HTTP, downloads or remote API."""

import copy
import ipaddress
import math
import os
from pathlib import Path
import re
import stat

try:
    import maxminddb
except ImportError:
    maxminddb = None

DEFAULT_DATABASE = "/data/geolocation/dbip-city-lite.mmdb"
MAX_DATABASE_BYTES = 512 * 1024 * 1024
PROVIDER = "DB-IP Lite"
LICENSE = "CC-BY-4.0"
ATTRIBUTION = "IP Geolocation by DB-IP"
ATTRIBUTION_URL = "https://db-ip.com"
NOTE = "Approximate IP network locations, not device GPS or a proven physical traffic path. Private and special-use addresses are not geolocated."
FIELDS = {"address", "scope", "status", "approximate", "city", "country", "country_code", "latitude", "longitude"}
GEOGRAPHY = {"city", "country", "country_code", "latitude", "longitude"}
INTERNAL_V4 = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
INTERNAL_V6 = ipaddress.ip_network("fc00::/7")
SPECIAL_V4 = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "192.0.0.0/24",
    "192.0.2.0/24", "192.88.99.0/24", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4"))
SPECIAL_V6 = tuple(ipaddress.ip_network(value) for value in (
    "::/96", "::ffff:0:0/96", "::ffff:0:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48",
    "100::/64", "2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20", "5f00::/16"))
GLOBAL_V6 = ipaddress.ip_network("2000::/3")


def canonical_address(value):
    if not isinstance(value, str) or len(value) > 253:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def classify_address(value):
    """Conservatively require unambiguous public unicast on Python 3.12 too."""
    address = canonical_address(value)
    if address is None:
        return "unknown"
    ip = ipaddress.ip_address(address)
    if "%" in address:
        return "special"  # A scoped literal is local interface context.
    if ip.version == 4:
        if any(ip in network for network in INTERNAL_V4):
            return "internal"
        if any(ip in network for network in SPECIAL_V4):
            return "special"
    else:
        if ip in INTERNAL_V6:
            return "internal"
        # Deny transition/embedded IPv4 forms even when their outer address looks
        # global; do not infer a physical location from their embedded payload.
        if ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None:
            return "special"
        if any(ip in network for network in SPECIAL_V6) or ip not in GLOBAL_V6:
            return "special"
        # ISATAP embeds an IPv4 address in an otherwise global unicast prefix.
        if ((int(ip) >> 32) & 0xffffffff) in {0x00005efe, 0x02005efe}:
            return "special"
    if (not ip.is_global or ip.is_private or ip.is_reserved or ip.is_multicast
            or ip.is_unspecified or ip.is_loopback or ip.is_link_local):
        return "special"
    return "public"


def geolocation_metadata(status="unavailable", note=None):
    return {"schema_version": 1, "provider": PROVIDER, "license": LICENSE,
            "attribution": ATTRIBUTION, "attribution_url": ATTRIBUTION_URL,
            "status": status, "note": note or NOTE}


def empty_location(address):
    scope = classify_address(address)
    return {"address": canonical_address(address), "scope": scope,
            "status": "internal" if scope == "internal" else "missing" if address is None else "unavailable",
            "approximate": True, "city": None, "country": None, "country_code": None,
            "latitude": None, "longitude": None}


def hop_addresses(hop):
    addresses = hop.get("addresses")
    if not isinstance(addresses, list):
        addresses = [hop["address"]] if hop.get("address") is not None else []
    result = []
    for value in addresses[:16]:
        address = canonical_address(value)
        if address is not None and address not in result:
            result.append(address)
    return result


def _number(value, low, high):
    try:
        return type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    except OverflowError:
        return False


def _text(value):
    if not isinstance(value, str) or not 0 < len(value) <= 200 or any(ord(char) < 32 for char in value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _provider_location(address, value):
    row = empty_location(address)
    if not isinstance(value, dict) or not isinstance(value.get("location"), dict):
        return row
    location = value["location"]
    latitude, longitude = location.get("latitude"), location.get("longitude")
    if not _number(latitude, -90, 90) or not _number(longitude, -180, 180):
        return row
    country, city = value.get("country"), value.get("city")
    if country is not None and not isinstance(country, dict) or city is not None and not isinstance(city, dict):
        return row
    country, city = country or {}, city or {}
    country_names, city_names = country.get("names", {}), city.get("names", {})
    if not isinstance(country_names, dict) or not isinstance(city_names, dict):
        return row
    name, city_name, code = country_names.get("en"), city_names.get("en"), country.get("iso_code")
    if any(text is not None and not _text(text) for text in (name, city_name)):
        return row
    if code is not None and (not isinstance(code, str) or re.fullmatch(r"[A-Z]{2}", code) is None):
        return row
    row.update(status="located", latitude=latitude, longitude=longitude, country=name, city=city_name, country_code=code)
    return row


def enrich_route(route, path=None):
    """Return a copy, bounded to 16 TTLs x 16 addresses, without external I/O.

    One read-only pure-Python mmap is closed after this route. Metadata provides
    type/build provenance; the 121 MB database is never rehashed per test.
    """
    result = copy.deepcopy(route)
    metadata = geolocation_metadata()
    result["geolocation"] = metadata
    hops = result.get("hops", [])
    if not isinstance(hops, list) or len(hops) > 16 or any(not isinstance(hop, dict) for hop in hops):
        metadata.update(status="error", note="Route exceeds the supported offline geolocation bounds. " + NOTE)
        return result
    public = set()
    for hop in hops:
        addresses = hop_addresses(hop)
        hop["locations"] = [empty_location(address) for address in addresses] or [empty_location(None)]
        public.update(address for address in addresses if classify_address(address) == "public")
    if not public:
        metadata["note"] = "No unambiguous public hop addresses are available for local lookup. " + NOTE
        return result
    try:
        selected = os.environ.get("FLOWBENCH_GEO_DB", DEFAULT_DATABASE) if path is None else os.fspath(path)
    except (TypeError, ValueError, OSError):
        metadata["note"] = "Local geolocation database path is unavailable. " + NOTE
        return result
    if (not isinstance(selected, str) or not selected or selected.startswith(("\\\\", "//"))
            or re.match(r"^[A-Za-z]+://", selected) or maxminddb is None):
        metadata["note"] = "Local geolocation database or reader unavailable. " + NOTE
        return result
    try:
        database = Path(selected)
        info = database.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_DATABASE_BYTES:
            metadata["note"] = "Local geolocation database is not a supported regular file. " + NOTE
            return result
        with maxminddb.open_database(database, mode=maxminddb.MODE_MMAP) as reader:
            details = reader.metadata()
            database_type, build_epoch = details.database_type, details.build_epoch
            if database_type != "DBIP-City-Lite" or type(build_epoch) is not int or not 0 <= build_epoch <= 253402300799:
                metadata["note"] = "Local database type or build metadata is unsupported. " + NOTE
                return result
            metadata.update(status="available", database_type=database_type, build_epoch=build_epoch)
            located = {address: _provider_location(address, reader.get(address)) for address in sorted(public)}
        for hop in hops:
            hop["locations"] = [copy.deepcopy(located.get(row["address"], row)) for row in hop["locations"]]
    except (FileNotFoundError, PermissionError):
        metadata.update(status="unavailable", note="Local geolocation database is missing or unreadable. " + NOTE)
    except Exception:
        # Optional local evidence must never turn successful traffic into a
        # failed run. Do not expose private paths or decoder exception payloads.
        metadata.update(status="error", note="Local geolocation database could not be read safely. " + NOTE)
    return result


def validate_route_geolocation(route):
    """Validate imported optional geography and its association to every reply."""
    if not isinstance(route, dict) or not isinstance(route.get("hops"), list) or any(not isinstance(hop, dict) for hop in route["hops"]):
        raise ValueError("Invalid geolocation route structure.")
    metadata = route.get("geolocation")
    hops = route.get("hops", [])
    if metadata is None and all("locations" not in hop for hop in hops):
        return route  # Legacy evidence need not invent geography.
    fields = {"schema_version", "provider", "license", "attribution", "attribution_url", "status", "note"}
    if (not isinstance(metadata, dict) or not fields <= set(metadata) or set(metadata) - fields - {"database_type", "build_epoch"}
            or type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1
            or metadata.get("provider") != PROVIDER or metadata.get("license") != LICENSE
            or metadata.get("attribution") != ATTRIBUTION or metadata.get("attribution_url") != ATTRIBUTION_URL
            or not isinstance(metadata.get("status"), str) or metadata["status"] not in {"available", "unavailable", "error"}
            or not isinstance(metadata.get("note"), str) or len(metadata["note"]) > 2048):
        raise ValueError("Invalid offline geolocation provenance.")
    if "database_type" in metadata and metadata["database_type"] != "DBIP-City-Lite":
        raise ValueError("Unsupported geolocation database type.")
    if "build_epoch" in metadata and (type(metadata["build_epoch"]) is not int or not 0 <= metadata["build_epoch"] <= 253402300799):
        raise ValueError("Invalid geolocation database build date.")
    if metadata["status"] == "available" and not {"database_type", "build_epoch"} <= set(metadata):
        raise ValueError("Available geography needs database build provenance.")
    if not isinstance(hops, list) or len(hops) > 16:
        raise ValueError("Geolocation supports at most 16 route hops.")
    for hop in hops:
        rows = hop.get("locations")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
            raise ValueError("Each geolocated hop needs bounded location rows.")
        expected = hop_addresses(hop) or [None]
        if [row.get("address") if isinstance(row, dict) else False for row in rows] != expected:
            raise ValueError("Geolocation rows must match all observed hop addresses in order.")
        for row in rows:
            if (not isinstance(row, dict) or set(row) != FIELDS or row.get("approximate") is not True
                    or row.get("scope") != classify_address(row.get("address"))
                    or not isinstance(row.get("status"), str) or row["status"] not in {"located", "internal", "unavailable", "missing"}):
                raise ValueError("Invalid geolocation location row.")
            if row["status"] == "located":
                if row["scope"] != "public" or metadata["status"] != "available":
                    raise ValueError("Only public addresses in an available dataset may have locations.")
                if not _number(row["latitude"], -90, 90) or not _number(row["longitude"], -180, 180):
                    raise ValueError("Geolocation coordinates must be finite bounded numbers.")
                if any(row[key] is not None and not _text(row[key]) for key in ("city", "country")):
                    raise ValueError("Geolocation place names must be bounded text.")
                code = row["country_code"]
                if code is not None and (not isinstance(code, str) or re.fullmatch(r"[A-Z]{2}", code) is None):
                    raise ValueError("Invalid geolocation country code.")
            elif any(row[key] is not None for key in GEOGRAPHY):
                raise ValueError("Unavailable or non-public hops cannot contain geography.")
            if row["scope"] == "internal" and row["status"] != "internal":
                raise ValueError("Internal addresses need an internal topology label.")
            if row["scope"] == "special" and row["status"] != "unavailable" or row["scope"] == "public" and row["status"] not in {"located", "unavailable"}:
                raise ValueError("Geolocation status must agree with address scope.")
            if row["address"] is None and row["status"] != "missing" or row["status"] == "missing" and row["address"] is not None:
                raise ValueError("Missing geography is reserved for missing hop addresses.")
    return route


validate_geolocation = validate_route_geolocation
