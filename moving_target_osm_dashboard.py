#!/usr/bin/env python3
import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import math
import ipaddress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DEFAULT_TARGETS = [
    "https://www.apple.com/library/test/success.html",
    "https://cloudflare.com/cdn-cgi/trace",
    "https://www.google.com/generate_204",
]

TRACE_TARGET = "google.com"
DEFAULT_WIRELESS_ADB_HOST = "100.77.37.113"
DEFAULT_WIRELESS_ADB_PORT = 5555
DEFAULT_WIRELESS_ADB_SERIAL = f"{DEFAULT_WIRELESS_ADB_HOST}:{DEFAULT_WIRELESS_ADB_PORT}"
MAX_CORRECTION_DISTANCE_KM = 1.0
BROWSER_GPS_MAX_AGE_SECONDS = 60
REDIS_SAMPLE_HISTORY_COUNT = 5000
IN_MEMORY_SAMPLE_LIMIT = 10000
OSM_DOWNLOAD_DIR = Path("tools/osm_extracts")
OSM_COUNTRY_EXTRACTS = {
    "germany": "https://download.geofabrik.de/europe/germany-latest.osm.pbf",
    "france": "https://download.geofabrik.de/europe/france-latest.osm.pbf",
    "luxembourg": "https://download.geofabrik.de/europe/luxembourg-latest.osm.pbf",
    "switzerland": "https://download.geofabrik.de/europe/switzerland-latest.osm.pbf",
    "austria": "https://download.geofabrik.de/europe/austria-latest.osm.pbf",
    "belgium": "https://download.geofabrik.de/europe/belgium-latest.osm.pbf",
}
OSM_COUNTRY_BOUNDS = {
    "germany": (47.2, 5.8, 55.1, 15.1),
    "france": (41.0, -5.3, 51.3, 9.7),
    "luxembourg": (49.4, 5.7, 50.2, 6.6),
    "switzerland": (45.8, 5.9, 47.9, 10.6),
    "austria": (46.3, 9.5, 49.1, 17.2),
    "belgium": (49.4, 2.5, 51.6, 6.5),
}


STATE = {
    "samples": [],
    "last_location_sample": None,
    "browser_location": None,
    "motion_sensor": None,
    "wifi_label": None,
    "error_logs": [],
    "adb_serial_override": None,
    "planned_start_utc": None,
    "planned_arrival_utc": None,
    "settings": {
        "gps_formula": "raw",
        "use_adb_gps": True,
        "use_browser_gps": True,
        "use_accelerometer": False,
        "allow_interpolated_fallback": False,
        "selected_track_id": "berlin_karlsruhe",
        "provider": "",
        "modal_bg": "#ffffff",
        "panel_opacity": 0.78,
        "modal_opacity": 1.0,
        "summary_dock": "top-left",
        "details_dock": "top-right",
        "chart_dock": "bottom",
    },
    "redis": {"enabled": False, "ok": False, "message": "not configured"},
    "clients": [],
    "started_utc": None,
    "config": {},
    "status": {},
    "osm_downloads": {},
    "collection_enabled": True,
    "last_router_geo": None,
    "gcp_redis_plan": None,
}
STATE_LOCK = threading.Lock()

TRACK_CATALOG = [
    {
        "id": "berlin_karlsruhe",
        "name": "Berlin to Karlsruhe",
        "performed_count": 2,
        "duration_hours": 6,
        "path": [
            (52.5251, 13.3694),
            (52.3906, 13.0645),
            (52.1205, 11.6276),
            (51.3397, 12.3731),
            (50.9833, 11.0299),
            (50.1109, 8.6821),
            (49.4875, 8.4660),
            (49.0069, 8.4037),
        ],
    },
    {
        "id": "karlsruhe_luxembourg",
        "name": "Karlsruhe to Luxembourg",
        "performed_count": 2,
        "duration_hours": 6,
        "path": [
            (49.0069, 8.4037),
            (49.2402, 7.0000),
            (49.4431, 6.6380),
            (49.6116, 6.1319),
        ],
    },
]


def sorted_track_catalog():
    return sorted(TRACK_CATALOG, key=lambda t: (-int(t.get("performed_count", 0)), t["name"]))


def public_track_catalog():
    return [
        {
            "id": track["id"],
            "name": track["name"],
            "performed_count": track["performed_count"],
            "duration_hours": track["duration_hours"],
            "path": [[lat, lon] for lat, lon in track["path"]],
        }
        for track in sorted_track_catalog()
    ]


def get_track(track_id):
    tracks = {track["id"]: track for track in TRACK_CATALOG}
    return tracks.get(track_id) or sorted_track_catalog()[0]


def route_distance_and_fraction(route, lat, lon):
    if not route:
        return {"distance_km": None, "fraction": 0.0, "lat": lat, "lon": lon}
    ref_lat = math.radians(lat)
    km_per_deg_lat = 111.32
    km_per_deg_lon = max(0.001, 111.32 * math.cos(ref_lat))

    points = [((p[1] - lon) * km_per_deg_lon, (p[0] - lat) * km_per_deg_lat, p) for p in route]
    total = 0.0
    segments = []
    for idx in range(len(points) - 1):
        ax, ay, a = points[idx]
        bx, by, b = points[idx + 1]
        seg_len = math.hypot(bx - ax, by - ay)
        segments.append((idx, ax, ay, bx, by, a, b, seg_len))
        total += seg_len
    if not segments:
        return {"distance_km": haversine_km(lat, lon, route[0][0], route[0][1]), "fraction": 0.0, "lat": route[0][0], "lon": route[0][1]}

    best = None
    walked = 0.0
    for idx, ax, ay, bx, by, a, b, seg_len in segments:
        if seg_len <= 0:
            walked += seg_len
            continue
        t = max(0.0, min(1.0, (-(ax) * (bx - ax) + -(ay) * (by - ay)) / (seg_len ** 2)))
        px = ax + (bx - ax) * t
        py = ay + (by - ay) * t
        dist = math.hypot(px, py)
        candidate = {
            "distance_km": dist,
            "fraction": (walked + seg_len * t) / total if total else 0.0,
            "lat": a[0] + (b[0] - a[0]) * t,
            "lon": a[1] + (b[1] - a[1]) * t,
        }
        if best is None or candidate["distance_km"] < best["distance_km"]:
            best = candidate
        walked += seg_len
    return best or {"distance_km": None, "fraction": 0.0, "lat": lat, "lon": lon}


def real_location_from_sample(sample):
    raw = sample.get("raw_location")
    if raw and raw.get("lat") is not None and raw.get("lon") is not None:
        return raw
    loc = sample.get("location") or {}
    if loc.get("lat") is None or loc.get("lon") is None:
        return None
    if loc.get("source") in {"corrected_constant_speed", "interpolated"} or loc.get("corrected") or loc.get("interpolated"):
        return None
    return loc


def recent_real_locations(limit=10):
    with STATE_LOCK:
        samples = list(STATE["samples"])
    locs = []
    for sample in reversed(samples):
        loc = real_location_from_sample(sample)
        if loc:
            locs.append(loc)
        if len(locs) >= limit:
            break
    return list(reversed(locs))


def infer_track_from_recent_gps(default_track_id, current_loc=None, limit=10):
    locs = recent_real_locations(limit)
    if current_loc and current_loc.get("lat") is not None and current_loc.get("lon") is not None:
        locs.append(current_loc)
    if not locs:
        return get_track(default_track_id), None
    best = None
    for track in TRACK_CATALOG:
        distances = [
            route_distance_and_fraction(track["path"], float(loc["lat"]), float(loc["lon"]))["distance_km"]
            for loc in locs
        ]
        distances = [d for d in distances if d is not None]
        if not distances:
            continue
        score = sum(distances) / len(distances)
        candidate = {"track": track, "avg_distance_km": round(score, 4), "sample_count": len(distances)}
        if best is None or candidate["avg_distance_km"] < best["avg_distance_km"]:
            best = candidate
    if not best:
        return get_track(default_track_id), None
    return best["track"], {
        "selected_by": "last_10_gps_samples",
        "avg_distance_km": best["avg_distance_km"],
        "sample_count": best["sample_count"],
    }


def add_log(level, source, message, detail=None):
    entry = {
        "timestamp_utc": utc_now(),
        "level": level,
        "source": source,
        "message": str(message),
        "detail": detail,
    }
    with STATE_LOCK:
        STATE["error_logs"].append(entry)
        STATE["error_logs"] = STATE["error_logs"][-300:]
        config = dict(STATE.get("config") or {})
    if config.get("redis_url"):
        try:
            redis_client = RedisClient(config["redis_url"])
            redis_client.store_log(config.get("redis_prefix") or "moving_client_data", entry)
        except Exception:
            pass
    return entry


class RedisClient:
    def __init__(self, url):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("redis", ""):
            raise ValueError(f"unsupported Redis URL scheme: {parsed.scheme}")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 6379
        self.db = int((parsed.path or "/0").strip("/") or "0")
        self.password = urllib.parse.unquote(parsed.password) if parsed.password else None
        self.timeout = 1.5

    def _encode(self, *parts):
        out = [f"*{len(parts)}\r\n".encode("utf-8")]
        for part in parts:
            if isinstance(part, bytes):
                data = part
            else:
                data = str(part).encode("utf-8")
            out.append(f"${len(data)}\r\n".encode("utf-8"))
            out.append(data + b"\r\n")
        return b"".join(out)

    def _read_line(self, sock):
        chunks = []
        while True:
            ch = sock.recv(1)
            if not ch:
                raise ConnectionError("Redis closed connection")
            chunks.append(ch)
            if len(chunks) >= 2 and chunks[-2:] == [b"\r", b"\n"]:
                return b"".join(chunks[:-2]).decode("utf-8", errors="replace")

    def _read_response(self, sock):
        line = self._read_line(sock)
        if not line:
            return None
        kind, payload = line[0], line[1:]
        if kind == "+":
            return payload
        if kind == "-":
            raise RuntimeError(payload)
        if kind == ":":
            return int(payload)
        if kind == "$":
            size = int(payload)
            if size < 0:
                return None
            data = b""
            while len(data) < size + 2:
                data += sock.recv(size + 2 - len(data))
            return data[:size].decode("utf-8", errors="replace")
        if kind == "*":
            return [self._read_response(sock) for _ in range(int(payload))]
        return line

    def command(self, *parts):
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)
            if self.password:
                sock.sendall(self._encode("AUTH", self.password))
                self._read_response(sock)
            if self.db:
                sock.sendall(self._encode("SELECT", self.db))
                self._read_response(sock)
            sock.sendall(self._encode(*parts))
            return self._read_response(sock)

    def ping(self):
        return self.command("PING")

    def store_sample(self, prefix, sample):
        seq = sample.get("sequence")
        payload = json.dumps(sample, ensure_ascii=False)
        ts = sample.get("timestamp_utc", "")
        summary = sample.get("latency_summary") or {}
        motion = sample.get("motion") or {}
        loc = sample.get("location") or {}
        radio = sample.get("radio") or {}
        wifi = sample.get("wifi") or {}

        self.command("SET", f"{prefix}:sample:{seq}", payload)
        self.command("SET", f"{prefix}:latest", payload)
        list_key = f"{prefix}:samples"
        self.command("RPUSH", list_key, payload)
        self.command("LTRIM", list_key, -IN_MEMORY_SAMPLE_LIMIT, -1)
        self.command("XADD", f"{prefix}:stream", "*", "sequence", seq, "timestamp_utc", ts, "json", payload)

        index_fields = {
            "sequence": seq,
            "timestamp_utc": ts,
            "measurement_day": sample.get("measurement_day", ""),
            "latency_avg_ms": summary.get("avg_ms", ""),
            "latency_min_ms": summary.get("min_ms", ""),
        "latency_max_ms": summary.get("max_ms", ""),
            "speed_kmh": motion.get("speed_kmh", ""),
            "lat": loc.get("lat", ""),
            "lon": loc.get("lon", ""),
            "location_source": loc.get("source", ""),
            "coordinate_type": loc.get("coordinate_type", loc.get("source", "")),
            "provider": sample.get("provider", ""),
            "router_ip": (sample.get("router_geo") or {}).get("selected_public_hop", {}).get("ip", ""),
            "router_geo_lat": (sample.get("router_geo") or {}).get("geoip", {}).get("lat", ""),
            "router_geo_lon": (sample.get("router_geo") or {}).get("geoip", {}).get("lon", ""),
            "router_geo_org": (sample.get("router_geo") or {}).get("geoip", {}).get("org", ""),
            "operator": radio.get("operator_alpha", ""),
            "nr_pci": radio.get("nr_pci", ""),
            "nr_arfcn": radio.get("nr_arfcn", ""),
            "nr_rsrp": radio.get("nr_ss_rsrp", ""),
            "nr_rsrq": radio.get("nr_ss_rsrq", ""),
            "nr_sinr": radio.get("nr_ss_sinr", ""),
            "lte_pci": radio.get("lte_pci", ""),
            "lte_earfcn": radio.get("lte_earfcn", ""),
            "lte_rsrp": radio.get("lte_rsrp", ""),
            "lte_rsrq": radio.get("lte_rsrq", ""),
            "wifi_ssid": wifi.get("ssid", ""),
            "wifi_mac_address": wifi.get("mac_address", ""),
            "wifi_ip_address": wifi.get("ip_address", ""),
            "wifi_interface": wifi.get("interface", ""),
        }
        hset_parts = ["HSET", f"{prefix}:index:{seq}"]
        for key, value in index_fields.items():
            hset_parts.extend([key, value])
        self.command(*hset_parts)

        if loc.get("lat") is not None and loc.get("lon") is not None:
            self.command("GEOADD", f"{prefix}:geo", loc["lon"], loc["lat"], str(seq))

    def load_samples(self, prefix, count=500):
        rows = self.command("LRANGE", f"{prefix}:samples", -count, -1) or []
        samples = []
        for row in rows:
            try:
                samples.append(json.loads(row))
            except (TypeError, json.JSONDecodeError):
                continue
        deduped = {}
        for sample in samples:
            key = sample.get("timestamp_utc") or sample.get("sequence")
            if key is not None:
                deduped[key] = sample
        return sorted(deduped.values(), key=lambda s: (s.get("timestamp_utc") or "", s.get("sequence") or 0))

    def store_log(self, prefix, entry):
        payload = json.dumps(entry, ensure_ascii=False)
        level = str(entry.get("level", "")).lower()
        list_key = f"{prefix}:error_logs" if level in {"error", "warning"} else f"{prefix}:event_logs"
        stream_key = f"{prefix}:error_log_stream" if level in {"error", "warning"} else f"{prefix}:event_log_stream"
        self.command("RPUSH", list_key, payload)
        self.command("LTRIM", list_key, -IN_MEMORY_SAMPLE_LIMIT, -1)
        self.command(
            "XADD",
            stream_key,
            "*",
            "timestamp_utc",
            entry.get("timestamp_utc", ""),
            "level",
            entry.get("level", ""),
            "source",
            entry.get("source", ""),
            "message",
            entry.get("message", ""),
            "json",
            payload,
        )

    def load_logs(self, prefix, count=300):
        rows = []
        for key in (f"{prefix}:error_logs", f"{prefix}:event_logs"):
            rows.extend(self.command("LRANGE", key, -count, -1) or [])
        logs = []
        for row in rows:
            try:
                logs.append(json.loads(row))
            except (TypeError, json.JSONDecodeError):
                continue
        return sorted(logs, key=lambda log: log.get("timestamp_utc", ""))[-count:]

    def store_browser_location(self, prefix, loc):
        payload = json.dumps(loc, ensure_ascii=False)
        self.command("SET", f"{prefix}:browser_location:latest", payload)
        self.command("RPUSH", f"{prefix}:browser_locations", payload)
        self.command("XADD", f"{prefix}:browser_location_stream", "*", "timestamp_utc", loc.get("received_utc", ""), "json", payload)
        self.command("GEOADD", f"{prefix}:browser_location_geo", loc["lon"], loc["lat"], loc.get("received_utc", utc_now()))

    def load_browser_location(self, prefix):
        row = self.command("GET", f"{prefix}:browser_location:latest")
        if not row:
            return None
        try:
            return json.loads(row)
        except json.JSONDecodeError:
            return None


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run(cmd, timeout=8):
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except FileNotFoundError:
        return {"ok": False, "code": 127, "stdout": "", "stderr": f"missing command: {cmd[0]}"}
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return {"ok": False, "code": 124, "stdout": out.strip(), "stderr": f"timeout after {timeout}s"}


def is_public_ip(value):
    try:
        ip = ipaddress.ip_address(value)
        return ip.is_global
    except ValueError:
        return False


def dns_servers():
    result = run(["scutil", "--dns"], timeout=3)
    servers = []
    if result["ok"]:
        for line in result["stdout"].splitlines():
            match = re.search(r"nameserver\[\d+\]\s*:\s*([0-9a-fA-F:.]+)", line)
            if match and match.group(1) not in servers:
                servers.append(match.group(1))
    if not servers:
        result = run(["cat", "/etc/resolv.conf"], timeout=2)
        if result["ok"]:
            for line in result["stdout"].splitlines():
                match = re.search(r"^\s*nameserver\s+(\S+)", line)
                if match and match.group(1) not in servers:
                    servers.append(match.group(1))
    return servers


def parse_traceroute(output):
    hops = []
    for line in output.splitlines():
        match = re.match(r"\s*(\d+)\s+(.+)$", line)
        if not match:
            continue
        hop_no = int(match.group(1))
        rest = match.group(2)
        ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", rest)
        host = rest.split()[0] if rest.split() else ""
        rtts = [float(x) for x in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*ms", rest)]
        hops.append({
            "hop": hop_no,
            "host": host,
            "ips": ips,
            "public_ips": [ip for ip in ips if is_public_ip(ip)],
            "avg_rtt_ms": round(sum(rtts) / len(rtts), 1) if rtts else None,
            "raw": rest[:300],
        })
    return hops


def hop_display_name(hop):
    if not hop:
        return ""
    host = str(hop.get("host") or "").strip()
    if host and host != "*":
        return host
    ips = hop.get("ips") or []
    if ips:
        return ips[0]
    raw = str(hop.get("raw") or "").strip()
    return raw if raw and raw != "* * *" else ""


def geoip_lookup(ip):
    if not ip or not is_public_ip(ip):
        return None
    result = run(["curl", "-fsS", "--max-time", "5", f"https://ipinfo.io/{ip}/json"], timeout=7)
    if not result["ok"]:
        return {"ip": ip, "ok": False, "error": result["stderr"][-200:]}
    try:
        data = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return {"ip": ip, "ok": False, "error": "invalid geoip json"}
    loc = data.get("loc", "")
    lat = lon = None
    if "," in loc:
        try:
            lat_s, lon_s = loc.split(",", 1)
            lat = float(lat_s)
            lon = float(lon_s)
        except ValueError:
            pass
    return {
        "ip": ip,
        "ok": True,
        "city": data.get("city"),
        "region": data.get("region"),
        "country": data.get("country"),
        "loc": loc,
        "lat": lat,
        "lon": lon,
        "org": data.get("org"),
        "hostname": data.get("hostname"),
        "timezone": data.get("timezone"),
    }


def router_geo_sample(target=TRACE_TARGET):
    trace = run(["traceroute", "-m", "7", "-w", "1", target], timeout=10)
    hops = parse_traceroute(trace["stdout"])
    selected = None
    first_hop = hops[0] if hops else None
    for hop in hops:
        for ip in hop["public_ips"]:
            selected = {"hop": hop["hop"], "ip": ip, "host": hop.get("host"), "avg_rtt_ms": hop.get("avg_rtt_ms")}
            break
        if selected:
            break
    geo = geoip_lookup(selected["ip"]) if selected else None
    serving_node = None
    if selected:
        serving_node = {
            "name": selected.get("host") or selected.get("ip"),
            "ip": selected.get("ip"),
            "hop": selected.get("hop"),
            "avg_rtt_ms": selected.get("avg_rtt_ms"),
            "definition": "first public DNS name/IP hop in traceroute to public internet target",
        }
    return {
        "target": target,
        "ok": trace["ok"] or bool(hops),
        "first_hop": first_hop,
        "access_point_name": hop_display_name(first_hop),
        "access_point_definition": "first hop returned by traceroute",
        "selected_public_hop": selected,
        "serving_node": serving_node,
        "geoip": geo,
        "hops": hops,
        "stderr": trace["stderr"][-300:] if trace["stderr"] else "",
    }


def adb_shell(adb, serial, command, timeout=8):
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += ["shell", command]
    return run(cmd, timeout=timeout)


def adb_devices(adb):
    out = run([adb, "devices", "-l"], timeout=5)
    devices = []
    for line in out["stdout"].splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1], "line": line})
    return out, devices


def adb_connect_wireless(adb, host=DEFAULT_WIRELESS_ADB_HOST, port=DEFAULT_WIRELESS_ADB_PORT):
    serial = f"{host}:{port}"
    out = run([adb, "connect", serial], timeout=6)
    return {
        "ok": out["ok"] and "unable" not in out["stdout"].lower() and "failed" not in out["stdout"].lower(),
        "serial": serial,
        "stdout": out["stdout"].strip(),
        "stderr": out["stderr"].strip(),
        "code": out["code"],
    }


def parse_location(text):
    matches = re.findall(
        r"Location\[[^\]\n]*?\s(-?\d+\.\d+),\s*(-?\d+\.\d+)(?:[^\]\n]*?hAcc=([0-9.]+))?",
        text,
    )
    if not matches:
        return None
    lat, lon, acc = matches[-1]
    loc = {"lat": float(lat), "lon": float(lon)}
    if acc:
        loc["accuracy_m"] = float(acc)
    return loc


def first_regex(text, pattern):
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None


