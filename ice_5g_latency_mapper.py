#!/usr/bin/env python3
import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_TARGETS = [
    "https://www.apple.com/library/test/success.html",
    "https://cloudflare.com/cdn-cgi/trace",
    "https://www.google.com/generate_204",
]


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
        return {
            "ok": False,
            "code": 124,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": f"timeout after {timeout}s",
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
    res = run(
        [curl, "-fsS", "--max-time", str(timeout), "-o", os.devnull, "-w", fmt, url],
        timeout=timeout + 2,
    )
    fields = {"url": url, "ok": res["ok"], "stderr": res["stderr"]}
    for line in res["stdout"].splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key.startswith("time_"):
                try:
                    fields[key] = float(value)
                except ValueError:
                    fields[key] = value
            else:
                fields[key] = value
    if "time_total" in fields:
        fields["latency_ms"] = round(fields["time_total"] * 1000, 1)
    return fields


def parse_location(dumpsys_location):
    # Common Android forms:
    # Location[fused 52.520000,13.405000 hAcc=...]
    # last location=Location[gps 52.520000,13.405000 ...]
    matches = re.findall(
        r"Location\[[^\]\n]*?\s(-?\d+\.\d+),\s*(-?\d+\.\d+)(?:[^\]\n]*?hAcc=([0-9.]+))?",
        dumpsys_location,
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

    # NR / 5G cell identity and signal fields seen in dumpsys telephony.registry.
    patterns = {
        "operator_alpha": r"\[gsm\.operator\.alpha\]: \[(.*?)\]",
        "operator_numeric": r"\[gsm\.operator\.numeric\]: \[(.*?)\]",
        "network_type": r"\[gsm\.network\.type\]: \[(.*?)\]",
        "nr_state": r"mNrState=(\w+)",
        "data_network_type": r"mDataNetworkType=(\w+|[0-9]+)",
        "voice_network_type": r"mVoiceNetworkType=(\w+|[0-9]+)",
        "nr_pci": r"CellIdentityNr:.*?mPci\s*=\s*([0-9]+)",
        "nr_tac": r"CellIdentityNr:.*?mTac\s*=\s*([0-9]+)",
        "nr_arfcn": r"CellIdentityNr:.*?mNrArfcn\s*=\s*([0-9]+)",
        "nr_bands": r"CellIdentityNr:.*?mBands\s*=\s*(\[[^\]]*\])",
        "nr_nci": r"CellIdentityNr:.*?mNci\s*=\s*([0-9]+)",
        "nr_mcc": r"CellIdentityNr:.*?mMcc(?:Str)?\s*=\s*([0-9]+)",
        "nr_mnc": r"CellIdentityNr:.*?mMnc(?:Str)?\s*=\s*([0-9]+)",
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
        if value is not None and value != "":
            radio[key] = value

    # Keep tiny redacted snippets for debugging parser changes without storing full dumpsys output.
    snippets = []
    for needle in ["CellIdentityNr", "CellSignalStrengthNr", "CellIdentityLte", "CellSignalStrengthLte"]:
        idx = text.find(needle)
        if idx >= 0:
            snippets.append(text[idx : idx + 500].replace("\n", " "))
    if snippets:
        radio["parser_snippets"] = snippets[:4]
    return radio


def collect(args):
    adb = args.adb or shutil.which("adb")
    targets = args.target or DEFAULT_TARGETS
    out_path = Path(args.out or f"ice_5g_latency_samples_{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.jsonl")

    adb_status = {"available": bool(adb), "path": adb}
    serial = args.serial
    if adb:
        dev_out, devices = adb_devices(adb)
        adb_status["devices"] = devices
        if not serial and devices:
            ready = [d for d in devices if d["state"] == "device"]
            if ready:
                serial = ready[0]["serial"]
        adb_status["selected_serial"] = serial
        if not serial:
            print("No authorized ADB device found. Latency will be logged without GPS/radio data.", file=sys.stderr)
            if devices:
                print(json.dumps(devices, indent=2), file=sys.stderr)
            elif dev_out["stderr"]:
                print(dev_out["stderr"], file=sys.stderr)
    else:
        print("adb not found. Install Android platform-tools, then rerun for GPS/radio data.", file=sys.stderr)

    print(f"Writing samples to {out_path}")
    print("Stop with Ctrl-C.")
    count = 0
    try:
        while args.samples == 0 or count < args.samples:
            started = time.time()
            sample = {
                "timestamp_utc": utc_now(),
                "sequence": count + 1,
                "adb": adb_status,
                "latency": [],
            }

            if adb and serial:
                loc_out = adb_shell(adb, serial, "dumpsys location", timeout=8)
                sample["location"] = parse_location(loc_out["stdout"])
                sample["location_status"] = {"ok": loc_out["ok"], "stderr": loc_out["stderr"][-300:]}

                props_cmd = "getprop | grep -E 'gsm\\.|ril\\.|ro\\.product|ro\\.build.version.release'"
                props_out = adb_shell(adb, serial, props_cmd, timeout=5)
                reg_out = adb_shell(adb, serial, "dumpsys telephony.registry", timeout=8)
                tel_out = adb_shell(adb, serial, "dumpsys telephony", timeout=10)
                sample["radio"] = parse_radio(reg_out["stdout"], tel_out["stdout"], props_out["stdout"])
                sample["radio_status"] = {
                    "telephony_registry_ok": reg_out["ok"],
                    "telephony_ok": tel_out["ok"],
                    "props_ok": props_out["ok"],
                    "stderr": " | ".join(x for x in [reg_out["stderr"], tel_out["stderr"], props_out["stderr"]] if x)[-500:],
                }

            for url in targets:
                sample["latency"].append(curl_timing(url, args.timeout))
                time.sleep(args.per_target_pause)

            totals = [x.get("latency_ms") for x in sample["latency"] if x.get("ok") and isinstance(x.get("latency_ms"), (int, float))]
            if totals:
                sample["latency_summary"] = {
                    "min_ms": min(totals),
                    "avg_ms": round(sum(totals) / len(totals), 1),
                    "max_ms": max(totals),
                }

            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")

            loc = sample.get("location") or {}
            radio = sample.get("radio") or {}
            summary = sample.get("latency_summary") or {}
            print(
                f"{sample['timestamp_utc']} seq={sample['sequence']} "
                f"lat={loc.get('lat')} lon={loc.get('lon')} "
                f"avg_ms={summary.get('avg_ms')} "
                f"nr_pci={radio.get('nr_pci')} nr_arfcn={radio.get('nr_arfcn')} "
                f"lte_pci={radio.get('lte_pci')} lte_earfcn={radio.get('lte_earfcn')}"
            )

            count += 1
            elapsed = time.time() - started
            time.sleep(max(0, args.interval - elapsed))
    except KeyboardInterrupt:
        print("\nStopped.")


def latency_color(ms):
    if ms is None:
        return "#777777"
    if ms < 80:
        return "#1a9850"
    if ms < 150:
        return "#91cf60"
    if ms < 250:
        return "#fee08b"
    if ms < 400:
        return "#fc8d59"
    return "#d73027"


def render(args):
    in_path = Path(args.input)
    samples = []
    with in_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))

    points = []
    for sample in samples:
        loc = sample.get("location") or {}
        if "lat" not in loc or "lon" not in loc:
            continue
        summary = sample.get("latency_summary") or {}
        radio = sample.get("radio") or {}
        avg = summary.get("avg_ms")
        label_bits = [
            f"time: {sample.get('timestamp_utc')}",
            f"avg latency: {avg} ms",
            f"min/max: {summary.get('min_ms')} / {summary.get('max_ms')} ms",
            f"operator: {radio.get('operator_alpha', '')} {radio.get('operator_numeric', '')}",
            f"NR pci/arfcn/bands: {radio.get('nr_pci', '')} / {radio.get('nr_arfcn', '')} / {radio.get('nr_bands', '')}",
            f"NR RSRP/RSRQ/SINR: {radio.get('nr_ss_rsrp', '')} / {radio.get('nr_ss_rsrq', '')} / {radio.get('nr_ss_sinr', '')}",
            f"LTE pci/earfcn/bands: {radio.get('lte_pci', '')} / {radio.get('lte_earfcn', '')} / {radio.get('lte_bands', '')}",
            f"LTE RSRP/RSRQ/SINR: {radio.get('lte_rsrp', '')} / {radio.get('lte_rsrq', '')} / {radio.get('lte_rssnr', '')}",
        ]
        points.append(
            {
                "lat": loc["lat"],
                "lon": loc["lon"],
                "avg_ms": avg,
                "color": latency_color(avg),
                "popup": "<br>".join(html.escape(x) for x in label_bits),
            }
        )

    if points:
        center_lat = sum(p["lat"] for p in points) / len(points)
        center_lon = sum(p["lon"] for p in points) / len(points)
    else:
        center_lat, center_lon = 51.1657, 10.4515

    out_path = Path(args.output or in_path.with_suffix(".html"))
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ICE 5G Latency Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{ height: 100%; margin: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .panel {{
      position: absolute; z-index: 1000; top: 12px; left: 12px; max-width: 380px;
      background: rgba(255,255,255,.94); border: 1px solid #ccc; border-radius: 6px;
      padding: 10px 12px; box-shadow: 0 2px 8px rgba(0,0,0,.16); font-size: 13px;
    }}
    .legend span {{ display: inline-block; width: 11px; height: 11px; margin-right: 5px; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div class="panel">
    <strong>ICE 5G Latency Map</strong><br>
    Samples: {len(samples)} | Geolocated points: {len(points)}<br>
    Source: {html.escape(str(in_path))}
    <div class="legend">
      <div><span style="background:#1a9850"></span>&lt;80 ms</div>
      <div><span style="background:#91cf60"></span>80-150 ms</div>
      <div><span style="background:#fee08b"></span>150-250 ms</div>
      <div><span style="background:#fc8d59"></span>250-400 ms</div>
      <div><span style="background:#d73027"></span>&gt;=400 ms</div>
    </div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const points = {json.dumps(points, ensure_ascii=False)};
    const map = L.map('map').setView([{center_lat}, {center_lon}], points.length ? 10 : 6);
    L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }}).addTo(map);
    const latlngs = [];
    for (const p of points) {{
      const marker = L.circleMarker([p.lat, p.lon], {{
        radius: 7,
        color: '#222',
        weight: 1,
        fillColor: p.color,
        fillOpacity: 0.9
      }}).addTo(map);
      marker.bindPopup(p.popup);
      latlngs.push([p.lat, p.lon]);
    }}
    if (latlngs.length > 1) {{
      L.polyline(latlngs, {{color: '#2554a6', weight: 3, opacity: 0.65}}).addTo(map);
      map.fitBounds(latlngs, {{padding: [30, 30]}});
    }} else if (latlngs.length === 1) {{
      map.setView(latlngs[0], 13);
    }}
  </script>
</body>
</html>
"""
    out_path.write_text(html_doc, encoding="utf-8")
    print(f"Wrote {out_path} with {len(points)} geolocated points from {len(samples)} samples.")


def main():
    parser = argparse.ArgumentParser(description="Low-rate ICE 5G latency, GPS, and radio mapper.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="Collect low-rate samples to JSONL.")
    p_collect.add_argument("--adb", help="Path to adb. Defaults to adb on PATH.")
    p_collect.add_argument("--serial", help="ADB serial if multiple devices are attached.")
    p_collect.add_argument("--out", help="Output JSONL file.")
    p_collect.add_argument("--interval", type=float, default=15.0, help="Seconds between samples.")
    p_collect.add_argument("--samples", type=int, default=0, help="Number of samples; 0 means until Ctrl-C.")
    p_collect.add_argument("--timeout", type=float, default=8.0, help="Per-HTTP-probe timeout in seconds.")
    p_collect.add_argument("--per-target-pause", type=float, default=1.0, help="Pause between HTTP targets.")
    p_collect.add_argument("--target", action="append", help="HTTPS URL to time. Can be repeated.")
    p_collect.set_defaults(func=collect)

    p_render = sub.add_parser("render", help="Render a JSONL sample file to an HTML map.")
    p_render.add_argument("input", help="Input JSONL file from collect.")
    p_render.add_argument("--output", help="Output HTML path.")
    p_render.set_defaults(func=render)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
