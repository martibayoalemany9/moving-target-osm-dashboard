# Realtime OpenStreetMap ICE 5G Latency Dashboard Implementation and Operational Log

Date: 2026-06-25  
Timezone during execution: Europe/Berlin  
Workspace: `/Users/username/curriculum_2026/69de423717afa15e22861207`

## Objective

Build and run a realtime map in the Codex environment for measuring Internet latency while traveling on an ICE train, correlated with:

- OpenStreetMap map display.
- Timestamped latency measurements.
- GPS coordinates from a Samsung phone over ADB.
- 5G NR / LTE radio properties from the Samsung phone over ADB.
- Serving cell identifiers such as PCI, ARFCN/EARFCN, TAC, RSRP, RSRQ, and SINR where Android exposes them.

The dashboard was designed to avoid aggressive network fingerprinting. It uses normal HTTPS timing probes instead of port scanning, repeated traceroute, or high-rate ICMP.

## Files Created or Modified

### `realtime_ice_5g_osm_map_app.py`

Self-contained realtime web app and sampler.

Responsibilities:

- Runs a local HTTP server.
- Serves a realtime Leaflet map using OpenStreetMap tiles.
- Collects latency samples every configured interval.
- Attempts to read Samsung GPS and 5G/LTE properties via ADB.
- Writes every sample to JSONL for later analysis.
- Exposes JSON and Server-Sent Events endpoints for live updates.

### `ice_5g_latency_mapper.py`

Earlier offline collector and renderer.

Responsibilities:

- `collect`: writes samples to JSONL.
- `render`: turns JSONL into an HTML map.

### `ice_train_5g_latency_gps_cell_tower_mapping_and_connection_optimization_notes_2026_06_25.md`

Setup and interpretation guide for ADB, Samsung developer settings, radio fields, and optimization.

### `realtime_openstreetmap_ice_5g_latency_dashboard_implementation_and_operational_log_2026_06_25.md`

This document.

## Runtime Status

The realtime dashboard is running locally at:

```text
http://127.0.0.1:8766/
```

The active sample log is:

```text
realtime_ice_5g_osm_samples_20260625T193110Z.jsonl
```

Port `8765` was attempted first but was already occupied:

```text
OSError: [Errno 48] Address already in use
```

The server was then started successfully on port `8766`:

```text
Realtime ICE 5G OSM map running at http://127.0.0.1:8766/
Sample log: realtime_ice_5g_osm_samples_20260625T193110Z.jsonl
Press Ctrl-C to stop.
```

## Codex App Browser Status

I attempted to attach the Codex in-app browser to display the local realtime map. The browser surface reported:

```text
Browser is not available: iab
```

Result:

- The local realtime app is running and verified.
- I could not programmatically show the page inside the Codex in-app browser because that browser surface is unavailable in this session.
- The URL can still be opened manually or from the system browser:

```text
http://127.0.0.1:8766/
```

## OpenStreetMap Implementation

The frontend uses Leaflet and OpenStreetMap tiles:

```html
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
```

Tile source:

```javascript
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors'
})
```

Important operational note:

- The realtime dashboard itself is local.
- The map background requires access to Leaflet CDN and OpenStreetMap tile servers.
- If the train connection blocks those resources, the app still collects samples, but the visual map background may not load.

## Backend Endpoints

### HTML dashboard

```text
GET /
```

Returns the realtime map UI.

Verified with:

```bash
curl -fsS http://127.0.0.1:8766/ | head -n 20
```

