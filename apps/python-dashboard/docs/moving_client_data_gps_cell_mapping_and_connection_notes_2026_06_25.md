# Moving Target 5G Latency, GPS, Cell Mapping, and Connection Optimization Notes

Date: 2026-06-25  
Use case: measure Internet latency along a moving route and correlate it with Samsung GPS plus 5G/LTE radio properties over ADB.

## What Was Added

File:

```text
moving_client_data.py
```

It has two modes:

```bash
./moving_client_data.py collect
./moving_client_data.py render samples.jsonl
```

The collector is intentionally low-rate. It does not scan ports, enumerate hosts, or hammer the network. Each sample records:

- Timestamp.
- GPS coordinates from the Samsung, when ADB exposes a recent location.
- 5G NR/LTE cell fields from Android `dumpsys`, when Samsung exposes them.
- HTTPS latency timings from normal web endpoints.
- A JSONL record suitable for later analysis.

The renderer creates an HTML map with colored points:

- Green: lower latency.
- Yellow/orange: moderate latency.
- Red: high latency.

## Current Limitation

ADB is not installed on this Mac at the moment:

```text
zsh: command not found: adb
```

Until Android platform-tools are installed, the script can still be kept ready, but it cannot read Samsung GPS or 5G radio state.

## Samsung Setup

On the Samsung phone:

1. Open `Settings > About phone > Software information`.
2. Tap `Build number` seven times to enable Developer options.
3. Open `Settings > Developer options`.
4. Enable `USB debugging`.
5. Connect the phone by USB.
6. When prompted, allow USB debugging for this computer.
7. Keep `Location` enabled.
8. Open Google Maps, Organic Maps, or another GPS-using app so Android maintains a fresh location fix.

Helpful Samsung settings if ADB sees the device but the script cannot read useful state:

- Keep the phone unlocked during the first ADB authorization.
- Set USB mode to file transfer if charging-only mode behaves badly.
- In `Developer options`, enable `Mobile data always active` only if you are testing phone cellular behavior directly; it is not always needed.
- Do not enable aggressive experimental radio locks unless you are prepared for worse handovers.

## Install ADB

Preferred options on macOS:

```bash
brew install android-platform-tools
```

Or install Android Studio / Android SDK Platform Tools from Google and make sure `adb` is on `PATH`.

Then check:

```bash
adb devices -l
```

Expected authorized state:

```text
List of devices attached
R5XXXXXXXXX device product:... model:... device:...
```

If it says `unauthorized`, unlock the Samsung and approve the USB debugging prompt.

## Collect Samples

Recommended low-aggression run on a train:

```bash
./moving_client_data.py collect --interval 15 --samples 240
```

That records one sample every 15 seconds for about one hour.

For a shorter test:

```bash
./moving_client_data.py collect --interval 20 --samples 10
```

If multiple Android devices are attached:

```bash
adb devices -l
./moving_client_data.py collect --serial YOUR_DEVICE_SERIAL --interval 15
```

Output will look like:

```text
Writing samples to moving_client_data_samples_20260625T181500Z.jsonl
2026-06-25T18:15:00+00:00 seq=1 lat=52.5201 lon=13.4051 avg_ms=83.4 nr_pci=123 nr_arfcn=634080 lte_pci=...
```

Stop a continuous run with `Ctrl-C`.

## Render the Map

After collecting:

```bash
./moving_client_data.py render moving_client_data_samples_YYYYMMDDTHHMMSSZ.jsonl
```

This writes:

```text
moving_client_data_samples_YYYYMMDDTHHMMSSZ.html
```

Open the HTML file in a browser. It uses OpenStreetMap tiles and Leaflet from a CDN, so the map background needs Internet access. The raw points are embedded in the file.

## What "Which Antenna" Means

The phone usually cannot tell you the physical antenna mast name directly.

What ADB may expose:

- `nr_pci`: 5G NR physical cell ID.
- `nr_arfcn`: 5G NR frequency channel.
- `nr_bands`: 5G band list, for example `n78`.
- `nr_nci`: NR cell identity.
- `nr_tac`: tracking area code.
- `nr_ss_rsrp`: signal power; less negative is better.
- `nr_ss_rsrq`: signal quality.
- `nr_ss_sinr`: signal-to-interference-plus-noise ratio; higher is better.
- LTE equivalents such as `lte_pci`, `lte_earfcn`, `lte_tac`, `lte_rsrp`, and `lte_rsrq`.

These fields identify the serving cell sector well enough to see handovers and bad coverage zones. They do not always identify the exact tower coordinates unless you join them with a cell database such as CellMapper or an operator dataset.

## How to Read the Results

Useful correlations:

- Latency spike with same cell ID: likely congestion, backhaul issue, or radio quality degradation.
- Latency spike plus cell change: likely handover or reselection.
- Bad `RSRP` and bad `SINR`: radio signal problem.
- Good `RSRP` but bad `SINR`: interference or overloaded/shared radio conditions.
- Good radio numbers but high latency: core-network routing, CGNAT, VPN, DNS, or server path issue.
- Repeated loss during high-speed sections: handover problems, tunnel sections, cuttings, rural gaps, or train-window attenuation.

## Optimization While Moving Around 200 km/h

Practical options:

- Put the phone/router near a window and keep it stationary.
- Avoid burying it under a laptop, bag, metal table, or power bank.
- Avoid forcing NR-only mode. At train speed, the network often needs LTE anchor and fast fallback for reliable handover.
- Prefer `5G Auto` over hard locks unless you are doing controlled testing.
- If tethering through a phone, use USB tethering when possible; it avoids Wi-Fi tethering contention inside the train.
- Disable unnecessary VPN/private relay during measurement if you want to diagnose the mobile network itself.
- Keep the measurement target stable; changing servers confuses the latency map.
- Use a low sample rate such as 10-20 seconds. Faster sampling can look noisy and may trigger filtering.

Things that usually do not help:

- Locking to one cell while moving fast.
- Forcing a single band without knowing the operator deployment.
- Repeated traceroute or ping floods.
- Changing APN settings unless the operator explicitly documents a better APN.

Best hypothesis for a moving target around 200 km/h:

The phone is frequently handing over between trackside macro cells and possibly switching between 5G NSA, LTE anchor cells, and LTE fallback. Latency problems are often caused by handover timing, signal blockage inside the train, congestion on train-filled cells, or a VPN/tunnel reestablishing after the radio path changes.

## Safer Probe Defaults

The script uses HTTPS timing, not aggressive fingerprinting.

Default targets:

```text
https://www.apple.com/library/test/success.html
https://cloudflare.com/cdn-cgi/trace
https://www.google.com/generate_204
```

You can choose a single stable target:

```bash
./moving_client_data.py collect --target https://cloudflare.com/cdn-cgi/trace --interval 15
```

## Files

- `moving_client_data.py`: collector and map renderer.
- `ice_train_5g_latency_gps_cell_tower_mapping_and_connection_optimization_notes_2026_06_25.md`: this guide.
