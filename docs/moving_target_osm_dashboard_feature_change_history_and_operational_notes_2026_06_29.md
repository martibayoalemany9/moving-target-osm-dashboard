# Realtime Moving Target 5G OSM Latency Mapping Feature Change History And Operational Notes

Date: 2026-06-29

## Current Prototype

The local app is `moving_target_osm_dashboard.py`. It serves a browser dashboard with OpenStreetMap tiles, live latency samples, GPS positions, route coloring by latency, a dual-axis latency/speed plot, Redis-backed sample storage, and KML export.

Default route target for router path sampling is now `google.com`, so traceroute/provider detection follows the requested "traceroute to Google" behavior. The app stores `measurement_day` on every sample and includes it in Redis indexes and KML exports.

## Requested Feature Implementation Table

| ID | Requested feature | Implemented | Notes |
|---:|---|---:|---|
| 001 | Remove corrected route position as the default display/export behavior | X | Default GPS formula is `raw`; no correction is applied unless selected in settings. Older restored Redis samples may still contain historical corrected coordinates. |
| 002 | Add a settings dialog to select the GPS correction formula and correction track | X | Settings dialog supports `raw` and `constant_speed_track`, plus a scrollable 10-visible-entry track selector sorted by performed count. Initial repeated six-hour tracks are Berlin-Karlsruhe and Karlsruhe-Luxembourg. |
| 003 | Keep a list of GPS positions with sensitivity/accuracy | X | Samples store GPS coordinates plus `accuracy_m` when browser or Android provides it. Android coarse/fine is inferred through accuracy because browser JavaScript does not expose the OS permission granularity directly. |
| 004 | Explain HTML5 GPS behavior | X | Documented in this file and in the settings dialog. |
| 005 | Do not correct GPS by default | X | Default setting is `gps_formula: raw`. |
| 006 | Allow selecting which sensors are used for GPS positions | X | Settings dialog has toggles for ADB GPS, browser GPS, accelerometer recording, and interpolated fallback. |
| 007 | Add the measurement day to the database | X | New samples include `measurement_day`; Redis indexes store it. Historical samples from before this change can show empty day values. |
| 008 | Recommend a tool to display exported KML files | X | Recommended Google Earth, QGIS, and GPXSee; QGIS is preferred for inspecting attributes. |
| 009 | Include latency values in KML | X | KML placemark descriptions include latency min/avg/max. |
| 010 | Include train/car speed in KML | X | KML placemark descriptions include `speed_kmh` when available. |
| 011 | Include latitude and longitude in KML | X | KML points and descriptions include lat/lon. |
| 012 | Include coordinate type in KML | X | KML descriptions include coordinate type/source. |
| 013 | Make dialogs dockable on top and bottom borders | X | Summary, details, and chart panels support top-left, top-right, left, right, and bottom dock settings. |
| 014 | Make right and left collapse independent | X | Panels are independently positioned and collapsed; left collapse no longer depends on right panel state. |
| 015 | Explain why WLAN name cannot be read | X | Documented: macOS treats SSID/BSSID as location-sensitive and may redact without Location Services permission. |
| 016 | Add provider as a parameter | X | Settings dialog includes provider override; samples store `provider`. |
| 017 | Detect provider from traceroute/DNS when tracing Google | X | Default trace target is `google.com`; provider is filled from traceroute GeoIP org/host, falling back to DNS server. |
| 018 | Make the top-left panel transparent | X | Summary panel uses configurable opacity. |
| 019 | Add top-right logging/settings entry with a wheel/settings control | X | Top-right Details/Logs panel includes a Settings button and error log tab. |
| 020 | Allow changing modal background color | X | Settings dialog includes modal background color picker. |
| 021 | Add a transparency slider | X | Settings dialog includes panel and modal transparency sliders. |
| 022 | Add a plot bar showing GPS collection source/formula | X | Plot has a GPS source/correction bar with tooltips per sample. |
| 023 | Generate/prepare a Git repository for GitHub commit | X | Workspace is already a Git repo; `.gitignore` was added so runtime logs/Redis data are excluded. No commit was created automatically. |
| 024 | Show/store the history of changes in Markdown | X | This file stores the feature history and implementation notes. |