Result included:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Realtime ICE 5G Latency Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
```

### JSON API

```text
GET /api/samples
```

Returns:

- Recent samples.
- Runtime status.
- ADB status.
- Current log path.
- Server start time.

Verified with:

```bash
curl -fsS http://127.0.0.1:8766/api/samples
```

### Realtime event stream

```text
GET /events
```

Uses Server-Sent Events. Each new sample is sent as:

```text
event: sample
data: {...sample JSON...}
```

The browser page listens with:

```javascript
const events = new EventSource('/events');
events.addEventListener('sample', ev => addSample(JSON.parse(ev.data)));
```

## Current ADB Status

ADB is not installed on this Mac right now:

```text
zsh: command not found: adb
```

The live API therefore reports:

```json
{
  "available": false,
  "path": null,
  "selected_serial": null,
  "message": "adb not found; GPS and radio fields unavailable."
}
```

Impact:

- Latency measurements are live.
- GPS coordinates are not available yet.
- 5G NR / LTE serving-cell information is not available yet.
- The OpenStreetMap page will show status and metrics, but it cannot plot route points until location data appears.

## Samsung Developer Mode Requirements

On the Samsung:

1. Open `Settings > About phone > Software information`.
2. Tap `Build number` seven times.
3. Open `Settings > Developer options`.
4. Enable `USB debugging`.
5. Connect the phone by USB.
6. Unlock the phone.
7. Accept the USB debugging authorization prompt.
8. Enable Location.
9. Keep a GPS-using app open if Android does not keep a fresh GPS fix by itself.

On the Mac, install ADB:

```bash
brew install android-platform-tools
```

Then verify:

```bash
adb devices -l
```

Expected authorized output:

```text
List of devices attached
R5XXXXXXXXX device product:... model:... device:... transport_id:...
```

If output says `unauthorized`, approve the prompt on the Samsung.

## What the App Samples

Each JSONL sample has this structure:

```json
{
  "timestamp_utc": "2026-06-25T19:31:10+00:00",
  "sequence": 1,
  "location": null,
  "radio": {},
  "adb": {
    "available": false,
    "path": null,
    "selected_serial": null,
    "message": "adb not found; GPS and radio fields unavailable."
  },
  "latency": [
    {
      "url": "https://www.apple.com/library/test/success.html",
      "ok": true,
      "http_code": "200",
      "remote_ip": "23.58.192.249",
      "time_namelookup": 0.010731,
      "time_connect": 6.195278,
      "time_appconnect": 6.371437,
      "time_starttransfer": 6.515228,
      "time_total": 6.515679,
      "latency_ms": 6515.7
    }
  ],
  "latency_summary": {
    "min_ms": 302.0,
    "avg_ms": 2729.1,
    "max_ms": 6515.7
  }
}
```

When ADB becomes available, `location` should become:

```json
{
  "lat": 52.5201,
  "lon": 13.4051,
  "accuracy_m": 12.0
}
```

And `radio` may include:

```json
{
  "operator_alpha": "Telekom.de",
  "operator_numeric": "26201",
  "network_type": "NR_SA or LTE/NR_NSA depending on device output",
  "nr_pci": "123",
  "nr_arfcn": "634080",
  "nr_bands": "[78]",
  "nr_ss_rsrp": "-91",
  "nr_ss_rsrq": "-11",
  "nr_ss_sinr": "18",
  "lte_pci": "321",
  "lte_earfcn": "1300",
  "lte_rsrp": "-99",
  "lte_rsrq": "-13"
}
```

Exact field availability depends on Samsung firmware, Android version, carrier policy, SIM state, and permissions.

## Current Live Latency Observations

The app is collecting live latency data. Recent samples show highly variable timings.

Examples from `GET /api/samples`:

```text
Sample 1:
Apple success URL: 6515.7 ms
Cloudflare trace: 1369.5 ms
Google generate_204: 302.0 ms
Average successful latency: 2729.1 ms

Sample 5:
Apple success URL: DNS resolution timed out after 8005 ms
Cloudflare trace: 7974.7 ms
Google generate_204: connection timed out after 8004 ms
Average successful latency: 7974.7 ms

