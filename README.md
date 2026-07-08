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

The Python dashboard also tries Android wireless ADB at:

```text
100.77.37.113:5555
```

Use the dashboard `Connect 100.77.37.113` button to select that wireless device.
The phone must already have wireless debugging enabled and paired/trusted for
this Mac.

## Redis And GPS Notes

Redis is optional. Configure it from the Python dashboard Settings modal or at
startup:

```bash
./moving_target_osm_dashboard.py --redis-url redis://127.0.0.1:6379/0 --redis-prefix moving_client_data
```

The Settings modal includes a GCP Memorystore Redis planner. It generates the
`gcloud redis instances create ...` commands on the Python backend and stores
the plan in memory, but it does not execute `gcloud` or create billable GCP
resources.

Browser GPS records include `accuracy_m`, `gps_accuracy_m`, altitude/speed
fields when available, and the latest latency array plus latency summary. The
map auto-pans to each browser GPS fix while the browser GPS watch is active.

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

## Android Auto Companion

This repository includes a native Android Auto companion app in `android-auto/`.
It uses the Android for Cars App Library template surface to display a compact,
driver-safe view of the latest dashboard sample.

Build the debug APK:

```bash
cd android-auto
ANDROID_HOME=/Users/username/Library/Android/sdk ./gradlew assembleDebug
```

The APK is generated at:

```text
android-auto/app/build/outputs/apk/debug/app-debug.apk
```

For Desktop Head Unit testing with the Python dashboard running on the Mac:

```bash
./moving_target_osm_dashboard.py
adb install android-auto/app/build/outputs/apk/debug/app-debug.apk
adb reverse tcp:8765 tcp:8765
adb forward tcp:5277 tcp:5277
/Users/username/Library/Android/sdk/extras/google/auto/desktop-head-unit
```

The phone app stores the samples endpoint used by the car template. The default
is `http://127.0.0.1:8765/api/samples`, which works with `adb reverse`.

Useful Android telephony diagnostics:

```bash
adb devices
adb logcat -b radio
adb bugreport bugreport.zip
adb shell dumpsys telephony
adb shell dumpsys connectivity
```