def parse_radio(registry, telephony, props):
    text = "\n".join([registry or "", telephony or "", props or ""])
    radio = {}
    patterns = {
        "operator_alpha": r"\[gsm\.operator\.alpha\]: \[(.*?)\]",
        "operator_numeric": r"\[gsm\.operator\.numeric\]: \[(.*?)\]",
        "network_type": r"\[gsm\.network\.type\]: \[(.*?)\]",
        "nr_state": r"mNrState=(\w+)",
        "data_network_type": r"mDataNetworkType=(\w+|[0-9]+)",
        "nr_pci": r"CellIdentityNr:.*?mPci\s*=\s*([0-9]+)",
        "nr_tac": r"CellIdentityNr:.*?mTac\s*=\s*([0-9]+)",
        "nr_arfcn": r"CellIdentityNr:.*?mNrArfcn\s*=\s*([0-9]+)",
        "nr_bands": r"CellIdentityNr:.*?mBands\s*=\s*(\[[^\]]*\])",
        "nr_nci": r"CellIdentityNr:.*?mNci\s*=\s*([0-9]+)",
        "nr_ss_rsrp": r"CellSignalStrengthNr:.*?ssRsrp\s*=\s*(-?[0-9]+)",
        "nr_ss_rsrq": r"CellSignalStrengthNr:.*?ssRsrq\s*=\s*(-?[0-9]+)",
        "nr_ss_sinr": r"CellSignalStrengthNr:.*?ssSinr\s*=\s*(-?[0-9]+)",
        "lte_ci": r"CellIdentityLte:.*?mCi\s*=\s*([0-9]+)",
        "lte_pci": r"CellIdentityLte:.*?mPci\s*=\s*([0-9]+)",
        "lte_tac": r"CellIdentityLte:.*?mTac\s*=\s*([0-9]+)",
        "lte_earfcn": r"CellIdentityLte:.*?mEarfcn\s*=\s*([0-9]+)",
        "lte_bands": r"CellIdentityLte:.*?mBands\s*=\s*(\[[^\]]*\])",
        "lte_rsrp": r"CellSignalStrengthLte:.*?rsrp\s*=\s*(-?[0-9]+)",
        "lte_rsrq": r"CellSignalStrengthLte:.*?rsrq\s*=\s*(-?[0-9]+)",
        "lte_rssnr": r"CellSignalStrengthLte:.*?rssnr\s*=\s*(-?[0-9]+)",
    }
    for key, pattern in patterns.items():
        value = first_regex(text, pattern)
        if value not in (None, ""):
            radio[key] = value
    return radio


def local_wifi_info(iface="en0"):
    info = {"interface": iface}
    ifconfig = run(["ifconfig", iface], timeout=3)
    if ifconfig["ok"]:
        mac = first_regex(ifconfig["stdout"], r"\bether\s+([0-9a-f:]{17})")
        ip = first_regex(ifconfig["stdout"], r"\binet\s+([0-9.]+)")
        status = first_regex(ifconfig["stdout"], r"\bstatus:\s+(\w+)")
        if mac:
            info["mac_address"] = mac
        if ip:
            info["ip_address"] = ip
        if status:
            info["status"] = status

    airport = run(["networksetup", "-getairportnetwork", iface], timeout=3)
    if airport["ok"]:
        ssid = first_regex(airport["stdout"], r"Current Wi-Fi Network:\s*(.+)")
        if ssid:
            info["ssid"] = ssid
    elif airport.get("stdout") or airport.get("stderr"):
        info["ssid_probe_error"] = (airport.get("stdout") or airport.get("stderr") or "")[-220:]

    profiler = run(["system_profiler", "SPAirPortDataType"], timeout=8)
    if profiler["ok"]:
        lines = profiler["stdout"].splitlines()
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped == f"{iface}:":
                for next_line in lines[idx + 1 : idx + 16]:
                    field = next_line.strip()
                    if not field or re.match(r"^[a-z0-9]+:$", field, re.I):
                        break
                    key, sep, value = field.partition(":")
                    if not sep:
                        continue
                    value = value.strip()
                    if key == "Status":
                        info.setdefault("status", value.lower())
                    elif key == "PHY Mode":
                        info["phy_mode"] = value
                    elif key == "Channel":
                        info["channel"] = value
                    elif key == "Country Code":
                        info["country_code"] = value
                    elif key == "Security":
                        info["security"] = value
                    elif key == "Signal / Noise":
                        values = [int(v) for v in re.findall(r"-?\d+", value)]
                        if values:
                            info["rssi_dbm"] = values[0]
                        if len(values) > 1:
                            info["noise_dbm"] = values[1]
                        if len(values) > 1:
                            info["snr_db"] = values[0] - values[1]
                    elif key == "Transmit Rate":
                        tx = first_regex(value, r"([0-9.]+)")
                        if tx:
                            try:
                                info["tx_rate_mbps"] = float(tx)
                            except ValueError:
                                info["tx_rate_mbps"] = tx
                break
        if not info.get("ssid"):
            for idx, line in enumerate(lines):
                if "Current Network Information:" in line:
                    for next_line in lines[idx + 1 : idx + 8]:
                        stripped = next_line.strip()
                        if stripped.endswith(":") and not stripped.startswith(("PHY Mode", "Channel", "Country", "Security", "Signal")):
                            info["ssid"] = stripped[:-1]
                            break
                    break
    else:
        info["wifi_metrics_error"] = (profiler.get("stderr") or profiler.get("stdout") or "")[-220:]

    if "rssi_dbm" not in info:
        info["rssi_status"] = "not exposed by current macOS permissions/interface state"

    if not info.get("ssid"):
        info["ssid"] = "unknown"
    return info


def curl_timing(url, timeout):
    curl = shutil.which("curl")
    if not curl:
        return {"url": url, "ok": False, "error": "curl not found"}
    fmt = (
        "http_code=%{http_code}\\n"
        "remote_ip=%{remote_ip}\\n"
        "time_namelookup=%{time_namelookup}\\n"
        "time_connect=%{time_connect}\\n"
        "time_appconnect=%{time_appconnect}\\n"
        "time_starttransfer=%{time_starttransfer}\\n"
        "time_total=%{time_total}\\n"
        "url_effective=%{url_effective}\\n"
    )
    res = run([curl, "-fsS", "--max-time", str(timeout), "-o", os.devnull, "-w", fmt, url], timeout=timeout + 2)
    fields = {"url": url, "ok": res["ok"]}
    if res["stderr"]:
        fields["stderr"] = res["stderr"][-300:]
    for line in res["stdout"].splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.startswith("time_"):
            try:
                fields[key] = float(value)
            except ValueError:
                fields[key] = value
        else:
            fields[key] = value
    if isinstance(fields.get("time_total"), float):
        fields["latency_ms"] = round(fields["time_total"] * 1000, 1)
    return fields


def latency_summary(results):
    values = [x.get("latency_ms") for x in results if x.get("ok") and isinstance(x.get("latency_ms"), (int, float))]
    if not values:
        return {}
    return {"min_ms": min(values), "avg_ms": round(sum(values) / len(values), 1), "max_ms": max(values)}


def connection_error_summary(results):
    total = len(results)
    failed = [x for x in results if not x.get("ok")]
    return {
        "target_count": total,
        "ok_count": total - len(failed),
        "error_count": len(failed),
        "error_rate_percent": round((len(failed) / total) * 100, 1) if total else 0,
        "errors": [
            {
                "url": x.get("url"),
                "http_code": x.get("http_code"),
                "stderr": x.get("stderr"),
                "error": x.get("error"),
            }
            for x in failed
        ],
    }


def haversine_km(lat1, lon1, lat2, lon2):
    radius_km = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def interpolate_route(route, fraction):
    fraction = max(0.0, min(1.0, fraction))
    segments = []
    total = 0.0
    for idx in range(len(route) - 1):
        a = route[idx]
        b = route[idx + 1]
        dist = haversine_km(a[0], a[1], b[0], b[1])
        segments.append((a, b, dist))
        total += dist
    if total <= 0:
        return {"lat": route[0][0], "lon": route[0][1]}
    target = total * fraction
    walked = 0.0
    for a, b, dist in segments:
        if walked + dist >= target:
            local = (target - walked) / dist if dist else 0.0
            return {
                "lat": a[0] + (b[0] - a[0]) * local,
                "lon": a[1] + (b[1] - a[1]) * local,
            }
        walked += dist
    return {"lat": route[-1][0], "lon": route[-1][1]}


def interpolate_location_for_sample(timestamp_utc, track_id=None):
    timestamp = dt.datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    track = get_track(track_id)
    with STATE_LOCK:
        start = STATE.get("planned_start_utc")
    if not start:
        start = timestamp
        with STATE_LOCK:
            STATE["planned_start_utc"] = start
    arrival = start + dt.timedelta(hours=float(track.get("duration_hours", 6)))
    total = max(1.0, (arrival - start).total_seconds())
    elapsed = max(0.0, (timestamp - start).total_seconds())
    fraction = elapsed / total
    loc = interpolate_route(track["path"], fraction)
    loc.update({
        "source": "interpolated",
        "interpolated": True,
        "track_id": track["id"],
        "track_name": track["name"],
        "route": track["name"],
        "assumption": f"constant velocity over selected {track.get('duration_hours', 6)} hour track",
        "fraction": round(max(0.0, min(1.0, fraction)), 4),
        "planned_arrival_utc": arrival.isoformat(timespec="seconds"),
    })
    return loc


def interpolated_location_for_track(timestamp_utc, track, fraction=None):
    if fraction is None:
        return interpolate_location_for_sample(timestamp_utc, track["id"])
    loc = interpolate_route(track["path"], fraction)
    loc.update({
        "source": "interpolated",
        "interpolated": True,
        "track_id": track["id"],
        "track_name": track["name"],
        "route": track["name"],
        "assumption": "projected from recent GPS samples onto selected track",
        "fraction": round(max(0.0, min(1.0, fraction)), 4),
    })
    return loc


def apply_position_correction(sample):
    settings = sample.get("settings") or {}
    formula = settings.get("gps_formula", "raw")
    selected_track_id = settings.get("selected_track_id") or "berlin_karlsruhe"
    raw = sample.get("location")
    if formula == "raw":
        if raw and raw.get("lat") is not None and raw.get("lon") is not None:
            raw["coordinate_type"] = raw.get("source", "raw")
            sample["position_error_km"] = None
            sample["position_correction"] = {"method": "raw", "applied": False}
        return

    inferred_track, inference = infer_track_from_recent_gps(selected_track_id, raw)
    corrected = interpolate_location_for_sample(sample["timestamp_utc"], inferred_track["id"])
    raw = sample.get("location")
    if formula in {"constant_speed_track", "constant_speed_to_karlsruhe"} and raw and raw.get("lat") is not None and raw.get("lon") is not None and not raw.get("interpolated"):
        projection = route_distance_and_fraction(inferred_track["path"], float(raw["lat"]), float(raw["lon"]))
        corrected = interpolated_location_for_track(sample["timestamp_utc"], inferred_track, projection.get("fraction"))
        position_error_km = round(projection.get("distance_km") or 0.0, 4)
        if position_error_km > MAX_CORRECTION_DISTANCE_KM:
            raw["coordinate_type"] = raw.get("source", "raw")
            sample["position_error_km"] = position_error_km
            sample["position_correction"] = {
                "method": f"constant-speed selected track: {corrected.get('track_name')}",
                "applied": False,
                "rejected": True,
                "reason": f"raw GPS is {position_error_km} km from predicted route, above {MAX_CORRECTION_DISTANCE_KM} km threshold",
                "track_id": corrected.get("track_id"),
                "track_name": corrected.get("track_name"),
                "track_inference": inference,
                "raw_source": raw.get("source", "gps"),
                "threshold_km": MAX_CORRECTION_DISTANCE_KM,
            }
            return
        sample["raw_location"] = raw
        sample["location"] = dict(corrected)
        sample["location"]["source"] = "corrected_constant_speed"
        sample["location"]["coordinate_type"] = "corrected"
        sample["location"]["corrected"] = True
        sample["position_error_km"] = position_error_km
        sample["position_correction"] = {
            "method": f"constant-speed selected track: {corrected.get('track_name')}",
            "applied": True,
            "track_id": corrected.get("track_id"),
            "track_name": corrected.get("track_name"),
            "track_inference": inference,
            "raw_source": raw.get("source", "gps"),
            "accelerometer_used": bool(settings.get("use_accelerometer") and sample.get("motion_sensor")),
            "note": "Browser accelerometer is recorded when available, but route correction uses the selected constant-speed track because accelerometer drift is too high without calibrated sensor fusion.",
        }
    elif formula in {"constant_speed_track", "constant_speed_to_karlsruhe"} and settings.get("allow_interpolated_fallback"):
        sample["location"] = dict(corrected)
        sample["location"]["source"] = "interpolated"
        sample["location"]["coordinate_type"] = "interpolated"
        sample["location"]["interpolated"] = True
        sample["position_error_km"] = None
        sample["position_correction"] = {
            "method": f"constant-speed selected track: {corrected.get('track_name')}",
            "applied": True,
            "track_id": corrected.get("track_id"),
            "track_name": corrected.get("track_name"),
            "track_inference": inference,
            "raw_source": None,
            "accelerometer_used": False,
        }
    elif formula in {"constant_speed_track", "constant_speed_to_karlsruhe"}:
        sample["position_error_km"] = None
        sample["position_correction"] = {
            "method": f"constant-speed selected track: {corrected.get('track_name')}",
            "applied": False,
            "rejected": True,
            "reason": "no GPS location available; sample remains unmatched until manually pinned or interpolated fallback is enabled",
            "track_id": corrected.get("track_id"),
            "track_name": corrected.get("track_name"),
            "track_inference": inference,
            "raw_source": None,
        }


def enrich_motion(sample):
    loc = sample.get("location")
    if not loc or "lat" not in loc or "lon" not in loc:
        sample["motion"] = {}
        return

    try:
        accuracy = float(loc.get("accuracy_m")) if loc.get("accuracy_m") is not None else None
    except (TypeError, ValueError):
        accuracy = None

    timestamp = dt.datetime.fromisoformat(sample["timestamp_utc"].replace("Z", "+00:00"))
    with STATE_LOCK:
        prev = STATE.get("last_location_sample")
        STATE["last_location_sample"] = {
            "timestamp": timestamp,
            "lat": loc["lat"],
            "lon": loc["lon"],
            "accuracy_m": accuracy,
        }

    if not prev:
        sample["motion"] = {"speed_kmh": None, "distance_from_previous_km": None}
        return

    seconds = max(0.001, (timestamp - prev["timestamp"]).total_seconds())
    dist = haversine_km(prev["lat"], prev["lon"], loc["lat"], loc["lon"])
    speed = round((dist / seconds) * 3600, 1)
    if accuracy is not None and accuracy > 250:
        sample["motion"] = {
            "speed_kmh": None,
            "distance_from_previous_km": round(dist, 4),
            "rejected": True,
            "reason": f"GPS accuracy too low: {accuracy:.0f} m",
        }
        return
    if speed > 380:
        sample["motion"] = {
            "speed_kmh": None,
            "distance_from_previous_km": round(dist, 4),
            "rejected": True,
            "reason": f"GPS jump rejected: {speed:.1f} km/h",
        }
        return
    sample["motion"] = {
        "speed_kmh": speed,
        "distance_from_previous_km": round(dist, 4),
    }


def choose_adb(adb_path, serial):
    adb = adb_path or shutil.which("adb")
    status = {
        "available": bool(adb),
        "path": adb,
        "selected_serial": serial,
        "wireless_serial": DEFAULT_WIRELESS_ADB_SERIAL,
    }
    if not adb:
        status["message"] = "adb not found; GPS and radio fields unavailable."
        return None, None, status
    out, devices = adb_devices(adb)
    ready = [d for d in devices if d["state"] == "device"]
    if not ready and not serial:
        connect_result = adb_connect_wireless(adb)
        status["wireless_connect"] = connect_result
        out, devices = adb_devices(adb)
        ready = [d for d in devices if d["state"] == "device"]
    status["devices"] = devices
    if not serial:
        wireless_ready = [d for d in ready if d["serial"] == DEFAULT_WIRELESS_ADB_SERIAL]
        if wireless_ready:
            serial = wireless_ready[0]["serial"]
        elif ready:
            serial = ready[0]["serial"]
    status["selected_serial"] = serial
    if not serial:
        status["message"] = f"No authorized ADB device; tried wireless {DEFAULT_WIRELESS_ADB_SERIAL}. Unlock phone and approve debugging."
    return adb, serial, status


def collect_one(config, adb, serial, adb_status):
    wifi = local_wifi_info()
    with STATE_LOCK:
        wifi_label = STATE.get("wifi_label")
    if wifi_label:
        wifi["ssid"] = wifi_label
        wifi["ssid_source"] = "manual"
    with STATE_LOCK:
        settings = dict(STATE.get("settings") or {})
    timestamp_utc = utc_now()

    sample = {
        "timestamp_utc": timestamp_utc,
        "measurement_day": timestamp_utc[:10],
        "sequence": None,
        "location": None,
        "radio": {},
        "wifi": wifi,
        "adb": adb_status,
        "dns_servers": dns_servers(),
        "settings": settings,
        "provider": settings.get("provider") or "",
        "latency": [],
    }

    if settings.get("use_adb_gps", True) and adb and serial:
        loc_out = adb_shell(adb, serial, "dumpsys location", timeout=8)
        sample["location"] = parse_location(loc_out["stdout"])
        if sample["location"] and sample["location"].get("accuracy_m") is not None:
            sample["location"]["gps_accuracy_m"] = sample["location"].get("accuracy_m")
        sample["location_status"] = {"ok": loc_out["ok"], "stderr": loc_out["stderr"][-250:]}

        props_cmd = "getprop | grep -E 'gsm\\.|ril\\.|ro\\.product|ro\\.build.version.release'"
        props_out = adb_shell(adb, serial, props_cmd, timeout=5)
        reg_out = adb_shell(adb, serial, "dumpsys telephony.registry", timeout=8)
        tel_out = adb_shell(adb, serial, "dumpsys telephony", timeout=10)
        sample["radio"] = parse_radio(reg_out["stdout"], tel_out["stdout"], props_out["stdout"])
        sample["radio_status"] = {
            "telephony_registry_ok": reg_out["ok"],
            "telephony_ok": tel_out["ok"],
            "props_ok": props_out["ok"],
            "stderr": " | ".join(x for x in [reg_out["stderr"], tel_out["stderr"], props_out["stderr"]] if x)[-400:],
        }

    if settings.get("use_browser_gps", True) and not sample["location"]:
        with STATE_LOCK:
            browser_loc = STATE.get("browser_location")
        if browser_loc:
            try:
                received = dt.datetime.fromisoformat(str(browser_loc.get("received_utc")).replace("Z", "+00:00"))
                age_seconds = (dt.datetime.now(dt.timezone.utc) - received).total_seconds()
            except (TypeError, ValueError):
                age_seconds = BROWSER_GPS_MAX_AGE_SECONDS + 1
            if age_seconds <= BROWSER_GPS_MAX_AGE_SECONDS:
                sample["location"] = {
                    "lat": browser_loc["lat"],
                    "lon": browser_loc["lon"],
                    "accuracy_m": browser_loc.get("accuracy_m"),
                    "gps_accuracy_m": browser_loc.get("gps_accuracy_m", browser_loc.get("accuracy_m")),
                    "source": "browser",
                    "received_utc": browser_loc.get("received_utc"),
                    "age_seconds": round(age_seconds, 1),
                    "latency": browser_loc.get("latency") or [],
                    "latency_summary": browser_loc.get("latency_summary") or {},
                }
                sample["location_status"] = {"ok": True, "source": "browser-geolocation", "age_seconds": round(age_seconds, 1)}
            else:
                sample["location_status"] = {
                    "ok": False,
                    "source": "browser-geolocation",
                    "reason": f"stale browser GPS ignored: {age_seconds:.1f}s old",
                    "max_age_seconds": BROWSER_GPS_MAX_AGE_SECONDS,
                }

    if settings.get("allow_interpolated_fallback") and not sample["location"]:
        sample["location"] = interpolate_location_for_sample(sample["timestamp_utc"], settings.get("selected_track_id"))
        sample["location_status"] = {"ok": True, "source": "interpolated"}

    with STATE_LOCK:
        motion_sensor = STATE.get("motion_sensor")
    if motion_sensor:
        sample["motion_sensor"] = motion_sensor

    apply_position_correction(sample)

    for url in config["targets"]:
        timing = curl_timing(url, config["timeout"])
        timing["wifi_context"] = {
            "ssid": wifi.get("ssid"),
            "interface": wifi.get("interface"),
            "rssi_dbm": wifi.get("rssi_dbm"),
            "noise_dbm": wifi.get("noise_dbm"),
            "snr_db": wifi.get("snr_db"),
            "tx_rate_mbps": wifi.get("tx_rate_mbps"),
            "channel": wifi.get("channel"),
            "phy_mode": wifi.get("phy_mode"),
        }
        sample["latency"].append(timing)
        time.sleep(config["per_target_pause"])
    sample["latency_summary"] = latency_summary(sample["latency"])
    sample["connection_errors"] = connection_error_summary(sample["latency"])
    if config.get("trace_interval", 0) > 0:
        now = time.time()
        with STATE_LOCK:
            last_trace = STATE.get("last_trace_time", 0)
            last_router_geo = STATE.get("last_router_geo")
        if now - last_trace >= config["trace_interval"]:
            sample["router_geo"] = router_geo_sample(config.get("trace_target") or TRACE_TARGET)
            if not sample.get("provider"):
                geo = (sample.get("router_geo") or {}).get("geoip") or {}
                hop = (sample.get("router_geo") or {}).get("selected_public_hop") or {}
                sample["provider"] = geo.get("org") or hop.get("host") or (sample.get("dns_servers") or [""])[0]
            with STATE_LOCK:
                STATE["last_trace_time"] = now
                STATE["last_router_geo"] = sample["router_geo"]
        elif last_router_geo:
            sample["router_geo"] = last_router_geo
    return sample


def redis_status(redis_client):
    if not redis_client:
        return {"enabled": False, "ok": False, "message": "not configured"}
    try:
        pong = redis_client.ping()
        return {"enabled": True, "ok": pong == "PONG", "message": f"Redis {pong}", "url": f"redis://{redis_client.host}:{redis_client.port}/{redis_client.db}"}
    except Exception as exc:
        return {"enabled": True, "ok": False, "message": str(exc), "url": f"redis://{redis_client.host}:{redis_client.port}/{redis_client.db}"}


