# Machine Doctor — Umbrel App

Machine Doctor is a hardware diagnostics app for Umbrel nodes. The first version focuses on CPU health: it runs a controlled `stress-ng` CPU test, tracks per-core load, reads host temperatures and frequencies where available, and saves a JSON report.

## Features

- 5-minute CPU stress test by default
- Live total CPU usage and per-core usage
- CPU temperature detection when exposed by the host
- Sensor inventory from `/sys/class/thermal` and `/sys/class/hwmon`
- Per-core frequency readings where available
- PASS / WARNING / FAIL result
- Persistent JSON reports in `/data/reports`
- Cancel button and single-test-at-a-time protection

## Installation

Add the Albercoin Store to Umbrel:

```bash
sudo ~/umbrel/scripts/repo add https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin
sudo ~/umbrel/scripts/repo update
```

Then install **Machine Doctor** from the Umbrel App Store UI.

## Permissions Required

Machine Doctor runs inside Docker and reads host hardware information through read-only mounts:

- `/proc:/host/proc:ro` for CPU stats, CPU model and load average
- `/sys:/host/sys:ro` for thermal zones, hwmon sensors and CPU frequencies
- `/dev:/host/dev:ro` reserved for future disk diagnostics

The first release uses `privileged: true` for broad Umbrel OS, Raspberry Pi and mini PC compatibility. The app does not expose a shell and does not accept arbitrary commands from the web interface.

The Umbrel package runs from the standard `${APP_DATA_DIR}/src` source mount and uses `python:3.12-slim` as its base image, so installation does not require Umbrel to build a local Docker image.

## Configuration

The CPU test is controlled with environment variables in `docker-compose.yml`:

```text
TEST_DURATION_SECONDS=300
CPU_WARNING_TEMP=80
CPU_CRITICAL_TEMP=90
CPU_MIN_EXPECTED_LOAD=90
```

`TEST_DURATION_SECONDS` is clamped between 5 and 3600 seconds.

## How To Test

For a quick development test, temporarily set:

```yaml
TEST_DURATION_SECONDS: "30"
```

Then rebuild and start the app through Umbrel. Open Machine Doctor, click **Start CPU test**, watch live samples, and open **View latest report** after the test finishes.

## Report Format

Reports are saved as JSON files in `/data/reports` and include:

- App and test metadata
- Start and finish timestamps
- CPU model and core count
- Max and average CPU usage
- Max and average temperature
- Warnings and errors
- Full per-second samples
- `stress-ng` output

## Known Limitations

- Some systems do not expose CPU temperature inside containers, even with `/sys` mounted.
- Per-core temperatures are not universally available; Machine Doctor lists detected sensors instead.
- CPU frequency paths vary by kernel and CPU governor.
- The first release does not yet implement disk, full thermal or RAM checks.
- A CPU stress test can temporarily slow down other services on low-power nodes.

## Roadmap

Priority order:

1. CPU Check
2. SSD / HDD Check using `smartctl`
3. Full Thermal Analysis
4. RAM Check using `memtester` or `stress-ng --vm`

## Architecture

```text
src/
  main.py
  requirements.txt
  modules/
    cpu/
      service.py
      sensors.py
      stress.py
      report.py
    disk/
      README.md
    thermal/
      README.md
    ram/
      README.md
  static/
    index.html
    styles.css
    app.js
```

## Security

- No arbitrary commands can be submitted from the UI.
- The only executed diagnostic command is fixed: `stress-ng --cpu 0 --timeout <duration>s --metrics-brief`.
- Only one CPU test can run at a time.
- The CPU test has a bounded duration.
- The app attempts to terminate the `stress-ng` child process when cancelling or shutting down.

## Support

Report issues at:

https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin/issues
