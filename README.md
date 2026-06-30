# Moving Target OSM Dashboard

Realtime and offline tooling for measuring mobile connectivity, GPS position,
radio metadata, Wi-Fi conditions, router egress, and HTTPS latency while moving.

## Realtime Dashboard

Run the local OpenStreetMap dashboard:

```bash
./moving_target_osm_dashboard.py
```

Then open:

```text
http://127.0.0.1:8765/
```

Useful options:

```bash
./moving_target_osm_dashboard.py --port 8766 --interval 10
./moving_target_osm_dashboard.py --adb /path/to/adb --serial DEVICE_SERIAL
./moving_target_osm_dashboard.py --redis-url redis://127.0.0.1:6379/0 --redis-prefix moving_client_data
```

## Offline Collector

Collect samples to JSONL:

```bash
./moving_client_data.py collect --interval 15
```

Render a static HTML map:

```bash
./moving_client_data.py render moving_client_data_samples_YYYYMMDDTHHMMSSZ.jsonl
```

## Included Tools

- `moving_target_osm_dashboard.py`: realtime dashboard and sampler.
- `moving_client_data.py`: offline collector and static HTML renderer.
- `satellite_probe.sh`: low-aggression gateway/backhaul diagnostic helper.
- `deploy_release_2220.sh`: timed local launch helper.
- `docs/`: implementation notes and operational history.

## Notes

ADB data requires an authorized Android device with USB debugging enabled.
Browser GPS and accelerometer data require browser/OS permission.