Sample 8:
Apple success URL: 942.1 ms
Cloudflare trace: 430.3 ms
Google generate_204: 286.6 ms
Average successful latency: 553.0 ms
```

Interpretation:

- The connection is working intermittently.
- There are major spikes in DNS, TCP connect, TLS handshake, and server response time.
- This pattern is consistent with train-speed cellular handovers, weak radio sections, congested cells, DNS instability, tunnel/VPN disruption, or carrier/core-network path changes.
- Without ADB GPS and radio fields, the current data cannot yet prove which cell/antenna caused each spike.

## Latency Color Scheme

The map uses:

```text
<80 ms      green
80-150 ms   light green
150-250 ms  yellow
250-400 ms  orange
>=400 ms    red
unknown     gray
```

The point popup includes:

- Timestamp.
- Average latency.
- 5G NR PCI / ARFCN.
- LTE PCI / EARFCN.

The top panel shows:

- Number of samples.
- Average latency for latest sample.
- GPS status.
- Serving cell status.

## Antenna / Cell Interpretation

The app does not claim exact tower coordinates from Android alone.

It can identify serving-cell properties:

- `nr_pci`: 5G NR physical cell ID.
- `nr_arfcn`: 5G NR frequency channel.
- `nr_bands`: 5G band.
- `nr_nci`: 5G cell identity when exposed.
- `nr_tac`: tracking area code.
- `nr_ss_rsrp`: 5G signal power.
- `nr_ss_rsrq`: 5G signal quality.
- `nr_ss_sinr`: 5G signal-to-interference-plus-noise ratio.
- `lte_pci`, `lte_earfcn`, `lte_tac`, `lte_rsrp`, `lte_rsrq`, `lte_rssnr`: LTE equivalents.

To turn these into physical antenna locations, the data must be joined with an external cell database such as CellMapper or an operator-provided radio-planning dataset. The current implementation logs the identifiers needed for that later join but does not query third-party cell databases.

## Why HTTPS Timing Instead of Ping/Traceroute

The previous in-flight router appeared to suppress ICMP/traceroute during one probe. For this ICE setup, the dashboard avoids aggressive fingerprinting:

- No port scanning.
- No subnet scanning.
- No repeated traceroute.
- No high-rate ICMP.
- No gateway probing.

Instead it measures ordinary HTTPS transactions to widely used endpoints.

Default targets:

```text
https://www.apple.com/library/test/success.html
https://cloudflare.com/cdn-cgi/trace
https://www.google.com/generate_204
```

You can make it even lighter by using one target:

```bash
./realtime_ice_5g_osm_map_app.py --port 8766 --interval 15 --target https://cloudflare.com/cdn-cgi/trace
```

## Running the Dashboard

Current command:

```bash
./realtime_ice_5g_osm_map_app.py --port 8766 --interval 10
```

Recommended once ADB is installed:

```bash
./realtime_ice_5g_osm_map_app.py --port 8766 --interval 15
```

If more than one Android device is attached:

```bash
adb devices -l
./realtime_ice_5g_osm_map_app.py --port 8766 --interval 15 --serial YOUR_DEVICE_SERIAL
```

If port `8766` is occupied:

```bash
./realtime_ice_5g_osm_map_app.py --port 8767 --interval 15
```

## How to Optimize the 5G Connection at ICE Speeds

At around 200 km/h, the device is constantly crossing cell boundaries. The main optimization goal is stable handover, not maximum theoretical throughput.

Recommended:

- Put the Samsung or router near a window.
- Keep it stationary; do not move it around during measurement.
- Avoid placing it under a laptop, metal table, power bank, or bag.
- Use USB tethering if the laptop relies on the phone; it avoids extra Wi-Fi tethering noise.
- Prefer `5G Auto` instead of forcing `NR only`.
- Avoid manual band locking while moving unless doing controlled tests.
- Disable VPN/private relay during radio diagnosis if you want to isolate the mobile network.
- Keep one stable latency target for clean comparisons.
- Sample every 10-20 seconds, not multiple times per second.

Not recommended:

- Aggressive traceroute loops.
- Port scanning gateways.
- Locking to one cell while moving fast.
- Forcing a single 5G band without route-specific deployment knowledge.

Likely causes of ICE latency spikes:

- 5G/LTE handover between trackside macro cells.
- NSA 5G anchor changes between LTE cells.
- Temporary fallback from 5G to LTE.
- Train window attenuation.
- Tunnels, cuttings, stations, bridges, and rural gaps.
- Carrier cell congestion when many passengers attach at once.
- DNS instability or tunnel/VPN reconnects after radio changes.

## Verification Performed

### Python syntax

Command:

```bash
python3 -m py_compile realtime_ice_5g_osm_map_app.py ice_5g_latency_mapper.py
```

Result:

```text
passed with no output
```

### Local HTML endpoint

Command:

```bash
curl -fsS http://127.0.0.1:8766/ | head -n 20
```

Result:

```text
HTML returned successfully and includes the Leaflet/OpenStreetMap page.
```

### Local API endpoint

Command:

```bash
curl -fsS http://127.0.0.1:8766/api/samples
```

Result:

```text
JSON returned successfully with live samples and ADB status.
```

### Live sample log

Command:

```bash
ls -lt realtime_ice_5g_osm_samples_*.jsonl | head -n 5
```

Result:

```text
realtime_ice_5g_osm_samples_20260625T193110Z.jsonl
```

## Current Gaps

1. ADB is not installed, so GPS and 5G cell fields are not yet available.
2. The Codex in-app browser was unavailable, so I could not display the dashboard inside the Codex browser surface.
3. OpenStreetMap tiles require external Internet access from the viewing browser.
4. Exact antenna coordinates require a cell database join; Android radio state alone gives serving cell identifiers, not tower latitude/longitude.

## Prototype Update: Plot, Wi-Fi, Redis, and Browser Result

The prototype was completed and opened in the system browser at:

```text
http://127.0.0.1:8766/
```

Current implemented UI:

- OpenStreetMap / Leaflet realtime map.
- Latency-colored train route segments. Green means lower latency; red means high latency.
- `Zoom latest` button to center the map on the newest GPS sample.
- `5G parameters` popup explaining ARFCN, RSRP, RSRQ, SINR, PCI, NCI/CI, TAC, NSA/SA, and related fields.
- Bottom time plot with two lines:
  - Red line: latency in milliseconds, left Y axis.
  - Blue line: speed in km/h, right Y axis.
- Wi-Fi display showing SSID, MAC address, and IP when macOS exposes them.
- Redis status in the latest-sample details panel.

Latest observed Wi-Fi data from the live API:

```json
{
  "interface": "en0",
  "mac_address": "26:89:b9:89:19:75",
  "ip_address": "192.168.76.127",
  "status": "active",
  "ssid": "<redacted>"
}
```

Latest observed latency summary:

```json
{
  "min_ms": 139.8,
  "avg_ms": 152.7,
  "max_ms": 168.5
}
```

Redis was installed with Homebrew and started locally on:

```text
redis://127.0.0.1:6379/0
```

Redis is running with append-only persistence under:

```text
redis-data/appendonlydir/
```

Telemetry keys use prefix:

```text
ice5g
```

Redis stores:

```text
ice5g:latest       latest full JSON sample
ice5g:samples      list of full JSON samples
ice5g:stream       Redis stream of samples
ice5g:sample:N     full JSON for sequence N
ice5g:index:N      hash index for sequence N
ice5g:geo          geospatial index when GPS exists
```

Verification:

```text
Redis PING: PONG
Redis stream length observed: 83
Dashboard Redis status: stored latest sample
```

Current Samsung/ADB state:

```text
ADB binary: /Users/username/Library/Android/sdk/platform-tools/adb
adb devices -l: no devices attached
```

Because ADB currently sees no Samsung device, GPS coordinates, serving-cell fields, and calculated speed are not yet available. The speed plot is implemented with a separate right-side Y axis, but it will only draw a blue line after GPS samples arrive. To fix that, unlock the Samsung, enable/confirm USB debugging, and make sure the USB connection is data-capable rather than charge-only.

## Next Step

Install Android platform-tools, authorize the Samsung over USB, and refresh `http://127.0.0.1:8766/`. The existing running app will need to be restarted after `adb` is installed so it can detect the newly available device.
