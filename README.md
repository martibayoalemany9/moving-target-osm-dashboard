# ICE 5G OSM Latency Dashboard

Realtime and offline tooling for measuring mobile connectivity, GPS position,
radio metadata, Wi-Fi conditions, router egress, and HTTPS latency while moving.

## Realtime Dashboard

Run the local OpenStreetMap dashboard:

```bash
./realtime_ice_5g_osm_map_app.py
```

Then open:

```text
http://127.0.0.1:8765/
```

Useful options:

```bash
./realtime_ice_5g_osm_map_app.py --port 8766 --interval 10
./realtime_ice_5g_osm_map_app.py --adb /path/to/adb --serial DEVICE_SERIAL
./realtime_ice_5g_osm_map_app.py --redis-url redis://127.0.0.1:6379/0 --redis-prefix ice5g
```

## Offline Collector

Collect samples to JSONL:

```bash
./ice_5g_latency_mapper.py collect --interval 15
```

Render a static HTML map:

```bash
./ice_5g_latency_mapper.py render ice_5g_latency_samples_YYYYMMDDTHHMMSSZ.jsonl
```

## Included Tools

- `realtime_ice_5g_osm_map_app.py`: realtime dashboard and sampler.
- `ice_5g_latency_mapper.py`: offline collector and static HTML renderer.
- `satellite_probe.sh`: low-aggression gateway/backhaul diagnostic helper.
- `deploy_release_2220.sh`: timed local launch helper.
- `docs/`: implementation notes and operational history.

## Notes

ADB data requires an authorized Android device with USB debugging enabled.
Browser GPS and accelerometer data require browser/OS permission.