def append_sample(sample, log_path, redis_client=None, redis_prefix="moving_client_data"):
    enrich_motion(sample)
    with STATE_LOCK:
        max_seq = max([s.get("sequence") or 0 for s in STATE["samples"]] or [0])
        sample["sequence"] = max_seq + 1
        STATE["samples"].append(sample)
        STATE["samples"] = STATE["samples"][-IN_MEMORY_SAMPLE_LIMIT:]
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    if redis_client:
        try:
            redis_client.store_sample(redis_prefix, sample)
            with STATE_LOCK:
                STATE["redis"] = {
                    "enabled": True,
                    "ok": True,
                    "message": "stored latest sample",
                    "prefix": redis_prefix,
                    "url": f"redis://{redis_client.host}:{redis_client.port}/{redis_client.db}",
                }
        except Exception as exc:
            with STATE_LOCK:
                STATE["redis"] = {
                    "enabled": True,
                    "ok": False,
                    "message": str(exc),
                    "prefix": redis_prefix,
                    "url": f"redis://{redis_client.host}:{redis_client.port}/{redis_client.db}",
                }


def restore_from_redis(redis_client, redis_prefix):
    if not redis_client:
        return {"restored_samples": 0, "restored_browser_location": False}
    restored_samples = []
    restored_location = None
    restored_logs = []
    try:
        restored_samples = redis_client.load_samples(redis_prefix, count=REDIS_SAMPLE_HISTORY_COUNT)
        restored_location = redis_client.load_browser_location(redis_prefix)
        restored_logs = redis_client.load_logs(redis_prefix, count=300)
    except Exception as exc:
        with STATE_LOCK:
            STATE["redis"] = {
                "enabled": True,
                "ok": False,
                "message": f"restore failed: {exc}",
                "prefix": redis_prefix,
                "url": f"redis://{redis_client.host}:{redis_client.port}/{redis_client.db}",
            }
        return {"restored_samples": 0, "restored_browser_location": False}

    last_loc = None
    for sample in restored_samples:
        loc = sample.get("location") or {}
        if loc.get("lat") is not None and loc.get("lon") is not None:
            try:
                timestamp = dt.datetime.fromisoformat(sample["timestamp_utc"].replace("Z", "+00:00"))
                last_loc = {"timestamp": timestamp, "lat": loc["lat"], "lon": loc["lon"], "accuracy_m": loc.get("accuracy_m")}
            except Exception:
                pass

    with STATE_LOCK:
        STATE["samples"] = restored_samples[-IN_MEMORY_SAMPLE_LIMIT:]
        if last_loc:
            STATE["last_location_sample"] = last_loc
        if restored_location:
            STATE["browser_location"] = restored_location
        if restored_logs:
            STATE["error_logs"] = restored_logs[-300:]
    return {"restored_samples": len(restored_samples), "restored_browser_location": bool(restored_location), "restored_logs": len(restored_logs)}


def parse_query_time_range(query):
    text = (query or "").lower()
    now = dt.datetime.now(dt.timezone.utc)
    local_now = dt.datetime.now().astimezone()
    if "last 6 months" in text or "last six months" in text:
        start_local = local_now - dt.timedelta(days=183)
        return start_local.astimezone(dt.timezone.utc), local_now.astimezone(dt.timezone.utc)
    if "last month" in text:
        first_this_month = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_prev_month = first_this_month - dt.timedelta(seconds=1)
        start_prev_month = last_prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start_prev_month.astimezone(dt.timezone.utc), last_prev_month.astimezone(dt.timezone.utc)
    if "today" in text:
        start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_local.astimezone(dt.timezone.utc), local_now.astimezone(dt.timezone.utc)
    if "yesterday" in text:
        start_local = (local_now - dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local.replace(hour=23, minute=59, second=59)
        return start_local.astimezone(dt.timezone.utc), end_local.astimezone(dt.timezone.utc)
    iso_matches = re.findall(r"\d{4}-\d{2}-\d{2}[T ][0-9:]{5,8}(?:Z|[+-]\d{2}:?\d{2})?", query or "")
    parsed = []
    for value in iso_matches[:2]:
        try:
            parsed.append(dt.datetime.fromisoformat(value.replace(" ", "T").replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(parsed) >= 2:
        return parsed[0].astimezone(dt.timezone.utc), parsed[1].astimezone(dt.timezone.utc)
    return None, None


def execute_redis_log_query(config, query):
    started = time.perf_counter()
    redis_url = config.get("redis_url")
    if not redis_url:
        return {"ok": False, "error": "Redis is not configured"}
    redis_client = RedisClient(redis_url)
    redis_prefix = config.get("redis_prefix") or "moving_client_data"
    logs = redis_client.load_logs(redis_prefix, count=1000)
    start, end = parse_query_time_range(query)
    text = (query or "").lower()
    include_errors = any(token in text for token in ["error", "errors", "warning", "warnings", "error_logs"])
    include_events = any(token in text for token in ["event", "events", "application", "application_events", "event_logs"])
    if not include_errors and not include_events:
        include_errors = True
        include_events = True

    filtered = []
    for log in logs:
        level = str(log.get("level", "")).lower()
        is_error = level in {"error", "warning"}
        if is_error and not include_errors:
            continue
        if not is_error and not include_events:
            continue
        try:
            ts = dt.datetime.fromisoformat(str(log.get("timestamp_utc", "")).replace("Z", "+00:00"))
        except ValueError:
            ts = None
        if start and ts and ts < start:
            continue
        if end and ts and ts > end:
            continue
        filtered.append(log)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "ok": True,
        "logs": filtered[-300:],
        "count": len(filtered[-300:]),
        "execution_ms": elapsed_ms,
        "range": {
            "start": start.isoformat(timespec="seconds") if start else None,
            "end": end.isoformat(timespec="seconds") if end else None,
        },
        "redis_keys": [f"{redis_prefix}:error_logs", f"{redis_prefix}:event_logs"],
    }


def build_gcp_redis_plan(payload):
    project = str(payload.get("project", "")).strip()
    region = str(payload.get("region", "europe-west1")).strip() or "europe-west1"
    instance = str(payload.get("instance", "moving-target-redis")).strip() or "moving-target-redis"
    tier = str(payload.get("tier", "basic")).strip().lower()
    memory_gb = str(payload.get("memory_gb", "1")).strip() or "1"
    network = str(payload.get("network", "default")).strip() or "default"
    prefix = str(payload.get("redis_prefix", "moving_client_data")).strip() or "moving_client_data"

    if not re.match(r"^[a-z][a-z0-9-]{2,39}$", instance):
        return {"ok": False, "error": "Instance name must be 3-40 chars, lowercase letters, numbers, and hyphens, starting with a letter."}
    if tier not in {"basic", "standard"}:
        return {"ok": False, "error": "Tier must be basic or standard."}
    try:
        memory_value = int(memory_gb)
    except ValueError:
        return {"ok": False, "error": "Memory size must be an integer GB value."}
    if memory_value < 1 or memory_value > 300:
        return {"ok": False, "error": "Memory size must be between 1 and 300 GB."}

    project_arg = f" --project={project}" if project else ""
    commands = [
        (
            f"gcloud redis instances create {instance}"
            f"{project_arg} --region={region} --tier={tier.upper()}"
            f" --size={memory_value} --network={network} --redis-version=redis_7_0"
        ),
        f"gcloud redis instances describe {instance}{project_arg} --region={region} --format='get(host,port)'",
        "export MOVING_TARGET_REDIS_URL='redis://<HOST>:<PORT>/0'",
        f"./moving_target_osm_dashboard.py --redis-url \"$MOVING_TARGET_REDIS_URL\" --redis-prefix {prefix}",
    ]
    return {
        "ok": True,
        "preview_only": True,
        "provider": "gcp-memorystore-redis",
        "project": project,
        "region": region,
        "instance": instance,
        "tier": tier,
        "memory_gb": memory_value,
        "network": network,
        "redis_prefix": prefix,
        "commands": commands,
        "note": "Creation is held as a backend plan only. The Python app does not execute gcloud or create billable GCP resources.",
    }


def start_osm_country_download(country):
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(country or "").strip().lower()).strip("-")
    if not normalized:
        return {"ok": False, "error": "country required"}
    url = OSM_COUNTRY_EXTRACTS.get(normalized)
    if not url:
        return {
            "ok": False,
            "error": f"unsupported country '{country}'",
            "supported": sorted(OSM_COUNTRY_EXTRACTS),
        }
    OSM_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = OSM_DOWNLOAD_DIR / f"{normalized}-latest.osm.pbf"
    absolute_target = target.resolve()
    existing_bytes = target.stat().st_size if target.exists() else 0
    with STATE_LOCK:
        current = STATE["osm_downloads"].get(normalized) or {}
        if current.get("status") == "downloading":
            return {"ok": True, **current}
        STATE["osm_downloads"][normalized] = {
            "status": "downloading",
            "country": normalized,
            "url": url,
            "path": str(absolute_target),
            "bytes": existing_bytes,
            "total_bytes": None,
            "percent": None,
            "resumed_from_bytes": existing_bytes,
            "started_utc": utc_now(),
        }

    def worker():
        try:
            resume_from = target.stat().st_size if target.exists() else 0
            req = urllib.request.Request(url)
            if resume_from > 0:
                req.add_header("Range", f"bytes={resume_from}-")
            with urllib.request.urlopen(req, timeout=30) as response:
                total = response.headers.get("Content-Length")
                content_range = response.headers.get("Content-Range")
                try:
                    total_bytes = int(total) + resume_from if total else None
                except ValueError:
                    total_bytes = None
                if content_range:
                    match = re.search(r"/(\d+)$", content_range)
                    if match:
                        total_bytes = int(match.group(1))
                if response.status == 200 and resume_from:
                    resume_from = 0
                mode = "ab" if resume_from else "wb"
                downloaded = resume_from
                with target.open(mode) as outf:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        outf.write(chunk)
                        downloaded += len(chunk)
                        progress = {
                            "status": "downloading",
                            "country": normalized,
                            "url": url,
                            "path": str(absolute_target),
                            "bytes": downloaded,
                            "total_bytes": total_bytes,
                            "percent": round(downloaded / total_bytes * 100, 1) if total_bytes else None,
                            "resumed_from_bytes": resume_from,
                            "started_utc": STATE["osm_downloads"].get(normalized, {}).get("started_utc"),
                        }
                        with STATE_LOCK:
                            STATE["osm_downloads"][normalized] = progress
            size = target.stat().st_size if target.exists() else 0
            status = {"status": "complete", "country": normalized, "url": url, "path": str(absolute_target), "bytes": size, "total_bytes": size, "percent": 100.0, "finished_utc": utc_now()}
            add_log("info", "osm-download", f"Downloaded OSM extract for {normalized}", status)
        except Exception as exc:
            status = {"status": "failed", "country": normalized, "url": url, "path": str(absolute_target), "error": str(exc), "finished_utc": utc_now()}
            add_log("error", "osm-download", f"OSM extract download failed for {normalized}", status)
        with STATE_LOCK:
            STATE["osm_downloads"][normalized] = status

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "status": "downloading", "country": normalized, "url": url, "path": str(absolute_target), "bytes": existing_bytes, "resumed_from_bytes": existing_bytes}


def osm_storage_status():
    OSM_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    total_bytes = 0
    for path in sorted(OSM_DOWNLOAD_DIR.glob("*.osm.pbf")):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total_bytes += size
        files.append({"name": path.name, "path": str(path.resolve()), "bytes": size})
    with STATE_LOCK:
        downloads = dict(STATE.get("osm_downloads") or {})
    return {
        "ok": True,
        "directory": str(OSM_DOWNLOAD_DIR.resolve()),
        "total_bytes": total_bytes,
        "files": files,
        "downloads": downloads,
    }


def downloaded_osm_countries():
    countries = set()
    for path in OSM_DOWNLOAD_DIR.glob("*-latest.osm.pbf"):
        country = path.name.replace("-latest.osm.pbf", "")
        if path.exists() and path.stat().st_size > 0:
            countries.add(country)
    with STATE_LOCK:
        for country, status in (STATE.get("osm_downloads") or {}).items():
            if status.get("status") == "complete":
                countries.add(country)
    return countries


def country_for_point(lat, lon):
    for country, (min_lat, min_lon, max_lat, max_lon) in OSM_COUNTRY_BOUNDS.items():
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return country
    return None


def osm_consistency_check(config):
    downloaded = downloaded_osm_countries()
    redis_info = {"enabled": False}
    samples = []
    if config.get("redis_url"):
        try:
            redis_client = RedisClient(config["redis_url"])
            samples = redis_client.load_samples(config.get("redis_prefix") or "moving_client_data", count=REDIS_SAMPLE_HISTORY_COUNT)
            redis_info = {"enabled": True, "ok": True, "sample_count": len(samples)}
        except Exception as exc:
            redis_info = {"enabled": True, "ok": False, "error": str(exc)}
    if not samples:
        with STATE_LOCK:
            samples = list(STATE.get("samples") or [])

    gps_samples = []
    matched = []
    unmatched = []
    missing_countries = set()
    outside_supported = 0
    for sample in samples:
        loc = sample.get("location") or {}
        if loc.get("lat") is None or loc.get("lon") is None:
            continue
        try:
            lat = float(loc["lat"])
            lon = float(loc["lon"])
        except (TypeError, ValueError):
            continue
        country = country_for_point(lat, lon)
        row = {
            "sequence": sample.get("sequence"),
            "timestamp_utc": sample.get("timestamp_utc"),
            "lat": lat,
            "lon": lon,
            "country": country,
        }
        gps_samples.append(row)
        if country and country in downloaded:
            matched.append(row)
        else:
            unmatched.append(row)
            if country:
                missing_countries.add(country)
            else:
                outside_supported += 1

    lines = [
        f"Redis samples read: {len(samples)}" if redis_info.get("enabled") else f"In-memory samples read: {len(samples)}",
        f"GPS samples: {len(gps_samples)}",
        f"Matched by downloaded country extract: {len(matched)}",
        f"Unmatched: {len(unmatched)}",
        f"Downloaded countries: {', '.join(sorted(downloaded)) or 'none'}",
        f"Countries not yet downloaded: {', '.join(sorted(missing_countries)) or 'none'}",
        f"Outside supported country bounds: {outside_supported}",
    ]
    if redis_info.get("enabled") and not redis_info.get("ok"):
        lines.append(f"Redis read error: {redis_info.get('error')}")
    return {
        "ok": True,
        "redis": redis_info,
        "sample_count": len(samples),
        "gps_sample_count": len(gps_samples),
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "downloaded_countries": sorted(downloaded),
        "missing_countries": sorted(missing_countries),
        "outside_supported_country_bounds": outside_supported,
        "report": "\n".join(lines),
    }


