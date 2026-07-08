# Android Auto Companion

Native Android Auto companion app for the Moving Target OSM Dashboard. It uses
Android for Cars App Library templates to show a compact, driver-safe view of
the latest Python dashboard sample.

## Build

```bash
ANDROID_HOME=/Users/username/Library/Android/sdk ./gradlew assembleDebug
```

The debug APK is written to:

```text
app/build/outputs/apk/debug/app-debug.apk
```

## Desktop Head Unit

Run the Python dashboard first:

```bash
cd ../python-dashboard
./moving_target_osm_dashboard.py
```

Then install and expose local ports to the phone:

```bash
adb install ../android-auto/app/build/outputs/apk/debug/app-debug.apk
adb reverse tcp:8765 tcp:8765
adb forward tcp:5277 tcp:5277
/Users/username/Library/Android/sdk/extras/google/auto/desktop-head-unit
```

The app default endpoint is `http://127.0.0.1:8765/api/samples`, which works
with `adb reverse`.
