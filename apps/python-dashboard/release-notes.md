# Python Dashboard Release Notes

## Current Feature Set

### Realtime OpenStreetMap Dashboard

The Python app serves a local browser dashboard backed by OpenStreetMap tiles.
It plots live samples, colors route segments by latency, keeps a compact
top-left operational panel, and exposes detailed sample metadata in side panels
and popups. The map follows the newest GPS position when live browser GPS is
active and can zoom to the latest known point on demand.

### HTTPS Latency Measurement

The sampler records HTTPS timing against multiple target URLs instead of using
ICMP ping. Each sample captures DNS, TCP connect, TLS, time-to-first-byte, and
total timing where curl exposes those fields. The dashboard summarizes minimum,
average, and maximum latency and stores per-target connection failures so
latency spikes can be compared with DNS, TLS, network, or radio events.

### Android ADB Device Integration

The dashboard discovers authorized Android devices through ADB and attempts
wireless ADB at `100.77.37.113:5555`. It can collect Android telephony, GPS, and
radio metadata when USB or wireless debugging is authorized. The top-left panel
now includes buttons for common ADB inspection and controlled write commands,
and the bottom panel includes an `ADB shell` tab for one-off commands against
the selected serial.

### Browser GPS Accuracy Recording

Browser geolocation samples store latitude, longitude, `accuracy_m`,
`gps_accuracy_m`, altitude, altitude accuracy, heading, speed, and browser
timestamp when the browser provides them. The backend enriches browser GPS
records with the latest latency array, latency summary, connection error
summary, and source sample sequence, making it easier to correlate browser
position quality with network timing.

### GPS Source And Route Correction

The app supports raw coordinates, browser GPS, Android ADB GPS, manual map pins,
and optional interpolation over a selected known route. It can infer the closest
route from recent real GPS samples and can reject implausible GPS jumps. The
bottom plot includes a GPS source bar so each plotted sample can be traced back
to its coordinate source and correction method.

### Redis Persistence

Redis is optional and configurable at startup or through Settings. When enabled,
the app stores latest sample state, sample history, Redis streams, per-sequence
sample keys, lightweight indexes, geospatial entries, browser GPS, and dashboard
logs under a configurable prefix. Runtime Redis URL and prefix changes are
picked up without restarting the Python process.

### GCP Redis Planning

The Settings modal includes a GCP Memorystore Redis planner. The browser sends
the desired project, region, instance name, tier, memory size, network, and key
prefix to the Python backend. The backend returns concrete `gcloud redis
instances create ...` commands and keeps the generated plan in app state, but it
does not execute `gcloud` or create billable cloud resources.

### Wi-Fi And Router Context

The sampler collects local Wi-Fi metadata when macOS exposes it, including SSID,
IP address, RSSI, noise, SNR, PHY mode, channel, and transmit rate. A Wi-Fi name
override is available because macOS can redact SSIDs without Location Services
permission. Short traceroute samples identify the first-hop access point and a
public egress hop for coarse router or provider context.

### Error, Event, And Query Panels

The bottom panel has tabs for the plot, error logs, application events, Redis
querying, and ADB shell output. Application events capture settings changes,
browser permission failures, ADB command runs, collection state changes, manual
pin operations, and Redis/query errors. The Redis query tab provides preset
time-window queries for inspecting stored event and error logs.

### Offline Collection And Rendering

`moving_client_data.py` remains available for collecting JSONL samples and
rendering static HTML maps outside the realtime dashboard. This is useful for
long trips, repeatable offline analysis, and keeping transport logs that can be
shared without running the live server.

### OSM Extract Management

The app can download selected country-level OSM extracts into local storage and
inspect local extract availability. Settings include storage status and a
consistency check so map-support files can be verified from the UI before or
during a trip.

### Android Auto Companion Support

The Python dashboard exposes `/api/samples`, which the Android Auto companion
app reads through `adb reverse tcp:8765 tcp:8765`. This keeps the Python app as
the local data source while the Android Auto module focuses on a driver-safe
template presentation.