## Implemented Feature Set

- Live OpenStreetMap view with repeated reference tracks, including Berlin-Karlsruhe and Karlsruhe-Luxembourg.
- Numbered sample markers on the map.
- Marker hover tooltip with latency.
- Marker click selects the sample and scrolls the time plot to that position.
- Latency-colored route segments from green to red.
- Router GeoIP path shown as a purple route when traceroute GeoIP data is available.
- Dual-axis plot: left axis latency in milliseconds, right axis speed in km/h.
- Separate dashed grids for latency and speed.
- Labels every five samples for latency and speed.
- Scrollable horizontal plot.
- GPS source/correction bar above the plot, showing where each coordinate came from.
- Redis storage and reload across app restarts.
- Browser GPS ingestion endpoint and client-side collection button.
- ADB device list, refresh, and selected-device controls.
- Android ADB GPS/radio collection when USB debugging is authorized.
- Manual Wi-Fi SSID override.
- KML export with latency, speed, latitude, longitude, coordinate type, source, provider, measurement day, and position error fields.
- Settings dialog for GPS formula, sensors, provider override, modal color, transparency, and dock positions.
- Raw GPS is the default. No GPS correction is applied unless selected in settings.
- Interpolated fallback is disabled by default and only used when enabled in settings.
- Independent docking/collapse behavior for summary, details, and chart panels.

## GPS Correction Policy

The default GPS formula is `raw`, which means the app uses the coordinate source as received. It annotates the coordinate type but does not move the point.

The optional `constant_speed_track` formula estimates a position along the selected reference track based on constant velocity over that track's configured six-hour timeframe. When a raw GPS point exists, the app stores it as `raw_location`, replaces the displayed/exported coordinate with the estimated route point, and records `position_error_km`.

Correction tracks are stored with an ID, name, performed count, duration, and coordinate path. They are sorted by performed count. Current initial tracks are `berlin_karlsruhe` and `karlsruhe_luxembourg`; more repeated routes can be added to the same catalog.

Accelerometer data can be recorded, but it is not used by default for correction. Browser accelerometer readings drift quickly without calibrated sensor fusion, so using them as a positional correction source would be misleading unless a stronger model is added.

## HTML5 GPS Notes

HTML5 Geolocation is permission-based. On phones, the browser/OS may combine GNSS, Wi-Fi, cellular, Bluetooth, and IP-based hints. JavaScript receives latitude, longitude, optional altitude, optional speed/heading, and an accuracy radius in meters.

Android's coarse/fine location choice is part of the OS permission model. Browser JavaScript generally does not receive a direct "coarse" or "fine" flag; it receives the resulting accuracy estimate. A large `accuracy_m` should be treated as coarse or unreliable.

Browser GPS usually requires HTTPS or `localhost`, plus OS location permission for the browser.

## WLAN Name Limitation

macOS treats Wi-Fi SSID/BSSID information as location-sensitive. In this environment, system commands returned redacted or authorization-blocked SSID values. Elevated shell access may still not be enough if Location Services permission is not granted to the terminal/browser process. The app therefore provides a manual Wi-Fi name override.

## KML Viewing Recommendation

Recommended KML viewers:

- Google Earth Pro or Google Earth Web for quick visual inspection.
- QGIS for deeper analysis, attribute tables, styling, and joining with other geospatial data.
- GPXSee for lightweight route viewing when local desktop tooling is preferred.

For this prototype, QGIS is the best technical validation tool because it displays KML placemark descriptions and lets you inspect latency/speed fields per point.

## Git And Release Notes

The workspace is already a Git repository. `.gitignore` excludes runtime logs, Python cache files, and Redis persistence files so commits can focus on source and documentation.

No commit has been created automatically. The recommended first commit is:

```sh
git add moving_target_osm_dashboard.py .gitignore moving_target_osm_dashboard_feature_change_history_and_operational_notes_2026_06_29.md
git commit -m "Add realtime Moving Target 5G latency mapping dashboard"
```

GitHub authentication was previously observed for user `martibayoalemany9`; pushing still requires choosing or creating a target repository.