def kml_escape(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def samples_to_kml(samples):
    point_marks = []
    coord_lines = []
    raw_coord_lines = []
    for sample in samples:
        loc = sample.get("location") or {}
        raw = sample.get("raw_location") or {}
        if loc.get("lat") is None or loc.get("lon") is None:
            continue
        seq = sample.get("sequence")
        summary = sample.get("latency_summary") or {}
        motion = sample.get("motion") or {}
        coord_lines.append(f"{loc['lon']},{loc['lat']},0")
        if raw.get("lat") is not None and raw.get("lon") is not None:
            raw_coord_lines.append(f"{raw['lon']},{raw['lat']},0")
        description = (
            f"Latency avg: {summary.get('avg_ms')} ms\n"
            f"Latency min/max: {summary.get('min_ms')} / {summary.get('max_ms')} ms\n"
            f"Speed: {motion.get('speed_kmh')} km/h\n"
            f"Latitude: {loc.get('lat')}\n"
            f"Longitude: {loc.get('lon')}\n"
            f"Coordinate type: {loc.get('coordinate_type') or loc.get('source')}\n"
            f"Track ID: {loc.get('track_id') or (sample.get('position_correction') or {}).get('track_id')}\n"
            f"Track name: {loc.get('track_name') or (sample.get('position_correction') or {}).get('track_name')}\n"
            f"Provider: {sample.get('provider')}\n"
            f"Measurement day: {sample.get('measurement_day')}\n"
            f"Location source: {loc.get('source')}\n"
            f"Raw GPS: {raw.get('lat')}, {raw.get('lon')}\n"
            f"Position error km: {sample.get('position_error_km')}\n"
        )
        point_marks.append(f"""
    <Placemark>
      <name>Sample {kml_escape(seq)}</name>
      <description>{kml_escape(description)}</description>
      <Point><coordinates>{loc['lon']},{loc['lat']},0</coordinates></Point>
    </Placemark>""")
    exported_line = "\n".join(coord_lines)
    raw_line = "\n".join(raw_coord_lines)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Moving Target latency samples</name>
    <Style id="exportedRoute"><LineStyle><color>ff7d5de5</color><width>4</width></LineStyle></Style>
    <Style id="rawRoute"><LineStyle><color>ff00aaff</color><width>2</width></LineStyle></Style>
    <Placemark>
      <name>Exported sample route</name>
      <styleUrl>#exportedRoute</styleUrl>
      <LineString><tessellate>1</tessellate><coordinates>{exported_line}</coordinates></LineString>
    </Placemark>
    <Placemark>
      <name>Raw GPS route</name>
      <styleUrl>#rawRoute</styleUrl>
      <LineString><tessellate>1</tessellate><coordinates>{raw_line}</coordinates></LineString>
    </Placemark>
    {''.join(point_marks)}
  </Document>
</kml>
"""


def collector_loop(config):
    log_path = Path(config["log"])
    redis_client = RedisClient(config["redis_url"]) if config.get("redis_url") else None
    redis_prefix = config.get("redis_prefix") or "moving_client_data"
    active_redis_url = config.get("redis_url")
    restore_info = restore_from_redis(redis_client, redis_prefix)
    adb, serial, adb_status = choose_adb(config.get("adb"), config.get("serial"))
    with STATE_LOCK:
        STATE["started_utc"] = utc_now()
        STATE["config"] = config
        STATE["redis"] = redis_status(redis_client)
        if redis_client and STATE["redis"].get("ok"):
            STATE["redis"]["message"] = (
                f"Redis PONG; restored {restore_info['restored_samples']} samples"
                + (" and browser GPS" if restore_info["restored_browser_location"] else "")
                + (f" and {restore_info.get('restored_logs', 0)} logs" if restore_info.get("restored_logs") else "")
            )
            STATE["redis"]["prefix"] = redis_prefix
        STATE["status"] = {"adb": adb_status, "log": str(log_path), "redis": STATE["redis"]}

    probe_count = 0
    while True:
        started = time.time()
        with STATE_LOCK:
            collection_enabled = bool(STATE.get("collection_enabled", True))
        if not collection_enabled:
            time.sleep(max(0.5, min(2.0, config["interval"])))
            continue
        if not serial or probe_count % 4 == 0:
            with STATE_LOCK:
                selected_serial = STATE.get("adb_serial_override") or config.get("serial")
            adb, serial, adb_status = choose_adb(config.get("adb"), selected_serial)
            with STATE_LOCK:
                STATE["status"] = {"adb": adb_status, "log": str(log_path), "redis": STATE["redis"]}
        with STATE_LOCK:
            runtime_config = dict(STATE.get("config") or {})
        runtime_redis_url = runtime_config.get("redis_url")
        runtime_redis_prefix = runtime_config.get("redis_prefix") or "moving_client_data"
        if runtime_redis_url != active_redis_url or runtime_redis_prefix != redis_prefix:
            active_redis_url = runtime_redis_url
            redis_prefix = runtime_redis_prefix
            redis_client = RedisClient(active_redis_url) if active_redis_url else None
            with STATE_LOCK:
                STATE["redis"] = redis_status(redis_client)
                if redis_client:
                    STATE["redis"]["prefix"] = redis_prefix
                STATE["status"] = {"adb": adb_status, "log": str(log_path), "redis": STATE["redis"]}
        sample = collect_one(config, adb, serial, adb_status)
        append_sample(sample, log_path, redis_client=redis_client, redis_prefix=redis_prefix)
        probe_count += 1
        elapsed = time.time() - started
        time.sleep(max(0.1, config["interval"] - elapsed))


def page_html():
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Moving Target OSM Dashboard</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map { height: 100%; margin: 0; }
    :root {
      --panel-bg: rgba(255,255,255,.95);
      --summary-bg: rgba(255,255,255,.78);
      --modal-card-bg: rgba(255,255,255,1);
    }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2933; }
    #map { background: #eef2f3; }
    .topbar {
      position: absolute; z-index: 1000; inset: 0; pointer-events: none;
    }
    .panel {
      pointer-events: auto; background: var(--panel-bg); border: 1px solid #c8d0d7;
      border-radius: 6px; box-shadow: 0 2px 10px rgba(25,35,45,.18); padding: 10px 12px;
    }
    #summaryPanel {
      position: absolute; top: 12px; left: 12px; width: min(760px, calc(100vw - 24px));
      background: var(--summary-bg); backdrop-filter: blur(4px);
    }
    #detailsPanel { position: absolute; top: 12px; right: 12px; max-height: calc(100vh - 24px); overflow-y: auto; }
    .dock-top-left { top: 12px !important; left: 12px !important; right: auto !important; bottom: auto !important; }
    .dock-top-right { top: 12px !important; right: 12px !important; left: auto !important; bottom: auto !important; }
    .dock-left { top: 96px !important; left: 12px !important; right: auto !important; bottom: auto !important; }
    .dock-right { top: 96px !important; right: 12px !important; left: auto !important; bottom: auto !important; }
    .dock-bottom {
      left: 12px !important; right: 12px !important; bottom: 12px !important; top: auto !important;
      width: auto !important; max-width: none !important;
    }
    .panel-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    h1 { font-size: 15px; margin: 0 0 8px; }
    .panel.collapsed .collapsible-body { display: none; }
    .metrics { display: grid; grid-template-columns: repeat(6, minmax(84px, 1fr)); gap: 8px; }
    .metric { border-left: 3px solid #557a95; padding-left: 8px; min-width: 0; }
    .label { font-size: 11px; color: #52616f; }
    .value { font-size: 16px; font-weight: 650; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .coordinate-value { font-size: 13px; font-variant-numeric: tabular-nums; white-space: normal; line-height: 1.2; }
    .geo-name { display: block; margin-top: 2px; font-size: 10px; line-height: 1.15; color: #52616f; font-weight: 500; }
    .gps-value {
      display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5px 8px;
      white-space: normal; overflow: visible; text-overflow: clip; font-size: 12px; font-weight: 500;
    }
    .gps-item-label {
      display: block; font-size: 10px; line-height: 1.15; color: #52616f; font-weight: 600;
      text-transform: uppercase;
    }
    .gps-item-number {
      display: block; margin-top: 2px; font-size: 13px; line-height: 1.2; color: #1f2933;
      font-variant-numeric: tabular-nums;
    }
    .gps-meta { grid-column: 1 / -1; font-size: 11px; line-height: 1.2; color: #52616f; font-weight: 500; }
    .side { width: 320px; max-width: 35vw; }
    .small { font-size: 12px; line-height: 1.35; color: #3a4750; }
    .legend { display: grid; grid-template-columns: repeat(5, auto); gap: 7px; align-items: center; margin-top: 7px; }
    .swatch { width: 11px; height: 11px; display: inline-block; border-radius: 50%; margin-right: 4px; border: 1px solid #333; }
    .status { font-size: 12px; margin-top: 8px; color: #52616f; }
    .actions { display: flex; gap: 8px; margin-top: 9px; flex-wrap: wrap; }
    button {
      border: 1px solid #9aa8b3; border-radius: 5px; background: #f7f9fa; color: #1f2933;
      font: inherit; font-size: 12px; padding: 6px 9px; cursor: pointer;
    }
    button:hover { background: #edf2f5; }
    .modal {
      position: absolute; z-index: 2000; inset: 0; display: none; align-items: center; justify-content: center;
      background: rgba(20, 28, 36, .35); padding: 18px;
    }
    .modal.open { display: flex; }
    .modal-card {
      width: min(820px, 96vw); max-height: 86vh; overflow: auto; background: var(--modal-card-bg);
      border: 1px solid #b9c3cc; border-radius: 8px; box-shadow: 0 10px 30px rgba(0,0,0,.28);
      padding: 16px 18px;
    }
    .modal-card h2 { margin: 0 0 10px; font-size: 18px; }
    .param-grid { display: grid; grid-template-columns: 150px 1fr; gap: 7px 12px; font-size: 13px; }
    .param-grid code { font-size: 12px; }
    .modal-note { margin-top: 12px; padding-top: 10px; border-top: 1px solid #d8dee4; font-size: 13px; }
    .release-list { display: grid; gap: 10px; font-size: 13px; line-height: 1.4; }
    .release-item { border-top: 1px solid #d8dee4; padding-top: 9px; }
    .release-date { font-weight: 700; color: #24313c; }
    .chart-panel {
      position: absolute; z-index: 1000; left: 12px; right: 12px; bottom: 12px;
      background: var(--panel-bg); border: 1px solid #c8d0d7; border-radius: 6px;
      box-shadow: 0 2px 10px rgba(25,35,45,.18); padding: 9px 12px; pointer-events: auto;
    }
    .chart-panel.fullscreen {
      top: 12px !important; left: 12px !important; right: 12px !important; bottom: 12px !important;
      z-index: 1800; display: flex; flex-direction: column;
    }
    .chart-panel.fullscreen .chart-extra { flex: 1; min-height: 0; display: flex; flex-direction: column; }
    .chart-panel.fullscreen .chart-scroll { flex: 1; min-height: 0; }
    .chart-panel.fullscreen #timeChart { height: calc(100vh - 150px); }
    .chart-panel.collapsed canvas, .chart-panel.collapsed .chart-extra { display: none; }
    .chart-head { display: flex; gap: 16px; align-items: center; font-size: 12px; color: #3a4750; margin-bottom: 5px; }
    .chart-actions { margin-left: auto; display: inline-flex; gap: 6px; align-items: center; }
    .plot-toggle {
      display: inline-flex; align-items: center; gap: 5px; border: 0; background: transparent;
      padding: 2px 4px; font-size: 12px; color: #3a4750;
    }
    .plot-toggle.off { opacity: .38; text-decoration: line-through; }
    .icon-btn {
      width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center;
      padding: 0; font-size: 16px; line-height: 1; font-weight: 700;
    }
    .line-key { display: inline-flex; align-items: center; gap: 5px; }
    .line-swatch { width: 24px; height: 3px; display: inline-block; border-radius: 2px; }
    .chart-scroll { overflow-x: auto; overflow-y: hidden; border-top: 1px solid #e1e6ea; }
    #timeChart { width: 1800px; height: 150px; display: block; }
    .gps-source-bar {
      min-width: 1800px; height: 18px; display: flex; gap: 1px; align-items: stretch;
      border-top: 1px solid #d8dee4; background: #f4f6f8; cursor: grab; user-select: none;
    }
    .gps-source-bar.dragging { cursor: grabbing; }
    .gps-source-seg { min-width: 7px; flex: 1 0 7px; border-bottom: 2px solid rgba(0,0,0,.15); }
    .gps-source-seg.hover {
      outline: 2px solid #111827; outline-offset: -2px; filter: brightness(1.12);
    }
    .sample-hover-tooltip {
      position: fixed; z-index: 2600; max-width: 320px; pointer-events: none;
      background: rgba(17,24,39,.94); color: #fff; border-radius: 6px;
      box-shadow: 0 8px 20px rgba(0,0,0,.28); padding: 8px 10px;
      font-size: 12px; line-height: 1.35; display: none;
    }
    .settings-grid { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 9px 14px; font-size: 13px; align-items: center; }
    .settings-grid input[type="text"], .settings-grid input[type="color"], .settings-grid select {
      min-width: 0; border: 1px solid #aab5bd; border-radius: 5px; font: inherit; padding: 6px 8px;
    }
    .settings-inline { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .settings-inline input { flex: 1 1 180px; min-width: 0; border: 1px solid #aab5bd; border-radius: 5px; font: inherit; padding: 6px 8px; }
    .osm-status { font-size: 12px; color: #52616f; }
    .settings-grid input[type="range"] { width: 100%; }
    #routeTrack { min-height: 208px; overflow-y: auto; }
    .settings-hint { margin-top: 12px; font-size: 12px; line-height: 1.45; color: #3a4750; }
    .osm-progress { display: grid; gap: 4px; min-width: min(420px, 100%); }
    .osm-progress progress { width: 100%; height: 12px; }
    .osm-path { overflow-wrap: anywhere; color: #52616f; }
    .osm-file-list { display: grid; gap: 6px; }
    .osm-file-item { border-top: 1px solid #d8dee4; padding-top: 6px; }
    .osm-file-name { font-weight: 700; color: #24313c; }
    .osm-report {
      white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid #d6dde3;
      background: #f4f6f8; padding: 7px 8px; border-radius: 5px; font-size: 12px;
    }
    .tabs { display: flex; gap: 6px; margin: 4px 0 8px; }
    .tab.active { background: #dfe8ee; border-color: #6f8799; }
    .tab-pane.hidden { display: none; }
    .error-table { max-height: 190px; overflow: auto; border-top: 1px solid #d6dde3; }
    .error-grid { display: grid; grid-template-columns: 92px 82px 130px minmax(260px, 1fr); border: 1px solid #d6dde3; font-size: 12px; }
    .error-grid > div { padding: 5px 7px; border-bottom: 1px solid #e1e6ea; min-width: 0; overflow-wrap: anywhere; }
    .error-grid .head { font-weight: 700; color: #24313c; background: #dfe8ee; position: sticky; top: 0; z-index: 1; }
    .error-grid .alt { background: #f4f6f8; }
    .redis-query { display: grid; gap: 8px; padding-top: 8px; border-top: 1px solid #d6dde3; }
    .redis-query textarea {
      width: 100%; min-height: 64px; resize: vertical; box-sizing: border-box;
      border: 1px solid #aab5bd; border-radius: 5px; font: inherit; font-size: 12px; padding: 7px 8px;
    }
    .query-presets { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .query-presets select {
      min-width: 220px; border: 1px solid #aab5bd; border-radius: 5px; font: inherit; font-size: 12px; padding: 6px 8px;
    }
    .query-result-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    #redisQueryPane { max-height: 260px; overflow: auto; padding-right: 4px; }
    #redisQueryPane .query-presets, #redisQueryPane .query-result-head {
      position: sticky; z-index: 2; background: var(--panel-bg);
    }
    #redisQueryPane .query-presets { top: 0; padding-top: 2px; }
    #redisQueryPane .query-result-head { top: 39px; padding: 4px 0; }
    .chart-panel.fullscreen #redisQueryPane { max-height: calc(100vh - 170px); }
    .prop-grid { display: grid; grid-template-columns: 155px minmax(0, 1fr); border: 1px solid #d6dde3; font-size: 12px; }
    .prop-grid > div { padding: 5px 7px; border-bottom: 1px solid #e1e6ea; min-width: 0; overflow-wrap: anywhere; }
    .prop-grid > div:nth-child(4n+1), .prop-grid > div:nth-child(4n+2) { background: #f4f6f8; }
    .prop-grid .key { font-weight: 650; color: #35424d; }
    .adb-row { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
    .adb-row select { min-width: 180px; border: 1px solid #aab5bd; border-radius: 5px; font: inherit; font-size: 12px; padding: 6px 8px; }
    .wifi-edit { display: flex; gap: 6px; margin-top: 8px; }
    .wifi-edit input {
      min-width: 0; flex: 1; border: 1px solid #aab5bd; border-radius: 5px;
      font: inherit; font-size: 12px; padding: 6px 8px;
    }
    .unmatched-box { margin-top: 9px; border-top: 1px solid #d8dee4; padding-top: 8px; }
    .unmatched-head { display: flex; justify-content: space-between; gap: 8px; align-items: center; margin-bottom: 5px; }
    .unmatched-list { display: grid; gap: 6px; max-height: 160px; overflow: auto; }
    .unmatched-item {
      display: grid; grid-template-columns: 52px 1fr auto; gap: 6px; align-items: center;
      border: 1px solid #d6dde3; border-radius: 5px; padding: 5px 6px; background: rgba(255,255,255,.72);
    }
    .unmatched-item:nth-child(even) { background: rgba(244,246,248,.8); }
    .unmatched-meta { min-width: 0; overflow-wrap: anywhere; }
    .sample-marker {
      width: 24px; height: 24px; border-radius: 50%; border: 2px solid #1f2933;
      color: #111; display: flex; align-items: center; justify-content: center;
      font-size: 11px; font-weight: 800; box-shadow: 0 1px 5px rgba(0,0,0,.35);
    }
    .sample-marker.selected { outline: 3px solid #9b5de5; outline-offset: 2px; }
    .leaflet-popup-content { width: min(620px, 82vw) !important; max-height: 62vh; overflow: auto; margin: 14px; }
    .sample-popup { min-width: min(580px, 78vw); }
    .sample-popup .prop-grid { grid-template-columns: 170px minmax(280px, 1fr); }
    .popup-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 8px; }
    .popup-close { padding: 4px 8px; }
    @media (max-width: 840px) {
      .topbar { grid-template-columns: 1fr; }
      #summaryPanel, #detailsPanel { width: auto; max-width: none; left: 8px; right: 8px; }
      #detailsPanel { top: 178px; }
      .side { width: auto; max-width: none; }
      .metrics { grid-template-columns: repeat(2, minmax(84px, 1fr)); }
      .chart-panel { position: absolute; left: 8px; right: 8px; bottom: 8px; }
      #timeChart { height: 120px; width: 1400px; }
    }
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="topbar">
    <div class="panel" id="summaryPanel">
      <div class="panel-title">
        <h1>Moving Target OSM Dashboard</h1>
        <button class="collapseBtn" data-target="summaryPanel" type="button">Collapse</button>
      </div>
      <div class="collapsible-body">
        <div class="metrics">
          <div class="metric"><div class="label">Samples</div><div class="value" id="samples">0</div></div>
          <div class="metric"><div class="label">Avg latency</div><div class="value" id="avg">--</div></div>
          <div class="metric"><div class="label">Speed</div><div class="value" id="speed">--</div></div>
          <div class="metric"><div class="label">Access point</div><div class="value" id="wifi">--</div></div>
          <div class="metric"><div class="label">GPS latitude</div><div class="value coordinate-value" id="latitude">--</div></div>
          <div class="metric"><div class="label">GPS longitude</div><div class="value coordinate-value" id="longitude">--</div></div>
        </div>
        <div class="legend small">
          <span><i class="swatch" style="background:#1a9850"></i>&lt;80</span>
          <span><i class="swatch" style="background:#91cf60"></i>80-150</span>
          <span><i class="swatch" style="background:#fee08b"></i>150-250</span>
          <span><i class="swatch" style="background:#fc8d59"></i>250-400</span>
          <span><i class="swatch" style="background:#d73027"></i>400+</span>
        </div>
        <div class="wifi-edit">
          <input id="wifiLabel" placeholder="Wi-Fi name override">
          <button id="saveWifiLabel" type="button">Save Wi-Fi name</button>
          <button id="detectMacWifi" type="button">Detect Mac Wi-Fi</button>
        </div>
        <div class="adb-row">
          <select id="adbDevices"><option value="">ADB auto/no device</option></select>
          <button id="refreshAdb" type="button">Refresh ADB</button>
          <button id="connectAdb" type="button">Connect 100.77.37.113</button>
        </div>
        <div class="actions">
          <button id="toggleCollection" type="button">Stop collection</button>
          <button id="zoomLatest" type="button">Zoom latest</button>
          <button id="startBrowserGps" type="button">Start browser GPS</button>
          <button id="startMotion" type="button">Start accelerometer</button>
          <button id="exportKml" type="button">Export KML</button>
          <button id="openReleaseNotes" type="button">Release notes</button>
          <button id="openHelp" type="button">5G parameters</button>
        </div>
        <div class="status" id="status">Connecting to local sampler...</div>
        <div class="unmatched-box small">
          <div class="unmatched-head">
            <strong>Unmatched latency samples</strong>
            <button id="pinAllUnmatched" type="button">Pin all</button>
          </div>
          <div id="unmatchedHint">Samples without GPS are not shown on the map. Select one, then click the map to pin and store a location.</div>
          <div id="unmatchedSamples" class="unmatched-list"></div>
        </div>
      </div>
    </div>
    <div class="panel side small" id="detailsPanel">
      <div class="panel-title">
        <strong>Details / Logs</strong>
        <span>
          <button id="openSettings" type="button">Settings</button>
          <button class="collapseBtn" data-target="detailsPanel" type="button">Collapse</button>
        </span>
      </div>
      <div class="collapsible-body" id="details">No sample yet.</div>
    </div>
  </div>
  <div class="chart-panel" id="chartPanel">
    <div class="chart-head">
      <strong>Time Plot</strong>
      <button class="plot-toggle" data-plot-key="latency" type="button"><i class="line-swatch" style="background:#d73027"></i>Latency ms</button>
      <button class="plot-toggle" data-plot-key="speed" type="button"><i class="line-swatch" style="background:#9b5de5"></i>Speed km/h</button>
      <button class="plot-toggle" data-plot-key="gpsAccuracy" type="button"><i class="line-swatch" style="background:#2b6cb0"></i>GPS accuracy m</button>
      <button class="plot-toggle" data-plot-key="wifiSignal" type="button"><i class="line-swatch" style="background:#2f855a"></i>Wi-Fi RSSI</button>
      <button class="plot-toggle" data-plot-key="accel" type="button"><i class="line-swatch" style="background:#f59f00"></i>Accel</button>
      <span id="chartScale">Waiting for samples</span>
      <span class="chart-actions">
        <button id="plotPageLeft" class="icon-btn" type="button" title="Page plot left" aria-label="Page plot left">&lsaquo;</button>
        <button id="plotPageRight" class="icon-btn" type="button" title="Page plot right" aria-label="Page plot right">&rsaquo;</button>
        <button id="fullscreenChart" type="button">Fullscreen</button>
        <button class="collapseBtn" data-target="chartPanel" type="button">Collapse</button>
      </span>
    </div>
    <div class="chart-extra">
      <div class="tabs">
        <button class="tab active" data-tab="plotPane" type="button">Plot</button>
        <button class="tab" data-tab="errorPane" type="button">Error logs</button>
        <button class="tab" data-tab="eventPane" type="button">Application events</button>
        <button class="tab" data-tab="redisQueryPane" type="button">Redis query</button>
      </div>
      <div id="plotPane" class="tab-pane chart-scroll">
        <div id="gpsSourceBar" class="gps-source-bar" title="GPS source / correction formula per sample"></div>
        <canvas id="timeChart"></canvas>
      </div>
      <div id="errorPane" class="tab-pane hidden error-table">
        <div id="errorRows" class="error-grid"></div>
      </div>
      <div id="eventPane" class="tab-pane hidden error-table">
        <div id="eventRows" class="error-grid"></div>
      </div>
      <div id="redisQueryPane" class="tab-pane hidden redis-query">
        <div class="query-presets">
          <label for="redisQueryPreset">Preset</label>
          <select id="redisQueryPreset">
            <option value="today">Events and errors today</option>
            <option value="yesterday">Events and errors yesterday</option>
            <option value="last_month">Events and errors last month</option>
            <option value="last_6_months">Events and errors last 6 months</option>
          </select>
        </div>
        <textarea id="redisQueryText">SELECT date,time,level,source,message FROM redis_logs WHERE type IN (application_events,error_logs) AND timestamp BETWEEN yesterday 00:00 AND yesterday 23:59</textarea>
        <div class="query-result-head">
          <button id="runRedisQuery" type="button">Apply</button>
          <span id="redisQueryStatus">Ready. Sample prompt queries yesterday's application events and error logs.</span>
        </div>
        <div id="redisQueryRows" class="error-grid"></div>
      </div>
    </div>
  </div>
  <div class="modal" id="settingsModal" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
    <div class="modal-card">
      <h2 id="settingsTitle">Collection And Display Settings</h2>
      <div class="settings-grid">
        <label for="gpsFormula">GPS correction formula</label>
        <select id="gpsFormula">
          <option value="raw">Raw coordinates, no correction</option>
          <option value="constant_speed_track">Constant speed on selected track</option>
        </select>
        <label for="routeTrack">Correction track</label>
        <select id="routeTrack" size="10"></select>
        <label><input id="useAdbGps" type="checkbox"> Use Android ADB GPS/radio</label><div>Reads Android location and 5G/LTE fields when USB debugging is authorized.</div>
        <label><input id="useBrowserGps" type="checkbox"> Use browser GPS</label><div>Uses HTML5 Geolocation when the browser and OS grant permission.</div>
        <label><input id="useAccelerometer" type="checkbox"> Record accelerometer</label><div>Stores browser motion readings. It is not applied to GPS unless a correction formula uses it.</div>
        <label><input id="allowInterpolatedFallback" type="checkbox"> Allow interpolated fallback</label><div>Creates positions on the selected six-hour track only when no real GPS is available.</div>
        <label for="providerInput">Provider override</label><input id="providerInput" type="text" placeholder="auto from traceroute/DNS">
        <label for="redisUrl">Redis URL</label><input id="redisUrl" type="text" placeholder="redis://127.0.0.1:6379/0">
        <label for="redisPrefix">Redis prefix</label><input id="redisPrefix" type="text" placeholder="moving_client_data">
        <label>GCP Redis creation</label>
        <div class="osm-progress">
          <div class="settings-inline">
            <input id="gcpRedisProject" type="text" placeholder="GCP project id">
            <input id="gcpRedisRegion" type="text" placeholder="europe-west1" value="europe-west1">
          </div>
          <div class="settings-inline">
            <input id="gcpRedisInstance" type="text" placeholder="moving-target-redis" value="moving-target-redis">
            <select id="gcpRedisTier"><option value="basic">basic</option><option value="standard">standard</option></select>
            <input id="gcpRedisMemory" type="number" min="1" max="300" value="1" title="Memory GB">
          </div>
          <div class="settings-inline">
            <input id="gcpRedisNetwork" type="text" placeholder="default" value="default">
            <button id="planGcpRedis" type="button">Plan GCP Redis</button>
          </div>
          <pre id="gcpRedisPlan" class="osm-report">No GCP Redis plan generated yet.</pre>
        </div>
        <label for="modalBg">Modal background color</label><input id="modalBg" type="color">
        <label for="panelOpacity">Panel transparency</label><input id="panelOpacity" type="range" min="35" max="100" step="1">
        <label for="modalOpacity">Modal transparency</label><input id="modalOpacity" type="range" min="55" max="100" step="1">
        <label for="summaryDock">Summary dock</label>
        <select id="summaryDock"><option>top-left</option><option>top-right</option><option>left</option><option>right</option><option>bottom</option></select>
        <label for="detailsDock">Details dock</label>
        <select id="detailsDock"><option>top-right</option><option>top-left</option><option>left</option><option>right</option><option>bottom</option></select>
        <label for="chartDock">Chart dock</label>
        <select id="chartDock"><option>bottom</option><option>top-left</option><option>top-right</option><option>left</option><option>right</option></select>
        <label for="osmCountry">Download OSM country</label>
        <div class="settings-inline">
          <input id="osmCountry" type="text" placeholder="germany, france, luxembourg">
          <button id="downloadOsmCountry" type="button">Download OSM</button>
          <span id="osmDownloadStatus" class="osm-status">Idle</span>
        </div>
        <label>OSM storage</label>
        <div class="osm-progress">
          <div id="osmStorageInfo" class="osm-status">Checking local OSM storage...</div>
          <div id="osmStoragePath" class="osm-path"></div>
          <div id="osmFileList" class="osm-file-list"></div>
          <button id="checkOsmConsistency" type="button">Check OSM consistency</button>
          <div id="osmConsistencyReport" class="osm-report">No consistency check run yet.</div>
        </div>
      </div>
      <div class="settings-hint">
        HTML5 GPS is permission-based. On phones it may use GNSS, Wi-Fi, cellular, Bluetooth, and IP hints, but JavaScript only receives latitude, longitude, altitude fields when present, speed/heading when available, and an accuracy radius in meters. Android's coarse/fine location choice is handled by the OS permission layer; browsers usually expose the resulting accuracy value, not a direct "coarse" or "fine" flag.
      </div>
      <div class="settings-hint">
        WLAN SSID is often redacted on macOS because nearby Wi-Fi names are treated as location-sensitive. The terminal/browser process needs Location Services permission, and sandboxed commands can still receive "&lt;redacted&gt;"; use the Wi-Fi name override when macOS blocks it.
      </div>
      <div class="actions">
        <button id="saveSettings" type="button">Save settings</button>
        <button id="closeSettings" type="button">Close</button>
      </div>
    </div>
  </div>
  <div class="modal" id="helpModal" role="dialog" aria-modal="true" aria-labelledby="helpTitle">
    <div class="modal-card">
      <h2 id="helpTitle">5G / LTE Parameter Guide</h2>
      <div class="param-grid">
        <code>NR</code><div>New Radio, the 5G radio access technology. On many German networks the phone may use 5G NSA, where LTE remains the anchor.</div>
        <code>NSA / SA</code><div>Non-standalone uses LTE plus 5G; standalone uses a 5G core. Handover behavior can differ strongly on a fast train.</div>
        <code>ARFCN</code><div>Absolute radio frequency channel number. <code>nr_arfcn</code> identifies the 5G channel; <code>lte_earfcn</code> is the LTE equivalent.</div>
        <code>Band</code><div>Frequency band, such as 5G <code>n78</code> or LTE bands. Higher bands can be faster but may fade more inside trains.</div>
        <code>PCI</code><div>Physical cell ID. It identifies the radio sector locally; it is useful for detecting handovers but is not globally unique.</div>
        <code>NCI / CI</code><div>5G NR cell identity or LTE cell identity. When exposed, this is closer to a unique serving-cell identifier.</div>
        <code>TAC</code><div>Tracking area code. A mobility-management region, not a tower coordinate.</div>
        <code>RSRP</code><div>Reference signal received power. Less negative is better: about -80 dBm is strong, -100 dBm is weak, -115 dBm is poor.</div>
        <code>RSRQ</code><div>Reference signal received quality. Less negative is better. A poor value can indicate congestion, interference, or bad radio conditions even when RSRP is acceptable.</div>
        <code>SINR</code><div>Signal-to-interference-plus-noise ratio. Higher is better. Above 15 dB is good; near 0 dB or negative is problematic.</div>
        <code>RSSNR</code><div>LTE signal-to-noise-style field exposed by Android. Higher is better; scale can vary by device/firmware.</div>
        <code>Operator</code><div>Carrier name and MCC/MNC. Useful when roaming or when the train crosses coverage regions.</div>
        <code>Latency</code><div>Measured HTTPS transaction time from the laptop. Spikes can come from handovers, DNS, TLS setup, congestion, VPNs, or server path changes.</div>
      </div>
      <div class="modal-note">
        There is no safe generic "5G improvement slider" exposed by Android or Samsung developer options. Practical tuning is physical placement, USB tethering, disabling VPN during diagnosis, using 5G Auto instead of forced NR-only, and avoiding band locks while moving at moving speeds.
      </div>
      <div class="actions">
        <button id="closeHelp" type="button">Close</button>
      </div>
    </div>
  </div>
  <div class="modal" id="releaseModal" role="dialog" aria-modal="true" aria-labelledby="releaseTitle">
    <div class="modal-card">
      <h2 id="releaseTitle">Release Notes</h2>
      <div class="release-list">
        <div class="release-item"><div class="release-date">2026-06-30</div>Renamed the project to Moving Target OSM Dashboard, uploaded the repository to GitHub, and renamed the offline client to <code>moving_client_data.py</code>.</div>
        <div class="release-item"><div class="release-date">2026-06-30</div>Added compact latitude/longitude display, collection start/stop controls, release notes, sensor status, traceroute access-point metadata, plot toggles, and OSM download progress/storage reporting.</div>
        <div class="release-item"><div class="release-date">2026-06-29</div>Added Redis persistence, KML export, route correction, error/event logs, settings, and OSM extract download support.</div>
        <div class="release-item"><div class="release-date">2026-06-25</div>Initial realtime OpenStreetMap dashboard with latency sampling, GPS, ADB radio metadata, Wi-Fi context, and route visualization.</div>
      </div>
      <div class="actions">
        <button id="closeReleaseNotes" type="button">Close</button>
      </div>
    </div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const map = L.map('map', { zoomControl: true }).setView([51.1657, 10.4515], 6);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);

    const markers = new Map();
    const routeSegments = [];
    const routerRouteLatLngs = [];
    const routerMarkers = new Map();
    let routerLine = null;
    const routeLatLngs = [];
    const plottedSamples = [];
    const CHART_SAMPLE_LIMIT = 5000;
    const CHART_MIN_WIDTH = 1800;
    const CHART_PX_PER_SAMPLE = 8;
    let hasFit = false;
    let latestLatLng = null;
    let gpsWatchId = null;
    let selectedSequence = null;
    let appSettings = {};
    let routeTracks = [];
    const trackLines = new Map();
    let latestSample = null;
    let virtualPathLine = null;
    const unmatchedSamples = new Map();
    let pendingLocationAssignment = null;
    let pendingPinAll = false;
    let gpsBarDrag = null;
    let motionHandler = null;
    let hoveredGpsSeg = null;
    let hoverToken = 0;
    let latestGpsCityToken = 0;
    let plotFollowLatest = true;
    let osmStatusTimer = null;
    const cityCache = new Map();

    function color(ms) {
      if (ms === null || ms === undefined) return '#777777';
      if (ms < 80) return '#1a9850';
      if (ms < 150) return '#91cf60';
      if (ms < 250) return '#fee08b';
      if (ms < 400) return '#fc8d59';
      return '#d73027';
    }

    function rgbaFromHex(hex, opacity) {
      const clean = String(hex || '#ffffff').replace('#', '');
      const value = /^[0-9a-fA-F]{6}$/.test(clean) ? clean : 'ffffff';
      const r = parseInt(value.slice(0, 2), 16);
      const g = parseInt(value.slice(2, 4), 16);
      const b = parseInt(value.slice(4, 6), 16);
      return `rgba(${r},${g},${b},${Math.max(0.1, Math.min(1, opacity))})`;
    }

    function gpsSourceColor(sample) {
      const loc = sample.location || {};
      const correction = sample.position_correction || {};
      if (correction.applied) return '#9b5de5';
      if (loc.source === 'browser') return '#1a9850';
      if (loc.source === 'adb' || loc.source === 'android') return '#2b6cb0';
      if (loc.source === 'interpolated') return '#8d99ae';
      if (loc.source) return '#f59f00';
      return '#d73027';
    }

    function drawGpsSourceBar(rows) {
      const bar = document.getElementById('gpsSourceBar');
      if (!bar) return;
      const visible = (rows || chartRows());
      bar.innerHTML = visible.map((sample, i) => {
        const loc = sample.location || {};
        const correction = sample.position_correction || {};
        const title = [
          `#${sample.sequence}`,
          `source=${loc.source || 'none'}`,
          `type=${loc.coordinate_type || loc.source || 'none'}`,
          `formula=${correction.method || sample.settings?.gps_formula || 'raw'}`,
          `latency=${sample.latency_summary?.avg_ms ?? ''}ms`
        ].join(' | ');
        return `<span class="gps-source-seg" data-index="${i}" data-sequence="${esc(sample.sequence)}" data-title="${esc(title)}" style="background:${gpsSourceColor(sample)}"></span>`;
      }).join('');
    }

    function chartRows() {
      return plottedSamples.slice(-CHART_SAMPLE_LIMIT);
    }

    function resizePlotForRows(rows) {
      const canvas = document.getElementById('timeChart');
      const bar = document.getElementById('gpsSourceBar');
      const width = Math.max(CHART_MIN_WIDTH, (rows.length || 1) * CHART_PX_PER_SAMPLE);
      if (canvas) canvas.style.width = `${width}px`;
      if (bar) {
        bar.style.width = `${width}px`;
        bar.style.minWidth = `${width}px`;
      }
    }

    function tooltipEl() {
      let el = document.getElementById('sampleHoverTooltip');
      if (!el) {
        el = document.createElement('div');
        el.id = 'sampleHoverTooltip';
        el.className = 'sample-hover-tooltip';
        document.body.appendChild(el);
      }
      return el;
    }

    function sampleForGpsSegment(el) {
      const index = Number(el?.dataset?.index);
      const rows = chartRows();
      return Number.isInteger(index) ? rows[index] : null;
    }

    function formatGpsForSample(sample) {
      const loc = sample?.location || {};
      if (!Number.isFinite(loc.lat) || !Number.isFinite(loc.lon)) return 'no GPS';
      const type = loc.coordinate_type || loc.source || 'gps';
      return `${Number(loc.lat).toFixed(5)}, ${Number(loc.lon).toFixed(5)} (${type})`;
    }

    function cityKey(lat, lon) {
      return `${Number(lat).toFixed(3)},${Number(lon).toFixed(3)}`;
    }

    async function lookupCityForCoords(lat, lon) {
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return '';
      const key = cityKey(lat, lon);
      if (cityCache.has(key)) return cityCache.get(key);
      try {
        const url = `https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=10&addressdetails=1&lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`;
        const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
        if (!res.ok) return '';
        const data = await res.json();
        const address = data.address || {};
        const street = [address.road, address.house_number].filter(Boolean).join(' ');
        const city = address.city || address.town || address.village || address.municipality || address.county || '';
        const region = address.state || address.region || '';
        const country = address.country || '';
        const poiName = data.name || address.amenity || address.shop || address.tourism || address.leisure || '';
        const poiType = data.type || data.category || address.shop || address.amenity || '';
        const label = [street, city, region, country].filter(Boolean).join(', ');
        const poi = [poiName, poiType].filter(Boolean).join(' · ');
        const enriched = { label, poi, rawType: data.type || '', category: data.category || '' };
        cityCache.set(key, enriched);
        return enriched;
      } catch (_) {
        cityCache.set(key, { label: '', poi: '', rawType: '', category: '' });
        return { label: '', poi: '', rawType: '', category: '' };
      }
    }

    async function reverseCity(sample, token) {
      const loc = sample?.location || {};
      const place = await lookupCityForCoords(loc.lat, loc.lon);
      return token === hoverToken ? place : { label: '', poi: '', rawType: '', category: '' };
    }

    function renderGpsMetric(sample, place = null) {
      const loc = sample?.location || {};
      const latEl = document.getElementById('latitude');
      const lonEl = document.getElementById('longitude');
      if (!Number.isFinite(loc.lat) || !Number.isFinite(loc.lon)) {
        latEl.textContent = '--';
        lonEl.textContent = '--';
        return;
      }
      const label = typeof place === 'string' ? place : (place?.label || '');
      const poi = typeof place === 'object' ? place.poi : '';
      const geo = label ? `<span class="geo-name">${esc(label)}</span>` : '';
      const poiLine = poi ? `<span class="geo-name">POI: ${esc(poi)}</span>` : '';
      latEl.innerHTML = `${Number(loc.lat).toFixed(5)}${geo}`;
      lonEl.innerHTML = `${Number(loc.lon).toFixed(5)}${poiLine || geo}`;
    }

    function updateLatestGpsMetric(sample) {
      latestGpsCityToken += 1;
      const token = latestGpsCityToken;
      renderGpsMetric(sample);
      const loc = sample?.location || {};
      if (!Number.isFinite(loc.lat) || !Number.isFinite(loc.lon)) return;
      lookupCityForCoords(loc.lat, loc.lon).then(place => {
        if (token === latestGpsCityToken) {
          sample.place_info = place;
          renderGpsMetric(sample, place);
          if (latestSample?.sequence === sample.sequence) addSample(sample);
        }
      });
    }

    function updateTooltipPosition(ev) {
      const el = tooltipEl();
      const margin = 14;
      const rect = el.getBoundingClientRect();
      let left = ev.clientX + margin;
      let top = ev.clientY + margin;
      if (left + rect.width + margin > window.innerWidth) left = ev.clientX - rect.width - margin;
      if (top + rect.height + margin > window.innerHeight) top = ev.clientY - rect.height - margin;
      el.style.left = `${Math.max(margin, left)}px`;
      el.style.top = `${Math.max(margin, top)}px`;
    }

    function showSampleTooltip(sample, ev, place = null) {
      if (!sample) return;
      const summary = sample.latency_summary || {};
      const loc = sample.location || {};
      const latency = Number.isFinite(summary.avg_ms) ? `${Math.round(summary.avg_ms)} ms` : 'n/a';
      const source = loc.source || 'none';
      const pinHint = (!Number.isFinite(loc.lat) || !Number.isFinite(loc.lon))
        ? '<div><strong>Pin:</strong> double-click this sample, then click the map</div>'
        : '';
      const label = typeof place === 'string' ? place : (place?.label || '');
      const poi = typeof place === 'object' ? place.poi : '';
      const cityRow = label ? `<div><strong>Place:</strong> ${esc(label)}</div>` : '';
      const poiRow = poi ? `<div><strong>POI:</strong> ${esc(poi)}</div>` : '';
      const el = tooltipEl();
      el.innerHTML = `
        <div><strong>Sample #${esc(sample.sequence)}</strong></div>
        <div><strong>Latency:</strong> ${esc(latency)}</div>
        <div><strong>Time:</strong> ${esc(sample.timestamp_utc || '')}</div>
        <div><strong>GPS:</strong> ${esc(formatGpsForSample(sample))}</div>
        <div><strong>Source:</strong> ${esc(source)}</div>
        ${cityRow}
        ${poiRow}
        ${pinHint}
      `;
      el.style.display = 'block';
      updateTooltipPosition(ev);
    }

    function clearGpsHover() {
      hoverToken += 1;
      if (hoveredGpsSeg) hoveredGpsSeg.classList.remove('hover');
      hoveredGpsSeg = null;
      const el = tooltipEl();
      el.style.display = 'none';
    }

    function setupGpsSourceBarInteractions() {
      const bar = document.getElementById('gpsSourceBar');
      const scroll = document.getElementById('plotPane');
      if (!bar || !scroll) return;

      bar.addEventListener('pointerdown', ev => {
        if (!ev.target.closest('.gps-source-seg')) return;
        gpsBarDrag = { x: ev.clientX, scrollLeft: scroll.scrollLeft, moved: false };
        bar.classList.add('dragging');
        bar.setPointerCapture?.(ev.pointerId);
      });

      bar.addEventListener('pointermove', ev => {
        if (gpsBarDrag) {
          if (Math.abs(ev.clientX - gpsBarDrag.x) > 3) gpsBarDrag.moved = true;
          scroll.scrollLeft = Math.max(0, gpsBarDrag.scrollLeft - (ev.clientX - gpsBarDrag.x));
          plotFollowLatest = false;
          ev.preventDefault();
          return;
        }
        const seg = ev.target.closest('.gps-source-seg');
        if (!seg) {
          clearGpsHover();
          return;
        }
        const sample = sampleForGpsSegment(seg);
        if (!sample) return;
        if (hoveredGpsSeg !== seg) {
          if (hoveredGpsSeg) hoveredGpsSeg.classList.remove('hover');
          hoveredGpsSeg = seg;
          hoveredGpsSeg.classList.add('hover');
          const token = hoverToken + 1;
          hoverToken = token;
          showSampleTooltip(sample, ev);
          reverseCity(sample, token).then(city => {
            if (token === hoverToken && hoveredGpsSeg === seg) showSampleTooltip(sample, ev, city);
          });
        } else {
          updateTooltipPosition(ev);
        }
      });

      function finishDrag(ev) {
        if (gpsBarDrag) {
          if (gpsBarDrag.moved) bar.dataset.suppressClick = '1';
          gpsBarDrag = null;
          bar.classList.remove('dragging');
          try { bar.releasePointerCapture?.(ev.pointerId); } catch (_) {}
        }
      }

      bar.addEventListener('pointerup', finishDrag);
      bar.addEventListener('pointercancel', finishDrag);
      bar.addEventListener('pointerleave', () => {
        if (!gpsBarDrag) clearGpsHover();
      });
      bar.addEventListener('click', ev => {
        if (bar.dataset.suppressClick === '1') {
          delete bar.dataset.suppressClick;
          return;
        }
        const seg = ev.target.closest('.gps-source-seg');
        const sample = sampleForGpsSegment(seg);
        if (sample?.sequence !== undefined) selectSample(sample.sequence);
      });
      bar.addEventListener('dblclick', ev => {
        const seg = ev.target.closest('.gps-source-seg');
        const sample = sampleForGpsSegment(seg);
        if (!sample?.sequence) return;
        ev.preventDefault();
        ev.stopPropagation();
        if (hasUsableLocation(sample)) {
          document.getElementById('status').textContent = `Sample #${sample.sequence} already has GPS.`;
          return;
        }
        pendingPinAll = false;
        pendingLocationAssignment = Number(sample.sequence);
        document.getElementById('unmatchedHint').textContent = `Click the map to assign a manual location to sample #${pendingLocationAssignment}.`;
        document.getElementById('status').textContent = `Pin mode active from plot for sample #${pendingLocationAssignment}. Click the map position to store it.`;
      });
    }

    function dockPanel(id, dock) {
      const panel = document.getElementById(id);
      if (!panel) return;
      panel.classList.remove('dock-top-left', 'dock-top-right', 'dock-left', 'dock-right', 'dock-bottom');
      panel.classList.add(`dock-${dock || 'top-left'}`);
    }

    function applySettings(settings) {
      appSettings = settings || {};
      const panelOpacity = Number(appSettings.panel_opacity ?? 0.78);
      const modalOpacity = Number(appSettings.modal_opacity ?? 1.0);
      const modalBg = appSettings.modal_bg || '#ffffff';
      document.documentElement.style.setProperty('--summary-bg', rgbaFromHex('#ffffff', panelOpacity));
      document.documentElement.style.setProperty('--panel-bg', rgbaFromHex('#ffffff', Math.max(panelOpacity, 0.68)));
      document.documentElement.style.setProperty('--modal-card-bg', rgbaFromHex(modalBg, modalOpacity));
      dockPanel('summaryPanel', appSettings.summary_dock || 'top-left');
      dockPanel('detailsPanel', appSettings.details_dock || 'top-right');
      dockPanel('chartPanel', appSettings.chart_dock || 'bottom');
      updateTrackStyles();
      updateVirtualPath();
      drawTimeChart();
    }

    async function loadSettings() {
      const res = await fetch('/api/settings');
      const data = await res.json();
      routeTracks = data.route_tracks || [];
      applySettings(data.settings || {});
      return data.settings || {};
    }

    function drawReferenceTracks() {
      if (!routeTracks.length) return;
      const bounds = [];
      routeTracks.forEach((track, index) => {
        if (trackLines.has(track.id)) return;
        const line = L.polyline(track.path, {
          color: '#ffffff',
          weight: index === 0 ? 7 : 5,
          opacity: index === 0 ? 0.9 : 0.55,
          dashArray: '12 8',
          lineCap: 'round',
          lineJoin: 'round'
        }).addTo(map).bindPopup(`${esc(track.name)} · ${track.duration_hours}h · performed ${track.performed_count}x`);
        trackLines.set(track.id, line);
        track.path.forEach(point => bounds.push(point));
        L.polyline(track.path, {
          color: '#1f2933',
          weight: index === 0 ? 9 : 7,
          opacity: 0.24,
          dashArray: '12 8',
          lineCap: 'round',
          lineJoin: 'round'
        }).addTo(map).bringToBack();
      });
      if (bounds.length && !hasFit) map.fitBounds(bounds, { padding: [35, 35] });
      updateVirtualPath();
    }

    function updateTrackStyles() {
      const selected = appSettings.selected_track_id || 'berlin_karlsruhe';
      for (const [id, line] of trackLines.entries()) {
        line.setStyle({
          color: id === selected ? '#ffffff' : '#d5dde5',
          weight: id === selected ? 8 : 4,
          opacity: id === selected ? 0.95 : 0.5,
        });
      }
    }

    function selectedTrack(trackId) {
      const selected = trackId || appSettings.selected_track_id || 'berlin_karlsruhe';
      return routeTracks.find(track => track.id === selected) || routeTracks[0] || null;
    }

    function pointDistanceKm(a, b) {
      const r = 6371.0088;
      const lat1 = a[0] * Math.PI / 180;
      const lat2 = b[0] * Math.PI / 180;
      const dLat = (b[0] - a[0]) * Math.PI / 180;
      const dLon = (b[1] - a[1]) * Math.PI / 180;
      const x = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
      return 2 * r * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
    }

    function pointAtTrackFraction(track, fraction) {
      const path = track?.path || [];
      if (!path.length) return null;
      const clamped = Math.max(0, Math.min(1, Number(fraction) || 0));
      const segments = [];
      let total = 0;
      for (let i = 0; i < path.length - 1; i += 1) {
        const dist = pointDistanceKm(path[i], path[i + 1]);
        segments.push({ a: path[i], b: path[i + 1], dist, index: i });
        total += dist;
      }
      if (!segments.length || total <= 0) return { point: path[0], nextIndex: 1 };
      const target = total * clamped;
      let walked = 0;
      for (const segment of segments) {
        if (walked + segment.dist >= target) {
          const local = segment.dist ? (target - walked) / segment.dist : 0;
          return {
            point: [
              segment.a[0] + (segment.b[0] - segment.a[0]) * local,
              segment.a[1] + (segment.b[1] - segment.a[1]) * local
            ],
            nextIndex: segment.index + 1
          };
        }
        walked += segment.dist;
      }
      return { point: path[path.length - 1], nextIndex: path.length };
    }

    function virtualPathLatLngs() {
      const sample = latestSample || {};
      const loc = sample.location || {};
      const correction = sample.position_correction || {};
      const inferredTrackId = loc.track_id || correction.track_id || sample.settings?.selected_track_id;
      const track = selectedTrack(inferredTrackId);
      if (!track || !track.path?.length) return [];
      const sampleTrackId = loc.track_id || correction.track_id || sample.settings?.selected_track_id;

      if (sampleTrackId === track.id && Number.isFinite(Number(loc.fraction))) {
        const anchor = pointAtTrackFraction(track, loc.fraction);
        return anchor ? [anchor.point, ...track.path.slice(anchor.nextIndex)] : track.path;
      }
      if (sampleTrackId === track.id && Number.isFinite(loc.lat) && Number.isFinite(loc.lon)) {
        const current = [loc.lat, loc.lon];
        let nearestIndex = 0;
        let nearestDistance = Infinity;
        track.path.forEach((point, index) => {
          const dist = pointDistanceKm(current, point);
          if (dist < nearestDistance) {
            nearestDistance = dist;
            nearestIndex = index;
          }
        });
        return [current, ...track.path.slice(Math.min(track.path.length - 1, nearestIndex + 1))];
      }
      return track.path;
    }

    function updateVirtualPath() {
      if (virtualPathLine) {
        map.removeLayer(virtualPathLine);
        virtualPathLine = null;
      }
      if (appSettings.gps_formula !== 'constant_speed_track') return;
      const latlngs = virtualPathLatLngs();
      if (latlngs.length < 2) return;
      const track = selectedTrack((latestSample?.location || {}).track_id || (latestSample?.position_correction || {}).track_id);
      virtualPathLine = L.polyline(latlngs, {
        color: '#ffffff',
        weight: 6,
        opacity: 0.98,
        dashArray: '5 12',
        lineCap: 'round',
        lineJoin: 'round'
      }).addTo(map).bindPopup(`Virtual correction path to destination: ${esc(track?.name || '')}`);
      virtualPathLine.bringToFront();
    }

    function fillTrackSelect(selectedTrackId) {
      const select = document.getElementById('routeTrack');
      select.innerHTML = '';
      routeTracks.forEach(track => {
        const opt = document.createElement('option');
        opt.value = track.id;
        opt.textContent = `${track.performed_count}x · ${track.duration_hours}h · ${track.name}`;
        opt.selected = track.id === selectedTrackId;
        select.appendChild(opt);
      });
    }

    function fillSettingsForm(settings) {
      document.getElementById('gpsFormula').value = settings.gps_formula || 'raw';
      fillTrackSelect(settings.selected_track_id || 'berlin_karlsruhe');
      document.getElementById('useAdbGps').checked = settings.use_adb_gps !== false;
      document.getElementById('useBrowserGps').checked = settings.use_browser_gps !== false;
      document.getElementById('useAccelerometer').checked = !!settings.use_accelerometer;
      document.getElementById('allowInterpolatedFallback').checked = !!settings.allow_interpolated_fallback;
      document.getElementById('providerInput').value = settings.provider || '';
      document.getElementById('redisUrl').value = settings.redis_url || '';
      document.getElementById('redisPrefix').value = settings.redis_prefix || 'moving_client_data';
      document.getElementById('modalBg').value = settings.modal_bg || '#ffffff';
      document.getElementById('panelOpacity').value = Math.round(Number(settings.panel_opacity ?? 0.78) * 100);
      document.getElementById('modalOpacity').value = Math.round(Number(settings.modal_opacity ?? 1.0) * 100);
      document.getElementById('summaryDock').value = settings.summary_dock || 'top-left';
      document.getElementById('detailsDock').value = settings.details_dock || 'top-right';
      document.getElementById('chartDock').value = settings.chart_dock || 'bottom';
    }

    function settingsFromForm() {
      return {
        gps_formula: document.getElementById('gpsFormula').value,
        selected_track_id: document.getElementById('routeTrack').value || 'berlin_karlsruhe',
        use_adb_gps: document.getElementById('useAdbGps').checked,
        use_browser_gps: document.getElementById('useBrowserGps').checked,
        use_accelerometer: document.getElementById('useAccelerometer').checked,
        allow_interpolated_fallback: document.getElementById('allowInterpolatedFallback').checked,
        provider: document.getElementById('providerInput').value.trim(),
        redis_url: document.getElementById('redisUrl').value.trim(),
        redis_prefix: document.getElementById('redisPrefix').value.trim() || 'moving_client_data',
        modal_bg: document.getElementById('modalBg').value,
        panel_opacity: Number(document.getElementById('panelOpacity').value) / 100,
        modal_opacity: Number(document.getElementById('modalOpacity').value) / 100,
        summary_dock: document.getElementById('summaryDock').value,
        details_dock: document.getElementById('detailsDock').value,
        chart_dock: document.getElementById('chartDock').value,
      };
    }

    function browserDistanceKm(a, b) {
      const radiusKm = 6371.0088;
      const lat1 = a.lat * Math.PI / 180;
      const lat2 = b.lat * Math.PI / 180;
      const dLat = (b.lat - a.lat) * Math.PI / 180;
      const dLon = (b.lon - a.lon) * Math.PI / 180;
      const value = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
      return 2 * radiusKm * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value));
    }

    function samplePoint(sample) {
      const loc = sample.location || {};
      if (!Number.isFinite(loc.lat) || !Number.isFinite(loc.lon) || !sample.timestamp_utc) return null;
      const ts = Date.parse(sample.timestamp_utc);
      if (!Number.isFinite(ts)) return null;
      return { lat: loc.lat, lon: loc.lon, ts };
    }

    function fiveSampleSpeedBars(rows) {
      const bars = [];
      for (let i = 0; i <= rows.length - 5; i += 1) {
        const first = samplePoint(rows[i]);
        const last = samplePoint(rows[i + 4]);
        if (!first || !last) continue;
        const hours = (last.ts - first.ts) / 3600000;
        if (!(hours > 0)) continue;
        const speed = browserDistanceKm(first, last) / hours;
        if (speed > 5) {
          bars.push({ index: i + 2, speed_kmh: speed });
        }
      }
      return bars;
    }

    function drawTimeChart() {
      const canvas = document.getElementById('timeChart');
      const rows = chartRows();
      resizePlotForRows(rows);
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      const ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const w = rect.width;
      const h = rect.height;
      ctx.clearRect(0, 0, w, h);
      const pad = { left: 42, right: 42, top: 12, bottom: 28 };
      drawGpsSourceBar(rows);
      const latency = rows.map(s => s.latency_summary?.avg_ms).filter(v => Number.isFinite(v));
      const speedBars = fiveSampleSpeedBars(rows);
      const speeds = speedBars.map(s => s.speed_kmh).filter(v => Number.isFinite(v) && v > 5);
      if (rows.length < 2 || (!latency.length && !speeds.length)) {
        ctx.strokeStyle = '#d8dee4';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(pad.left, pad.top);
        ctx.lineTo(pad.left, h - pad.bottom);
        ctx.lineTo(w - pad.right, h - pad.bottom);
        ctx.stroke();
        ctx.fillStyle = '#52616f';
        ctx.font = '12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
        ctx.fillText('Waiting for enough latency/GPS samples', pad.left + 8, pad.top + 22);
        return;
      }
      const maxLatency = Math.max(100, ...latency);
      const maxSpeed = speeds.length ? Math.max(25, ...speeds) : null;
      document.getElementById('chartScale').textContent = speeds.length
        ? `Left axis latency max ${Math.round(maxLatency)} ms | right axis speed max ${Math.round(maxSpeed)} km/h`
        : `Left axis latency max ${Math.round(maxLatency)} ms | no speed samples yet`;
      const xAt = i => pad.left + (i / Math.max(1, rows.length - 1)) * (w - pad.left - pad.right);
      const yLatency = v => h - pad.bottom - (Math.min(v, maxLatency) / maxLatency) * (h - pad.top - pad.bottom);
      const ySpeed = v => h - pad.bottom - (Math.min(v, maxSpeed || 1) / (maxSpeed || 1)) * (h - pad.top - pad.bottom);
      ctx.strokeStyle = '#d8dee4';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.left, pad.top);
      ctx.lineTo(pad.left, h - pad.bottom);
      ctx.lineTo(w - pad.right, h - pad.bottom);
      ctx.stroke();
      ctx.save();
      ctx.setLineDash([5, 5]);
      ctx.strokeStyle = '#aeb8c2';
      ctx.lineWidth = 0.8;
      ctx.fillStyle = '#52616f';
      ctx.font = '10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
      for (let tick = 0; tick <= 4; tick += 1) {
        const value = Math.round((maxLatency / 4) * tick);
        const y = yLatency(value);
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(w - pad.right, y);
        ctx.stroke();
        ctx.fillText(`${value} ms`, 4, y - 2);
      }
      ctx.restore();
      ctx.save();
      ctx.fillStyle = '#52616f';
      ctx.font = '11px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
      ctx.fillText('Latency (ms)', pad.left + 4, pad.top + 10);
      ctx.textAlign = 'right';
      ctx.fillStyle = '#2f7d45';
      ctx.fillText('Speed (km/h)', w - pad.right - 4, pad.top + 10);
      ctx.restore();
      if (speeds.length) {
        ctx.save();
        ctx.setLineDash([4, 6]);
        ctx.strokeStyle = '#59b66d';
        ctx.lineWidth = 0.8;
        ctx.fillStyle = '#2f7d45';
        ctx.font = '10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
        ctx.textAlign = 'right';
        for (let tick = 0; tick <= 4; tick += 1) {
          const value = Math.round((maxSpeed / 4) * tick);
          const y = ySpeed(value);
          ctx.beginPath();
          ctx.moveTo(pad.left, y);
          ctx.lineTo(w - pad.right, y);
          ctx.stroke();
          ctx.fillText(`${value} km/h`, w - 4, y - 2);
        }
        ctx.restore();
      }
      if (selectedSequence !== null) {
        const selectedIndex = rows.findIndex(s => s.sequence === selectedSequence);
        if (selectedIndex >= 0) {
          const x = xAt(selectedIndex);
          ctx.save();
          ctx.strokeStyle = '#9b5de5';
          ctx.lineWidth = 2;
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(x, pad.top);
          ctx.lineTo(x, h - pad.bottom);
          ctx.stroke();
          ctx.fillStyle = '#9b5de5';
          ctx.font = '11px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
          ctx.fillText(`#${selectedSequence}`, x + 4, pad.top + 12);
          ctx.restore();
        }
      }
      function drawLine(getValue, yFn, stroke) {
        ctx.strokeStyle = stroke;
        ctx.lineWidth = 2;
        ctx.beginPath();
        let started = false;
        rows.forEach((sample, i) => {
          const value = getValue(sample);
          if (!Number.isFinite(value)) return;
          const x = xAt(i);
          const y = yFn(value);
          if (!started) {
            ctx.moveTo(x, y);
            started = true;
          } else {
            ctx.lineTo(x, y);
          }
        });
        if (started) ctx.stroke();
      }
      drawLine(s => s.latency_summary?.avg_ms, yLatency, '#d73027');
      if (speeds.length) {
        ctx.save();
        ctx.strokeStyle = '#9b5de5';
        ctx.lineWidth = 2;
        ctx.beginPath();
        speedBars.forEach((bar, idx) => {
          const x = xAt(bar.index);
          const y = ySpeed(bar.speed_kmh);
          if (idx === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.restore();
      }
      rows.forEach((sample, i) => {
        if ((sample.sequence || i + 1) % 5 !== 0) return;
        const x = xAt(i);
        const lat = sample.latency_summary?.avg_ms;
        ctx.font = '10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
        if (Number.isFinite(lat)) {
          ctx.fillStyle = '#d73027';
          ctx.fillText(`${Math.round(lat)}ms`, x + 3, Math.max(pad.top + 18, yLatency(lat) - 6));
        }
      });
      speedBars.forEach(bar => {
        const x = xAt(bar.index);
        const y = ySpeed(bar.speed_kmh);
        ctx.fillStyle = '#9b5de5';
        ctx.fillRect(x - 2, y, 4, h - pad.bottom - y);
        ctx.beginPath();
        ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.font = '10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
        ctx.fillText(`${Math.round(bar.speed_kmh)}km/h`, x + 4, Math.max(pad.top + 18, y - 6));
      });
      ctx.fillStyle = '#52616f';
      ctx.font = '11px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
      ctx.fillText('0', 8, h - pad.bottom + 4);
      ctx.fillText(`${Math.round(maxLatency)}ms`, 4, pad.top + 4);
      if (speeds.length) {
        ctx.textAlign = 'right';
        ctx.fillStyle = '#9b5de5';
        ctx.fillText(`${Math.round(maxSpeed)}km/h`, w - pad.right + 5, pad.top + 4);
        ctx.textAlign = 'left';
      }
      const first = rows[0]?.timestamp_utc?.slice(11, 19) || '';
      const last = rows[rows.length - 1]?.timestamp_utc?.slice(11, 19) || '';
      ctx.fillText(first, pad.left, h - 8);
      ctx.textAlign = 'right';
      ctx.fillText(last, w - pad.right, h - 8);
      ctx.textAlign = 'left';
      const scroll = document.getElementById('plotPane');
      if (scroll && !gpsBarDrag && plotFollowLatest) scroll.scrollLeft = scroll.scrollWidth;
    }

    function updatePlotFollowLatest() {
      const scroll = document.getElementById('plotPane');
      if (!scroll) return;
      plotFollowLatest = scroll.scrollLeft + scroll.clientWidth >= scroll.scrollWidth - 12;
    }

    function pagePlot(direction) {
      const scroll = document.getElementById('plotPane');
      if (!scroll) return;
      const delta = Math.max(160, Math.floor(scroll.clientWidth * 0.85)) * direction;
      scroll.scrollBy({ left: delta, behavior: 'smooth' });
      if (direction < 0) {
        plotFollowLatest = false;
      } else {
        window.setTimeout(updatePlotFollowLatest, 280);
      }
    }

    function chartXForSequence(sequence) {
      const canvas = document.getElementById('timeChart');
      const rect = canvas.getBoundingClientRect();
      const rows = chartRows();
      const index = rows.findIndex(s => s.sequence === sequence);
      if (index < 0) return null;
      const padLeft = 42;
      const padRight = 42;
      return padLeft + (index / Math.max(1, rows.length - 1)) * (rect.width - padLeft - padRight);
    }

    function selectSample(sequence) {
      selectedSequence = sequence;
      for (const [seq, marker] of markers.entries()) {
        const el = marker.getElement();
        if (el) el.querySelector('.sample-marker')?.classList.toggle('selected', seq === sequence);
      }
      drawTimeChart();
      const x = chartXForSequence(sequence);
      const scroll = document.getElementById('plotPane');
      if (scroll && x !== null) {
        scroll.scrollLeft = Math.max(0, x - scroll.clientWidth / 2);
      }
      document.getElementById('status').textContent = `Selected sample #${sequence}; plot scrolled to its time position.`;
    }

    function esc(v) {
      return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function propGrid(rows) {
      return `<div class="prop-grid">${rows.map(([k, v]) => `<div class="key">${esc(k)}</div><div>${esc(v ?? '')}</div>`).join('')}</div>`;
    }

    function hasUsableLocation(sample) {
      const loc = sample?.location || {};
      return Number.isFinite(loc.lat) && Number.isFinite(loc.lon);
    }

    function renderUnmatchedSamples() {
      const box = document.getElementById('unmatchedSamples');
      const samples = Array.from(unmatchedSamples.values()).slice(-25).reverse();
      if (!samples.length) {
        box.innerHTML = '<div>No unmatched latency samples.</div>';
        return;
      }
      box.innerHTML = samples.map(sample => {
        const summary = sample.latency_summary || {};
        return `
          <div class="unmatched-item">
            <strong>#${esc(sample.sequence)}</strong>
            <div class="unmatched-meta">${esc(sample.timestamp_utc || '')}<br>latency ${esc(summary.avg_ms ?? 'n/a')} ms</div>
            <button type="button" data-assign-seq="${esc(sample.sequence)}">Pin</button>
          </div>
        `;
      }).join('');
      box.querySelectorAll('[data-assign-seq]').forEach(button => {
        button.addEventListener('click', () => {
          pendingLocationAssignment = Number(button.dataset.assignSeq);
          document.getElementById('unmatchedHint').textContent = `Click the map to assign a manual location to sample #${pendingLocationAssignment}.`;
          document.getElementById('status').textContent = `Pin mode active for unmatched sample #${pendingLocationAssignment}. Click the map position to store it.`;
        });
      });
    }

    async function assignSampleLocation(sequence, latlng) {
      const res = await fetch('/api/assign-location', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sequence, lat: latlng.lat, lon: latlng.lng })
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'assignment failed');
      unmatchedSamples.delete(sequence);
      renderUnmatchedSamples();
      addSample(data.sample);
      document.getElementById('unmatchedHint').textContent = 'Samples without GPS are not shown on the map. Select one, then click the map to pin and store a location.';
      document.getElementById('status').textContent = `Stored manual map pin for sample #${sequence}.`;
    }

    async function assignAllUnmatched(latlng) {
      const sequences = Array.from(unmatchedSamples.keys());
      if (!sequences.length) {
        document.getElementById('status').textContent = 'No unmatched samples to pin.';
        return;
      }
      document.getElementById('status').textContent = `Pinning ${sequences.length} unmatched samples...`;
      let ok = 0;
      const failures = [];
      for (const sequence of sequences) {
        try {
          await assignSampleLocation(sequence, latlng);
          ok += 1;
        } catch (err) {
          failures.push(`#${sequence}: ${err.message}`);
        }
      }
      pendingPinAll = false;
      document.getElementById('unmatchedHint').textContent = 'Samples without GPS are not shown on the map. Select one, then click the map to pin and store a location.';
      document.getElementById('status').textContent = failures.length
        ? `Pinned ${ok} samples; failures: ${failures.join(', ')}`
        : `Pinned ${ok} unmatched samples to ${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}.`;
    }

    function latestLatLngObject() {
      if (!latestLatLng) return null;
      if (Array.isArray(latestLatLng)) return { lat: latestLatLng[0], lng: latestLatLng[1] };
      return latestLatLng;
    }

    function accessPointName(sample) {
      const router = sample?.router_geo || {};
      const firstHop = router.first_hop || {};
      const name = router.access_point_name || firstHop.host || (firstHop.ips || [])[0] || '';
      return name && name !== '*' ? name : 'waiting for traceroute hop 1';
    }

    async function clientLog(level, source, message, detail) {
      try {
        await fetch('/api/client-log', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ level, source, message, detail })
        });
        await refreshLogs();
      } catch (_) {}
    }

    async function refreshLogs() {
      try {
        const res = await fetch('/api/logs');
        const data = await res.json();
        const logs = (data.logs || []).slice().reverse();
        renderLogGrid('errorRows', logs.filter(log => ['error', 'warning'].includes(String(log.level || '').toLowerCase())), 'No errors or warnings');
        renderLogGrid('eventRows', logs.filter(log => !['error', 'warning'].includes(String(log.level || '').toLowerCase())), 'No application events');
      } catch (err) {
        const fallback = logGridHeader() + `<div></div><div></div><div>log-view</div><div>${esc(String(err))}</div>`;
        document.getElementById('errorRows').innerHTML = fallback;
        document.getElementById('eventRows').innerHTML = fallback;
      }
    }

    function logGridHeader() {
      return ['Date', 'Time', 'GPS/source kind', 'Normalized error']
        .map(v => `<div class="head">${esc(v)}</div>`).join('');
    }

    function logFrequencyKey(normalized) {
      return `${normalized.kind}|${normalized.message}`.toLowerCase();
    }

    function pastelLogColor(log, count, minCount, maxCount) {
      const level = String(log.level || 'info').toLowerCase();
      const isError = ['error', 'warning'].includes(level);
      const ratio = maxCount === minCount ? 1 : (count - minCount) / Math.max(1, maxCount - minCount);
      if (isError) {
        const lightness = 96 - ratio * 10;
        return `hsl(5 82% ${lightness}%)`;
      }
      const lightness = 96 - ratio * 12;
      return `hsl(126 48% ${lightness}%)`;
    }

    function renderLogGrid(elementId, logs, emptyMessage) {
      const normalizedRows = logs.map(log => ({ log, normalized: normalizeLog(log) }));
      const counts = new Map();
      normalizedRows.forEach(row => {
        const key = logFrequencyKey(row.normalized);
        counts.set(key, (counts.get(key) || 0) + 1);
      });
      const countValues = Array.from(counts.values());
      const minCount = countValues.length ? Math.min(...countValues) : 0;
      const maxCount = countValues.length ? Math.max(...countValues) : 0;
      const rows = normalizedRows.map((row, rowIndex) => {
        const normalized = row.normalized;
        const alt = rowIndex % 2 === 1 ? ' alt' : '';
        const count = counts.get(logFrequencyKey(normalized)) || 1;
        const style = ` style="background:${pastelLogColor(row.log, count, minCount, maxCount)}" title="frequency ${count}"`;
        return [
          `<div class="${alt}"${style}>${esc(normalized.date)}</div>`,
          `<div class="${alt}"${style}>${esc(normalized.time)}</div>`,
          `<div class="${alt}"${style}>${esc(normalized.kind)} · ${count}x</div>`,
          `<div class="${alt}"${style}>${esc(normalized.message)}</div>`
        ].join('');
      }).join('');
      document.getElementById(elementId).innerHTML = rows
        ? logGridHeader() + rows
        : logGridHeader() + `<div></div><div></div><div></div><div>${esc(emptyMessage)}</div>`;
    }

    function redisQueryForPreset(value) {
      const base = 'SELECT date,time,level,source,message FROM redis_logs WHERE type IN (application_events,error_logs) AND timestamp BETWEEN ';
      const ranges = {
        today: 'today 00:00 AND now',
        yesterday: 'yesterday 00:00 AND yesterday 23:59',
        last_month: 'last month',
        last_6_months: 'last 6 months'
      };
      return `${base}${ranges[value] || ranges.yesterday}`;
    }

    async function runRedisQuery() {
      const query = document.getElementById('redisQueryText').value;
      const status = document.getElementById('redisQueryStatus');
      status.textContent = 'Executing Redis log query...';
      try {
        const res = await fetch('/api/redis-log-query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query })
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'query failed');
        renderLogGrid('redisQueryRows', data.logs || [], 'No Redis log rows matched this query');
        status.textContent = `Returned ${data.count} rows in ${data.execution_ms} ms. Range: ${data.range?.start || 'any'} to ${data.range?.end || 'any'}.`;
      } catch (err) {
        document.getElementById('redisQueryRows').innerHTML = logGridHeader() + `<div></div><div></div><div>redis-query</div><div>${esc(err.message)}</div>`;
        status.textContent = `Query failed: ${err.message}`;
      }
    }

    function formatBytes(bytes) {
      const value = Number(bytes || 0);
      if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
      if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
      if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
      return `${value} B`;
    }

    function countryFromOsmName(name) {
      return String(name || '').replace(/-latest\.osm\.pbf$/i, '');
    }

    function renderOsmStatus(data) {
      const info = document.getElementById('osmStorageInfo');
      const path = document.getElementById('osmStoragePath');
      const list = document.getElementById('osmFileList');
      const files = data.files || [];
      const downloads = Object.values(data.downloads || {});
      info.textContent = `OSM data: ${formatBytes(data.total_bytes)} across ${files.length} downloaded country file${files.length === 1 ? '' : 's'}.`;
      path.textContent = `Directory: ${data.directory || ''}`;
      const rows = [];
      for (const item of downloads.sort((a, b) => String(a.country).localeCompare(String(b.country)))) {
        const pct = Number.isFinite(item.percent) ? item.percent : 0;
        const total = item.total_bytes ? ` / ${formatBytes(item.total_bytes)}` : '';
        rows.push(`
          <div class="osm-file-item">
            <div class="osm-file-name">${esc(item.country || countryFromOsmName(item.path))} · ${esc(item.status || '')} · ${formatBytes(item.bytes)}${total}</div>
            <progress max="100" value="${esc(pct)}"></progress>
            <div class="osm-path">${esc(item.path || '')}</div>
          </div>
        `);
      }
      for (const file of files) {
        const country = countryFromOsmName(file.name);
        if (downloads.some(d => d.country === country)) continue;
        rows.push(`
          <div class="osm-file-item">
            <div class="osm-file-name">${esc(country)} · downloaded · ${formatBytes(file.bytes)}</div>
            <progress max="100" value="100"></progress>
            <div class="osm-path">${esc(file.path)}</div>
          </div>
        `);
      }
      list.innerHTML = rows.join('') || '<div class="osm-status">No OSM country extracts downloaded yet.</div>';
    }

    async function refreshOsmStatus() {
      const res = await fetch('/api/osm-status');
      const data = await res.json();
      renderOsmStatus(data);
      return data;
    }

    async function runOsmConsistencyCheck() {
      const out = document.getElementById('osmConsistencyReport');
      out.textContent = 'Checking Redis GPS samples against downloaded OSM countries...';
      try {
        const res = await fetch('/api/osm-consistency');
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'consistency check failed');
        out.textContent = data.report || JSON.stringify(data, null, 2);
      } catch (err) {
        out.textContent = `OSM consistency check failed: ${err.message}`;
      }
    }

    async function downloadOsmCountry() {
      const country = document.getElementById('osmCountry').value.trim();
      const status = document.getElementById('osmDownloadStatus');
      if (!country) {
        status.textContent = 'Type a country first.';
        return;
      }
      status.textContent = `Starting ${country} download...`;
      try {
        const res = await fetch('/api/osm-download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ country })
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'download failed');
        status.textContent = `Downloading ${data.country} to ${data.path}`;
        await refreshOsmStatus();
      } catch (err) {
        status.textContent = `OSM download failed: ${err.message}`;
      }
    }

    function parseLogDetail(detail) {
      if (!detail) return {};
      if (typeof detail === 'object') return detail;
      if (typeof detail === 'string') {
        try { return JSON.parse(detail); } catch (_) { return { raw: detail }; }
      }
      return { raw: String(detail) };
    }

    function normalizeLog(log) {
      const timestamp = String(log.timestamp_utc || '');
      const [datePart, timePartRaw] = timestamp.split('T');
      const timePart = (timePartRaw || '').replace('+00:00', 'Z').slice(0, 8);
      const detail = parseLogDetail(log.detail);
      const source = String(log.source || 'unknown');
      const level = String(log.level || 'info');
      let kind = source;
      let message = String(log.message || '').replace(/\s+/g, ' ').trim();

      if (source === 'browser-gps') {
        kind = detail.code ? `browser GPS code ${detail.code}` : 'browser GPS';
        if (detail.code === 1) message = 'Permission denied: allow location for the browser and OS.';
        else if (detail.code === 2) message = 'Position unavailable: OS/browser could not determine a GPS/Wi-Fi/cell location.';
        else if (detail.code === 3) message = 'Timeout: no location fix arrived before the browser timeout.';
        else if (/Starting browser GPS watch/i.test(message)) message = 'Browser GPS watch started.';
      } else if (source === 'settings') {
        kind = 'settings';
        message = 'Settings updated.';
      } else if (source === 'accelerometer') {
        kind = 'accelerometer';
        message = /started/i.test(message) ? 'Accelerometer listener started.' : message;
      } else if (source === 'adb') {
        kind = 'ADB';
        message = message || 'ADB event.';
      }

      if (level !== 'info' && !message.toLowerCase().startsWith(level.toLowerCase())) {
        message = `${level}: ${message}`;
      }
      return { date: datePart || '', time: timePart || '', kind, message };
    }

    async function refreshAdbDevices() {
      const select = document.getElementById('adbDevices');
      try {
        const res = await fetch('/api/adb-devices');
        const data = await res.json();
        select.innerHTML = '<option value="">ADB auto/no device</option>';
        for (const device of data.devices || []) {
          const opt = document.createElement('option');
          opt.value = device.serial;
          opt.textContent = `${device.serial} · ${device.state}`;
          if (device.serial === data.selected) opt.selected = true;
          select.appendChild(opt);
        }
        const wireless = data.wireless_connect;
        const suffix = wireless ? ` · wireless ${data.wireless_serial}: ${wireless.stdout || wireless.stderr || 'attempted'}` : '';
        document.getElementById('status').textContent = (data.devices || []).length
          ? `ADB devices found: ${(data.devices || []).length}${suffix}`
          : `ADB has no visible devices. Tried wireless ${data.wireless_serial}. Check wireless debugging and pairing.${suffix}`;
      } catch (err) {
        document.getElementById('status').textContent = `ADB refresh failed: ${err}`;
        clientLog('error', 'adb', 'ADB refresh failed', String(err));
      }
    }

    function addSample(sample) {
      latestSample = sample;
      if (hasUsableLocation(sample)) unmatchedSamples.delete(sample.sequence);
      else unmatchedSamples.set(sample.sequence, sample);
      renderUnmatchedSamples();
      document.getElementById('samples').textContent = sample.sequence ?? '--';
      const summary = sample.latency_summary || {};
      const radio = sample.radio || {};
      const loc = sample.location || {};
      const motion = sample.motion || {};
      const wifi = sample.wifi || {};
      const connectionErrors = sample.connection_errors || {};
      const dnsServers = sample.dns_servers || [];
      const placeInfo = sample.place_info || {};
      const locationSource = loc.source || (loc.interpolated ? 'interpolated' : 'unknown');
      const rawLoc = sample.raw_location || {};
      if (!plottedSamples.some(s => s.sequence === sample.sequence)) {
        plottedSamples.push(sample);
        if (plottedSamples.length > CHART_SAMPLE_LIMIT) plottedSamples.shift();
      } else {
        const idx = plottedSamples.findIndex(s => s.sequence === sample.sequence);
        if (idx >= 0) plottedSamples[idx] = sample;
      }
      document.getElementById('avg').textContent = summary.avg_ms ? `${summary.avg_ms} ms` : '--';
      document.getElementById('speed').textContent = Number.isFinite(motion.speed_kmh) ? `${motion.speed_kmh} km/h` : '--';
      document.getElementById('wifi').textContent = accessPointName(sample);
      updateLatestGpsMetric(sample);
      document.getElementById('details').innerHTML = `<strong>Latest sample</strong>` + propGrid([
        ['Time', sample.timestamp_utc],
        ['Latency min/avg/max', `${summary.min_ms} / ${summary.avg_ms} / ${summary.max_ms} ms`],
        ['Latency method', 'HTTPS curl timings to domain names; no ping. DNS lookup, TCP connect, TLS, first byte, and total time are captured per target.'],
        ['DNS name servers', dnsServers.join(', ')],
        ['Speed / distance', `${motion.speed_kmh ?? ''} km/h / ${motion.distance_from_previous_km ?? ''} km`],
        ['Location source', locationSource],
        ['Place', placeInfo.label || 'pending reverse geocode'],
        ['POI', placeInfo.poi || 'none detected'],
        ['POI type/category', [placeInfo.rawType, placeInfo.category].filter(Boolean).join(' / ') || 'n/a'],
        ['Selected correction track', sample.position_correction?.track_name || loc.track_name || sample.settings?.selected_track_id || 'n/a'],
        ['Route inference', sample.position_correction?.track_inference ? JSON.stringify(sample.position_correction.track_inference) : 'n/a'],
        ['Raw GPS', rawLoc.lat ? `${rawLoc.lat}, ${rawLoc.lon}` : 'none'],
        ['Corrected GPS error', sample.position_error_km != null ? `${sample.position_error_km} km` : 'n/a'],
        ['Correction method', sample.position_correction?.method || ''],
        ['Correction status', sample.position_correction?.rejected ? `rejected: ${sample.position_correction?.reason || ''}` : (sample.position_correction?.applied ? 'applied' : 'not applied')],
        ['Accelerometer', sample.motion_sensor ? JSON.stringify(sample.motion_sensor.acceleration || sample.motion_sensor.acceleration_including_gravity || {}) : 'not available'],
        ['Access point name', accessPointName(sample)],
        ['Access point source', 'traceroute hop 1'],
        ['Wi-Fi SSID / MAC / IP', `${wifi.ssid} / ${wifi.mac_address} / ${wifi.ip_address}`],
        ['Wi-Fi RSSI / noise / SNR', wifi.rssi_dbm != null ? `${wifi.rssi_dbm} dBm / ${wifi.noise_dbm ?? 'n/a'} dBm / ${wifi.snr_db ?? 'n/a'} dB` : (wifi.rssi_status || wifi.wifi_metrics_error || 'not available')],
        ['Wi-Fi PHY / channel / Tx rate', `${wifi.phy_mode || 'n/a'} / ${wifi.channel || 'n/a'} / ${wifi.tx_rate_mbps != null ? `${wifi.tx_rate_mbps} Mbps` : 'n/a'}`],
        ['Connection errors', `${connectionErrors.error_count ?? 0}/${connectionErrors.target_count ?? 0} targets failed (${connectionErrors.error_rate_percent ?? 0}%)`],
        ['Serving node', `${sample.router_geo?.serving_node?.name || ''} ${sample.router_geo?.serving_node?.ip || ''}`],
        ['Router GeoIP', `${sample.router_geo?.selected_public_hop?.ip || ''} ${sample.router_geo?.geoip?.city || ''} ${sample.router_geo?.geoip?.org || ''}`],
        ['ADB', (sample.adb || {}).message || (sample.adb || {}).selected_serial || 'ok'],
        ['Redis', (window.latestStatus || {}).redis?.message || 'not configured'],
      ]);
      drawTimeChart();
      updateVirtualPath();

      if (!loc.lat || !loc.lon) return;
      const latlng = [loc.lat, loc.lon];
      latestLatLng = latlng;
      const popup = `
        <div class="sample-popup">
        <div class="popup-head"><strong>Sample #${esc(sample.sequence)}</strong><button class="popup-close" onclick="map.closePopup()" type="button">Close</button></div>
        ${propGrid([
          ['Time', sample.timestamp_utc],
          ['Avg latency', `${summary.avg_ms} ms`],
          ['Location source', locationSource],
          ['Selected correction track', sample.position_correction?.track_name || loc.track_name || sample.settings?.selected_track_id || 'n/a'],
          ['Route inference', sample.position_correction?.track_inference ? JSON.stringify(sample.position_correction.track_inference) : 'n/a'],
          ['Raw GPS', rawLoc.lat ? `${rawLoc.lat}, ${rawLoc.lon}` : 'none'],
          ['Corrected GPS error', sample.position_error_km != null ? `${sample.position_error_km} km` : 'n/a'],
          ['Correction status', sample.position_correction?.rejected ? `rejected: ${sample.position_correction?.reason || ''}` : (sample.position_correction?.applied ? 'applied' : 'not applied')],
          ['Position', `${loc.lat}, ${loc.lon}`],
          ['DNS name servers', dnsServers.join(', ')],
          ['Wi-Fi RSSI / SNR', wifi.rssi_dbm != null ? `${wifi.rssi_dbm} dBm / ${wifi.snr_db ?? 'n/a'} dB` : (wifi.rssi_status || 'not available')],
          ['Connection errors', `${connectionErrors.error_count ?? 0}/${connectionErrors.target_count ?? 0} targets failed (${connectionErrors.error_rate_percent ?? 0}%)`],
          ['Serving node', `${sample.router_geo?.serving_node?.name || ''} ${sample.router_geo?.serving_node?.ip || ''}`],
          ['Router IP / GeoIP', `${sample.router_geo?.selected_public_hop?.ip || ''} / ${sample.router_geo?.geoip?.city || ''} ${sample.router_geo?.geoip?.country || ''}`],
          ['Router org', sample.router_geo?.geoip?.org || ''],
          ['NR RSRP / RSRQ / SINR', `${radio.nr_ss_rsrp || ''} / ${radio.nr_ss_rsrq || ''} / ${radio.nr_ss_sinr || ''}`],
          ['LTE RSRP / RSRQ / RSSNR', `${radio.lte_rsrp || ''} / ${radio.lte_rsrq || ''} / ${radio.lte_rssnr || ''}`],
        ])}
        </div>
      `;
      if (!markers.has(sample.sequence)) {
        const marker = L.marker(latlng, {
          icon: L.divIcon({
            className: '',
            html: `<div class="sample-marker" style="background:${color(summary.avg_ms)}">${esc(sample.sequence)}</div>`,
            iconSize: [24, 24],
            iconAnchor: [12, 12],
            popupAnchor: [0, -12],
            tooltipAnchor: [0, -14]
          })
        })
          .bindPopup(popup)
          .bindTooltip(`Sample #${esc(sample.sequence)} · latency ${esc(summary.avg_ms)} ms`, {
            direction: 'top',
            opacity: 0.95,
            sticky: true
          })
          .on('mouseover', function () { this.openTooltip(); })
          .on('click', function () {
            selectSample(sample.sequence);
            this.openPopup();
          })
          .addTo(map);
        markers.set(sample.sequence, marker);
        const previous = routeLatLngs[routeLatLngs.length - 1];
        routeLatLngs.push(latlng);
        if (previous) {
          const segment = L.polyline([previous, latlng], {
            color: color(summary.avg_ms),
            weight: 7,
            opacity: 0.95
          }).addTo(map);
          segment.bringToFront();
          routeSegments.push(segment);
        }
        const routerGeo = sample.router_geo?.geoip;
        if (routerGeo?.lat && routerGeo?.lon && !routerMarkers.has(sample.sequence)) {
          const routerLatLng = [routerGeo.lat, routerGeo.lon];
          routerRouteLatLngs.push(routerLatLng);
          const routerMarker = L.circleMarker(routerLatLng, {
            radius: 5,
            color: '#9b5de5',
            fillColor: '#9b5de5',
            fillOpacity: 0.85,
            weight: 2
          }).bindTooltip(`Router ${sample.router_geo?.selected_public_hop?.ip || ''} · ${routerGeo.city || ''} · ${routerGeo.org || ''}`, { sticky: true }).addTo(map);
          routerMarkers.set(sample.sequence, routerMarker);
          if (routerLine) routerLine.setLatLngs(routerRouteLatLngs);
          else routerLine = L.polyline(routerRouteLatLngs, { color: '#9b5de5', weight: 4, opacity: 0.82 }).addTo(map);
        }
        if (!hasFit && routeLatLngs.length >= 1) {
          map.setView(latlng, 13);
          hasFit = true;
        } else {
          map.panTo(latlng, { animate: true, duration: 0.5 });
        }
      }
    }

    async function loadInitial() {
      await loadSettings();
      drawReferenceTracks();
      const res = await fetch('/api/samples');
      const data = await res.json();
      if (data.route_tracks) routeTracks = data.route_tracks;
      if (data.settings) applySettings(data.settings);
      drawReferenceTracks();
      window.latestStatus = data.status || {};
      document.getElementById('status').textContent = `Connected. Log: ${data.status?.log || ''}`;
      for (const sample of data.samples) addSample(sample);
      if (routeLatLngs.length > 1) map.fitBounds(routeLatLngs, { padding: [30, 30] });
      refreshAdbDevices();
      refreshLogs();
    }

    loadInitial().catch(err => {
      document.getElementById('status').textContent = `Initial load failed: ${err}`;
    });
    setupGpsSourceBarInteractions();
    document.getElementById('plotPageLeft').addEventListener('click', () => pagePlot(-1));
    document.getElementById('plotPageRight').addEventListener('click', () => pagePlot(1));
    document.getElementById('plotPane').addEventListener('scroll', updatePlotFollowLatest);
    document.getElementById('redisQueryPreset').addEventListener('change', ev => {
      document.getElementById('redisQueryText').value = redisQueryForPreset(ev.target.value);
    });
    document.getElementById('downloadOsmCountry').addEventListener('click', downloadOsmCountry);
    document.getElementById('checkOsmConsistency').addEventListener('click', runOsmConsistencyCheck);

    const events = new EventSource('/events');
    events.onopen = () => document.getElementById('status').textContent = 'Live sampler connected.';
    events.onerror = () => document.getElementById('status').textContent = 'Live stream reconnecting...';
    events.addEventListener('sample', ev => addSample(JSON.parse(ev.data)));
    map.on('click', ev => {
      if (pendingPinAll) {
        pendingPinAll = false;
        assignAllUnmatched(ev.latlng).catch(err => {
          document.getElementById('status').textContent = `Pin all failed: ${err.message}`;
          clientLog('error', 'manual-pin', 'Pin all assignment failed', { error: String(err) });
        });
        return;
      }
      if (pendingLocationAssignment === null) return;
      const sequence = pendingLocationAssignment;
      pendingLocationAssignment = null;
      assignSampleLocation(sequence, ev.latlng).catch(err => {
        document.getElementById('status').textContent = `Manual pin failed: ${err.message}`;
        clientLog('error', 'manual-pin', 'Manual pin assignment failed', { sequence, error: String(err) });
      });
    });
    document.getElementById('pinAllUnmatched').addEventListener('click', () => {
      const latlng = latestLatLngObject();
      if (latlng) {
        assignAllUnmatched(latlng).catch(err => {
          document.getElementById('status').textContent = `Pin all failed: ${err.message}`;
          clientLog('error', 'manual-pin', 'Pin all assignment failed', { error: String(err) });
        });
        return;
      }
      pendingPinAll = true;
      pendingLocationAssignment = null;
      document.getElementById('unmatchedHint').textContent = 'No current GPS position is available. Click the map once to pin all unmatched samples there.';
      document.getElementById('status').textContent = 'Pin all mode active. Click the map position to store it for all unmatched samples.';
    });

    document.getElementById('zoomLatest').addEventListener('click', () => {
      if (latestLatLng) {
        map.setView(latestLatLng, Math.max(map.getZoom(), 15), { animate: true });
      } else {
        document.getElementById('status').textContent = 'No GPS sample yet; connect/authorize ADB and keep location active.';
      }
    });
    const helpModal = document.getElementById('helpModal');
    const settingsModal = document.getElementById('settingsModal');
    document.getElementById('openSettings').addEventListener('click', async () => {
      const settings = await loadSettings();
      fillSettingsForm(settings);
      await refreshOsmStatus().catch(err => {
        document.getElementById('osmStorageInfo').textContent = `OSM status failed: ${err.message}`;
      });
      if (!osmStatusTimer) osmStatusTimer = window.setInterval(() => refreshOsmStatus().catch(() => {}), 1500);
      settingsModal.classList.add('open');
    });
    document.getElementById('closeSettings').addEventListener('click', () => {
      settingsModal.classList.remove('open');
      if (osmStatusTimer) {
        window.clearInterval(osmStatusTimer);
        osmStatusTimer = null;
      }
    });
    settingsModal.addEventListener('click', ev => {
      if (ev.target === settingsModal) {
        settingsModal.classList.remove('open');
        if (osmStatusTimer) {
          window.clearInterval(osmStatusTimer);
          osmStatusTimer = null;
        }
      }
    });
    document.getElementById('saveSettings').addEventListener('click', async () => {
      const settings = settingsFromForm();
      applySettings(settings);
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
      });
      const data = await res.json();
      if (data.ok) {
        applySettings(data.settings);
        document.getElementById('status').textContent = 'Settings saved. New samples will use the selected GPS formula and sensors.';
        settingsModal.classList.remove('open');
      } else {
        document.getElementById('status').textContent = `Settings failed: ${data.error || 'unknown error'}`;
      }
    });
    document.getElementById('planGcpRedis').addEventListener('click', async () => {
      const out = document.getElementById('gcpRedisPlan');
      out.textContent = 'Building backend-held GCP Redis plan...';
      const payload = {
        project: document.getElementById('gcpRedisProject').value.trim(),
        region: document.getElementById('gcpRedisRegion').value.trim() || 'europe-west1',
        instance: document.getElementById('gcpRedisInstance').value.trim() || 'moving-target-redis',
        tier: document.getElementById('gcpRedisTier').value,
        memory_gb: document.getElementById('gcpRedisMemory').value || '1',
        network: document.getElementById('gcpRedisNetwork').value.trim() || 'default',
        redis_prefix: document.getElementById('redisPrefix').value.trim() || 'moving_client_data'
      };
      try {
        const res = await fetch('/api/gcp-redis-plan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'plan failed');
        out.textContent = [
          data.note,
          '',
          ...data.commands
        ].join('\\n');
        document.getElementById('status').textContent = 'GCP Redis plan generated. No cloud resources were created.';
      } catch (err) {
        out.textContent = `GCP Redis plan failed: ${err.message}`;
      }
    });
    ['panelOpacity', 'modalOpacity', 'modalBg', 'summaryDock', 'detailsDock', 'chartDock', 'routeTrack'].forEach(id => {
      document.getElementById(id).addEventListener('input', () => applySettings(settingsFromForm()));
      document.getElementById(id).addEventListener('change', () => applySettings(settingsFromForm()));
    });
    document.getElementById('openHelp').addEventListener('click', () => helpModal.classList.add('open'));
    document.getElementById('closeHelp').addEventListener('click', () => helpModal.classList.remove('open'));
    helpModal.addEventListener('click', ev => {
      if (ev.target === helpModal) helpModal.classList.remove('open');
    });
    document.querySelectorAll('.collapseBtn').forEach(button => {
      button.addEventListener('click', () => {
        const panel = document.getElementById(button.dataset.target);
        panel.classList.toggle('collapsed');
        button.textContent = panel.classList.contains('collapsed') ? 'Expand' : 'Collapse';
        setTimeout(drawTimeChart, 60);
      });
    });
    document.querySelectorAll('.tab').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === button));
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('hidden', p.id !== button.dataset.tab));
        if (button.dataset.tab === 'errorPane' || button.dataset.tab === 'eventPane') refreshLogs();
        setTimeout(drawTimeChart, 60);
      });
    });
    document.getElementById('refreshAdb').addEventListener('click', refreshAdbDevices);
    document.getElementById('connectAdb').addEventListener('click', async () => {
      let serial = '100.77.37.113:5555';
      const select = document.getElementById('adbDevices');
      if ([...select.options].some(opt => opt.value === serial)) select.value = serial;
      const res = await fetch('/api/adb-select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ serial })
      });
      const data = await res.json();
      document.getElementById('status').textContent = data.ok ? `ADB wireless selected: ${data.serial || 'auto'}` : `ADB wireless selection failed`;
      await refreshAdbDevices();
    });
    document.getElementById('saveWifiLabel').addEventListener('click', async () => {
      const ssid = document.getElementById('wifiLabel').value.trim();
      if (!ssid) {
        document.getElementById('status').textContent = 'Type a Wi-Fi name first.';
        return;
      }
      const res = await fetch('/api/wifi-label', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ssid })
      });
      const data = await res.json();
      document.getElementById('status').textContent = data.ok ? `Wi-Fi name saved: ${data.ssid}` : `Wi-Fi name failed: ${data.error}`;
    });
    document.getElementById('detectMacWifi').addEventListener('click', async () => {
      try {
        const res = await fetch('/api/wifi-info');
        const data = await res.json();
        const wifi = data.wifi || {};
        if (wifi.ssid && wifi.ssid !== 'unknown' && !/^<redacted>$/i.test(wifi.ssid)) {
          document.getElementById('wifiLabel').value = wifi.ssid;
          document.getElementById('status').textContent = `Mac Wi-Fi detected: ${wifi.ssid}. Click Save Wi-Fi name to use it as override.`;
        } else {
          document.getElementById('status').textContent = wifi.ssid_probe_error || wifi.wifi_metrics_error || 'Mac Wi-Fi SSID is unavailable or redacted by macOS Location Services.';
        }
      } catch (err) {
        document.getElementById('status').textContent = `Mac Wi-Fi detection failed: ${err.message}`;
      }
    });
    document.getElementById('startBrowserGps').addEventListener('click', () => {
      const button = document.getElementById('startBrowserGps');
      if (gpsWatchId !== null) {
        navigator.geolocation?.clearWatch(gpsWatchId);
        gpsWatchId = null;
        button.textContent = 'Start browser GPS';
        document.getElementById('status').textContent = 'Browser GPS watch stopped.';
        clientLog('info', 'browser-gps', 'Browser GPS watch stopped', { protocol: location.protocol, host: location.host });
        return;
      }
      if (!navigator.geolocation) {
        const msg = 'Browser geolocation is not available. This browser/runtime does not expose navigator.geolocation.';
        document.getElementById('status').textContent = msg;
        clientLog('error', 'browser-gps', msg, { protocol: location.protocol, host: location.host });
        return;
      }
      clientLog('info', 'browser-gps', 'Starting browser GPS watch', { protocol: location.protocol, host: location.host });
      gpsWatchId = navigator.geolocation.watchPosition(async position => {
        const payload = {
          lat: position.coords.latitude,
          lon: position.coords.longitude,
          accuracy_m: position.coords.accuracy,
          altitude_m: position.coords.altitude,
          altitude_accuracy_m: position.coords.altitudeAccuracy,
          heading_deg: position.coords.heading,
          speed_mps: position.coords.speed,
          browser_timestamp_ms: position.timestamp
        };
        try {
          const res = await fetch('/api/browser-location', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
          const data = await res.json();
          const gpsLoc = data.location || payload;
          map.setView([gpsLoc.lat, gpsLoc.lon], Math.max(map.getZoom(), 15), { animate: true });
          document.getElementById('status').textContent = `Browser GPS active: ${payload.lat.toFixed(5)}, ${payload.lon.toFixed(5)} accuracy ${Math.round(payload.accuracy_m || 0)}m latency ${data.location?.latency_summary?.avg_ms ?? 'n/a'}ms`;
        } catch (err) {
          document.getElementById('status').textContent = `Browser GPS post failed: ${err}`;
          clientLog('error', 'browser-gps', 'Browser GPS POST failed', String(err));
        }
      }, error => {
        const causes = {
          1: 'Permission denied. Allow location for this browser/page in the permission prompt and macOS Location Services.',
          2: 'Position unavailable. The OS/browser could not determine location from GPS/Wi-Fi/cell sources.',
          3: 'Timeout. No location fix arrived before the timeout.'
        };
        const msg = `Browser GPS failed: ${error.message}. Likely cause: ${causes[error.code] || 'unknown'}`;
        document.getElementById('status').textContent = msg;
        clientLog('error', 'browser-gps', msg, { code: error.code, message: error.message, protocol: location.protocol, host: location.host });
      }, { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 });
      button.textContent = 'Stop browser GPS';
    });
    document.getElementById('startMotion').addEventListener('click', async () => {
      const button = document.getElementById('startMotion');
      if (motionHandler) {
        window.removeEventListener('devicemotion', motionHandler);
        motionHandler = null;
        button.textContent = 'Start accelerometer';
        document.getElementById('status').textContent = 'Accelerometer listener stopped.';
        clientLog('info', 'accelerometer', 'Accelerometer listener stopped', {});
        return;
      }
      if (typeof DeviceMotionEvent === 'undefined') {
        const msg = 'Accelerometer is not available in this browser.';
        document.getElementById('status').textContent = msg;
        clientLog('warning', 'accelerometer', msg, {});
        return;
      }
      try {
        if (typeof DeviceMotionEvent.requestPermission === 'function') {
          const perm = await DeviceMotionEvent.requestPermission();
          if (perm !== 'granted') throw new Error(`permission ${perm}`);
        }
        motionHandler = async ev => {
          const payload = {
            acceleration: ev.acceleration,
            accelerationIncludingGravity: ev.accelerationIncludingGravity,
            rotationRate: ev.rotationRate,
            interval: ev.interval
          };
          try {
            await fetch('/api/motion-sensor', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });
            document.getElementById('status').textContent = 'Accelerometer active; values are recorded. GPS correction uses the selected track only when that formula is enabled.';
          } catch (err) {
            clientLog('error', 'accelerometer', 'Motion sensor POST failed', String(err));
          }
        };
        window.addEventListener('devicemotion', motionHandler, { passive: true });
        button.textContent = 'Stop accelerometer';
        clientLog('info', 'accelerometer', 'Accelerometer listener started', {});
      } catch (err) {
        motionHandler = null;
        button.textContent = 'Start accelerometer';
        const msg = `Accelerometer failed: ${err.message}`;
        document.getElementById('status').textContent = msg;
        clientLog('error', 'accelerometer', msg, {});
      }
    });
    document.getElementById('exportKml').addEventListener('click', () => {
      window.location.href = '/export.kml';
    });
    document.getElementById('runRedisQuery').addEventListener('click', runRedisQuery);
    document.getElementById('fullscreenChart').addEventListener('click', () => {
      const panel = document.getElementById('chartPanel');
      panel.classList.toggle('fullscreen');
      document.getElementById('fullscreenChart').textContent = panel.classList.contains('fullscreen') ? 'Exit fullscreen' : 'Fullscreen';
      setTimeout(drawTimeChart, 80);
    });
    window.addEventListener('resize', drawTimeChart);
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def send_bytes(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(page_html().encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/samples":
            with STATE_LOCK:
                status = dict(STATE["status"])
                status["redis"] = STATE["redis"]
                status["collection_enabled"] = STATE.get("collection_enabled", True)
                payload = {
                    "samples": STATE["samples"],
                    "status": status,
                    "started_utc": STATE["started_utc"],
                    "settings": STATE["settings"],
                    "route_tracks": public_track_catalog(),
                }
            self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/api/settings":
            with STATE_LOCK:
                settings = dict(STATE["settings"])
                config = dict(STATE.get("config") or {})
                if config.get("redis_url"):
                    settings["redis_url"] = config.get("redis_url")
                settings["redis_prefix"] = config.get("redis_prefix") or "moving_client_data"
                payload = {
                    "ok": True,
                    "settings": settings,
                    "route_tracks": public_track_catalog(),
                    "gcp_redis_plan": STATE.get("gcp_redis_plan"),
                }
            self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/api/adb-devices":
            with STATE_LOCK:
                config = dict(STATE.get("config") or {})
                selected = STATE.get("adb_serial_override") or config.get("serial")
            adb = config.get("adb") or shutil.which("adb") or "/Users/username/Library/Android/sdk/platform-tools/adb"
            connect_result = None
            out, devices = adb_devices(adb)
            if not any(d.get("serial") == DEFAULT_WIRELESS_ADB_SERIAL and d.get("state") == "device" for d in devices):
                connect_result = adb_connect_wireless(adb)
                out, devices = adb_devices(adb)
            payload = {
                "ok": out["ok"],
                "adb": adb,
                "selected": selected,
                "devices": devices,
                "wireless_serial": DEFAULT_WIRELESS_ADB_SERIAL,
                "wireless_connect": connect_result,
                "stderr": out["stderr"],
            }
            self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/api/wifi-info":
            payload = {"ok": True, "wifi": local_wifi_info()}
            self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/api/logs":
            with STATE_LOCK:
                payload = {"logs": STATE["error_logs"][-300:]}
            self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/api/osm-status":
            self.send_bytes(json.dumps(osm_storage_status(), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/api/osm-consistency":
            with STATE_LOCK:
                config = dict(STATE.get("config") or {})
            self.send_bytes(json.dumps(osm_consistency_check(config), ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path == "/export.kml":
            with STATE_LOCK:
                samples = list(STATE["samples"])
            body = samples_to_kml(samples).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.google-earth.kml+xml; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="moving_client_data_samples.kml"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            last_sent = 0
            try:
                while True:
                    with STATE_LOCK:
                        samples = [s for s in STATE["samples"] if (s.get("sequence") or 0) > last_sent]
                    for sample in samples:
                        last_sent = sample.get("sequence") or last_sent
                        data = json.dumps(sample, ensure_ascii=False)
                        self.wfile.write(f"event: sample\ndata: {data}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                return
        self.send_bytes(b"not found", "text/plain; charset=utf-8", status=404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_bytes(b'{"ok":false,"error":"invalid json"}', "application/json; charset=utf-8", status=400)
            return

        if parsed.path == "/api/browser-location":
            try:
                lat = float(payload["lat"])
                lon = float(payload["lon"])
            except (KeyError, TypeError, ValueError):
                self.send_bytes(b'{"ok":false,"error":"lat/lon required"}', "application/json; charset=utf-8", status=400)
                return
            try:
                accuracy_m = float(payload.get("accuracy_m")) if payload.get("accuracy_m") is not None else None
            except (TypeError, ValueError):
                accuracy_m = None
            loc = {
                "lat": lat,
                "lon": lon,
                "accuracy_m": accuracy_m,
                "gps_accuracy_m": accuracy_m,
                "altitude_m": payload.get("altitude_m"),
                "altitude_accuracy_m": payload.get("altitude_accuracy_m"),
                "heading_deg": payload.get("heading_deg"),
                "speed_mps": payload.get("speed_mps"),
                "browser_timestamp_ms": payload.get("browser_timestamp_ms"),
                "received_utc": utc_now(),
            }
            redis_result = {"enabled": False}
            with STATE_LOCK:
                latest_sample = STATE["samples"][-1] if STATE["samples"] else None
                if latest_sample:
                    loc["latency"] = latest_sample.get("latency") or []
                    loc["latency_summary"] = latest_sample.get("latency_summary") or {}
                    loc["connection_errors"] = latest_sample.get("connection_errors") or {}
                    loc["latency_sample_sequence"] = latest_sample.get("sequence")
                    loc["latency_sample_timestamp_utc"] = latest_sample.get("timestamp_utc")
                STATE["browser_location"] = loc
                config = dict(STATE.get("config") or {})
            if config.get("redis_url"):
                try:
                    redis_client = RedisClient(config["redis_url"])
                    redis_prefix = config.get("redis_prefix") or "moving_client_data"
                    redis_client.store_browser_location(redis_prefix, loc)
                    redis_result = {"enabled": True, "ok": True, "message": "browser GPS stored", "prefix": redis_prefix}
                except Exception as exc:
                    redis_result = {"enabled": True, "ok": False, "message": str(exc)}
            self.send_bytes(json.dumps({"ok": True, "location": loc, "redis": redis_result}).encode("utf-8"), "application/json; charset=utf-8")
            return

        if parsed.path == "/api/wifi-label":
            label = str(payload.get("ssid", "")).strip()
            if not label:
                self.send_bytes(b'{"ok":false,"error":"ssid required"}', "application/json; charset=utf-8", status=400)
                return
            with STATE_LOCK:
                STATE["wifi_label"] = label[:80]
            self.send_bytes(json.dumps({"ok": True, "ssid": label[:80]}).encode("utf-8"), "application/json; charset=utf-8")
            return

        if parsed.path == "/api/assign-location":
            try:
                sequence = int(payload["sequence"])
                lat = float(payload["lat"])
                lon = float(payload["lon"])
            except (KeyError, TypeError, ValueError):
                self.send_bytes(b'{"ok":false,"error":"sequence, lat, lon required"}', "application/json; charset=utf-8", status=400)
                return
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                self.send_bytes(b'{"ok":false,"error":"lat/lon out of range"}', "application/json; charset=utf-8", status=400)
                return
            with STATE_LOCK:
                config = dict(STATE.get("config") or {})
                sample = next((s for s in STATE["samples"] if s.get("sequence") == sequence), None)
                if sample:
                    sample["location"] = {
                        "lat": lat,
                        "lon": lon,
                        "source": "manual_map_pin",
                        "coordinate_type": "manual",
                        "assigned_utc": utc_now(),
                    }
                    sample["manual_location_assignment"] = {
                        "assigned_utc": sample["location"]["assigned_utc"],
                        "method": "browser_map_click",
                    }
                    sample["position_error_km"] = None
                    sample["position_correction"] = {"method": "manual map pin", "applied": False}
            if not sample:
                self.send_bytes(b'{"ok":false,"error":"sample not found"}', "application/json; charset=utf-8", status=404)
                return
            redis_result = {"enabled": False}
            if config.get("redis_url"):
                try:
                    redis_client = RedisClient(config["redis_url"])
                    redis_prefix = config.get("redis_prefix") or "moving_client_data"
                    redis_client.store_sample(redis_prefix, sample)
                    redis_result = {"enabled": True, "ok": True, "message": "manual sample location stored", "prefix": redis_prefix}
                except Exception as exc:
                    redis_result = {"enabled": True, "ok": False, "message": str(exc)}
            add_log("info", "manual-pin", f"Assigned manual location to sample #{sequence}", {"lat": lat, "lon": lon, "redis": redis_result})
            self.send_bytes(json.dumps({"ok": True, "sample": sample, "redis": redis_result}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return

        if parsed.path == "/api/adb-select":
            serial = str(payload.get("serial", "")).strip()
            with STATE_LOCK:
                STATE["adb_serial_override"] = serial or None
                config = STATE.get("config") or {}
                config["serial"] = serial or None
                STATE["config"] = config
            add_log("info", "adb", f"Selected ADB device: {serial or 'auto'}")
            self.send_bytes(json.dumps({"ok": True, "serial": serial or None}).encode("utf-8"), "application/json; charset=utf-8")
            return

        if parsed.path == "/api/client-log":
            entry = add_log(
                str(payload.get("level", "info")),
                str(payload.get("source", "browser")),
                str(payload.get("message", "")),
                payload.get("detail"),
            )
            self.send_bytes(json.dumps({"ok": True, "log": entry}).encode("utf-8"), "application/json; charset=utf-8")
            return

        if parsed.path == "/api/collection-control":
            action = str(payload.get("action", "")).strip().lower()
            if action not in {"start", "stop"}:
                self.send_bytes(b'{"ok":false,"error":"action must be start or stop"}', "application/json; charset=utf-8", status=400)
                return
            enabled = action == "start"
            with STATE_LOCK:
                STATE["collection_enabled"] = enabled
                status = dict(STATE.get("status") or {})
                status["collection_enabled"] = enabled
                STATE["status"] = status
            add_log("info", "collection", f"Collection {'started' if enabled else 'stopped'} from dashboard")
            self.send_bytes(json.dumps({"ok": True, "collection_enabled": enabled}).encode("utf-8"), "application/json; charset=utf-8")
            return

        if parsed.path == "/api/redis-log-query":
            with STATE_LOCK:
                config = dict(STATE.get("config") or {})
            result = execute_redis_log_query(config, str(payload.get("query", "")))
            status = 200 if result.get("ok") else 400
            self.send_bytes(json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status=status)
            return

        if parsed.path == "/api/osm-download":
            result = start_osm_country_download(str(payload.get("country", "")))
            status = 200 if result.get("ok") else 400
            self.send_bytes(json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status=status)
            return

        if parsed.path == "/api/gcp-redis-plan":
            result = build_gcp_redis_plan(payload)
            if result.get("ok"):
                with STATE_LOCK:
                    STATE["gcp_redis_plan"] = result
            status = 200 if result.get("ok") else 400
            self.send_bytes(json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status=status)
            return

        if parsed.path == "/api/settings":
            allowed = {
                "gps_formula",
                "use_adb_gps",
                "use_browser_gps",
                "use_accelerometer",
                "allow_interpolated_fallback",
                "selected_track_id",
                "provider",
                "modal_bg",
                "panel_opacity",
                "modal_opacity",
                "summary_dock",
                "details_dock",
                "chart_dock",
                "redis_url",
                "redis_prefix",
            }
            docks = {"top-left", "top-right", "left", "right", "bottom"}
            formulas = {"raw", "constant_speed_track", "constant_speed_to_karlsruhe"}
            track_ids = {track["id"] for track in TRACK_CATALOG}
            with STATE_LOCK:
                settings = dict(STATE.get("settings") or {})
                for key, value in payload.items():
                    if key not in allowed:
                        continue
                    if key == "gps_formula":
                        if value == "constant_speed_to_karlsruhe":
                            value = "constant_speed_track"
                        settings[key] = value if value in formulas else "raw"
                    elif key == "selected_track_id":
                        settings[key] = value if value in track_ids else settings.get(key, "berlin_karlsruhe")
                    elif key in {"summary_dock", "details_dock", "chart_dock"}:
                        settings[key] = value if value in docks else settings.get(key, "top-left")
                    elif key in {"use_adb_gps", "use_browser_gps", "use_accelerometer", "allow_interpolated_fallback"}:
                        settings[key] = bool(value)
                    elif key in {"panel_opacity", "modal_opacity"}:
                        try:
                            settings[key] = max(0.1, min(1.0, float(value)))
                        except (TypeError, ValueError):
                            pass
                    elif key == "modal_bg":
                        text = str(value)
                        settings[key] = text if re.match(r"^#[0-9a-fA-F]{6}$", text) else settings.get(key, "#ffffff")
                    elif key == "provider":
                        settings[key] = str(value).strip()[:120]
                    elif key == "redis_url":
                        redis_url = str(value).strip()
                        config = STATE.get("config") or {}
                        config["redis_url"] = redis_url or None
                        STATE["config"] = config
                    elif key == "redis_prefix":
                        redis_prefix = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value).strip())[:80] or "moving_client_data"
                        config = STATE.get("config") or {}
                        config["redis_prefix"] = redis_prefix
                        STATE["config"] = config
                STATE["settings"] = settings
                config = dict(STATE.get("config") or {})
                response_settings = dict(settings)
                if config.get("redis_url"):
                    response_settings["redis_url"] = config.get("redis_url")
                response_settings["redis_prefix"] = config.get("redis_prefix") or "moving_client_data"
            add_log("info", "settings", "Updated collection/display settings", response_settings)
            self.send_bytes(json.dumps({"ok": True, "settings": response_settings, "route_tracks": public_track_catalog()}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return

        if parsed.path == "/api/motion-sensor":
            motion = {
                "received_utc": utc_now(),
                "acceleration": payload.get("acceleration"),
                "acceleration_including_gravity": payload.get("accelerationIncludingGravity"),
                "rotation_rate": payload.get("rotationRate"),
                "interval": payload.get("interval"),
            }
            with STATE_LOCK:
                STATE["motion_sensor"] = motion
            self.send_bytes(json.dumps({"ok": True, "motion": motion}).encode("utf-8"), "application/json; charset=utf-8")
            return

        self.send_bytes(b'{"ok":false,"error":"not found"}', "application/json; charset=utf-8", status=404)


def main():
    parser = argparse.ArgumentParser(description="Realtime moving target latency map using OpenStreetMap tiles.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--per-target-pause", type=float, default=0.5)
    parser.add_argument("--target", action="append", default=None)
    parser.add_argument("--adb", default=None)
    parser.add_argument("--serial", default=None)
    parser.add_argument("--redis-url", default=None, help="Optional Redis URL, for example redis://127.0.0.1:6379/0")
    parser.add_argument("--redis-prefix", default="moving_client_data", help="Redis key prefix for samples, streams, indexes, and geo data.")
    parser.add_argument("--trace-interval", type=float, default=60.0, help="Seconds between short traceroute samples for router GeoIP. Use 0 to disable.")
    parser.add_argument("--trace-target", default=TRACE_TARGET, help="Traceroute target for router GeoIP sampling.")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parser.add_argument("--log", default=f"moving_target_osm_samples_{stamp}.jsonl")
    args = parser.parse_args()

    config = {
        "host": args.host,
        "port": args.port,
        "interval": args.interval,
        "timeout": args.timeout,
        "per_target_pause": args.per_target_pause,
        "targets": args.target or DEFAULT_TARGETS,
        "adb": args.adb,
        "serial": args.serial,
        "redis_url": args.redis_url,
        "redis_prefix": args.redis_prefix,
        "trace_interval": args.trace_interval,
        "trace_target": args.trace_target,
        "log": args.log,
    }

    thread = threading.Thread(target=collector_loop, args=(config,), daemon=True)
    thread.start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Moving Target OSM Dashboard running at {url}")
    print(f"Sample log: {args.log}")
    if args.redis_url:
        print(f"Redis storage: {args.redis_url} prefix={args.redis_prefix}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
