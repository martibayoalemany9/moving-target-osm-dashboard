# Python Dashboard

Local Python tooling for measuring mobile connectivity, GPS position, radio
metadata, Wi-Fi conditions, router egress, Redis persistence, and HTTPS latency
while moving.

## Realtime Dashboard

```bash
./moving_target_osm_dashboard.py
```

Open `http://127.0.0.1:8765/`.

Useful options:

```bash
./moving_target_osm_dashboard.py --port 8766 --interval 10
./moving_target_osm_dashboard.py --adb /path/to/adb --serial DEVICE_SERIAL
./moving_target_osm_dashboard.py --redis-url redis://127.0.0.1:6379/0 --redis-prefix moving_client_data
```

The dashboard tries Android wireless ADB at `100.77.37.113:5555`. Use the
`Connect 100.77.37.113` button to select that wireless device after wireless
debugging is enabled and paired/trusted for this Mac.

## Redis And GPS

Redis is optional. Configure it in the Settings modal or at startup with
`--redis-url`. The Settings modal includes a GCP Memorystore Redis planner that
generates `gcloud redis instances create ...` commands on the Python backend but
does not execute cloud operations.

Browser GPS records include accuracy, altitude, heading, speed, browser
timestamp, and the latest latency context. While browser GPS is active, the map
auto-scrolls to each GPS fix.

## ADB Tools

The top-left panel includes ADB buttons for:

- stay awake while plugged in
- Bluetooth HCI logging
- Bluetooth verbose logs
- Bluetooth disable/enable
- OEM unlock inspection
- activity, connectivity, Bluetooth, Wi-Fi, and network-device dumpsys output

The bottom panel includes an `ADB shell` tab for one-off `adb shell` commands
against the currently selected serial.

Useful terminal equivalents:

```bash
adb devices
adb logcat -b radio
adb bugreport bugreport.zip
adb shell dumpsys telephony
adb shell dumpsys connectivity
```

## Offline Collector

```bash
./moving_client_data.py collect --interval 15
./moving_client_data.py render moving_client_data_samples_YYYYMMDDTHHMMSSZ.jsonl
```

## Files

- `moving_target_osm_dashboard.py`: realtime dashboard and sampler.
- `moving_client_data.py`: offline collector and static HTML renderer.
- `satellite_probe.sh`: low-aggression gateway/backhaul diagnostic helper.
- `deploy_release_2220.sh`: timed local launch helper.
- `docs/`: implementation notes and operational history.
- `release-notes.md`: feature-oriented release notes for the Python app.
