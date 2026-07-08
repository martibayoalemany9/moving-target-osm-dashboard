# Moving Target OSM Dashboard

This repository contains three independently extensible apps for mobile
connectivity measurement and display.

## App Layout

- `apps/python-dashboard`: local Python dashboard, hardware sampler, Redis
  writer, ADB tooling, OpenStreetMap view, and offline collector.
- `apps/web-dashboard`: React/Next.js web dashboard for public/no-auth and
  Google-auth browser deployments.
- `apps/android-auto`: native Android Auto companion app using Android for Cars
  templates.

## Python Dashboard

```bash
cd apps/python-dashboard
./moving_target_osm_dashboard.py
```

Open `http://127.0.0.1:8765/`.

Useful options:

```bash
./moving_target_osm_dashboard.py --port 8766 --interval 10
./moving_target_osm_dashboard.py --adb /path/to/adb --serial DEVICE_SERIAL
./moving_target_osm_dashboard.py --redis-url redis://127.0.0.1:6379/0 --redis-prefix moving_client_data
```

The Python dashboard tries wireless ADB at `100.77.37.113:5555` and exposes
ADB diagnostics/actions in the top-left panel plus an ADB shell tab in the
bottom panel.

## Web Dashboard

```bash
cd apps/web-dashboard
npm install
npm run dev:public
npm run dev:auth
```

Public/no-auth runs on `http://127.0.0.1:3000`. Google-auth runs on
`http://127.0.0.1:3001` and requires real Google OAuth credentials.

## Android Auto

```bash
cd apps/android-auto
ANDROID_HOME=/Users/username/Library/Android/sdk ./gradlew assembleDebug
```

The debug APK is generated at
`apps/android-auto/app/build/outputs/apk/debug/app-debug.apk` from the repository
root. With the Python dashboard running, use `adb reverse tcp:8765 tcp:8765` so
the phone can read `http://127.0.0.1:8765/api/samples`.
