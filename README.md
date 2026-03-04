# StarNav — Starlink to MAVLink Position Forwarding

StarNav bridges a Starlink dish and a MAVLink autopilot (e.g. CubePilot, ArduPilot). It continuously reads the dish's GPS position over gRPC and forwards it to an autopilot as a MAVLink external position estimate (`COMMAND_INT 43003`), enabling the autopilot's EKF to fuse Starlink-derived position with its own sensors.

It runs as a persistent service on an OpenWRT router sitting between the Starlink dish and the autopilot.

---

## How It Works

```
Starlink Dish ──gRPC──► OpenWRT Router ──MAVLink UDP──► Autopilot (CubePilot)
  192.168.100.1:9200       (starnav.py)                   GLOBAL_POSITION_INT
                                │                          GPS_RAW_INT
                                │                          ATTITUDE
                           /tmp/starnav_status.json
                                │
                           Web UI (port 8081)
```

1. **Starlink position** — `starlink_grpc.get_location()` is called every 200 ms, returning latitude, longitude, altitude, and a 1σ horizontal uncertainty value (metres).
2. **Quality gating** — A position is only forwarded to the autopilot when *both* conditions are met:
   - The 99% uncertainty (`1σ × 3`) has been continuously below `uncertainty_limit` for at least `min_stable_time` seconds, **or**
   - A significant accuracy improvement (`correction` flag) is detected — the 99% uncertainty dropped by more than `accuracy_jump_threshold` in a single cycle.
3. **MAVLink send** — When gated, `COMMAND_INT` with command ID 43003 is sent, carrying the Starlink position and 1σ accuracy. The autopilot responds with `COMMAND_ACK`; the result is tracked and shown in the web UI.
4. **GPS global origin** — On startup, StarNav reads the Starlink position and sends `SET_GPS_GLOBAL_ORIGIN` to the autopilot so its local-frame EKF is anchored correctly.
5. **MAVLink receive** — `GPS_RAW_INT`, `GLOBAL_POSITION_INT`, and `ATTITUDE` messages are drained from the autopilot each cycle to keep the web UI and CSV logs populated with live aircraft data.

### Fake GPS burst

A "Fake GPS" mode sends a `GPS_INPUT` message (3D fix, GPS2 port) using the current Starlink position. This is useful for forcing an initial EKF fix without needing a real GPS receiver locked on. The burst lasts 5 seconds at 5 Hz and is triggered from the web UI or by creating the file `/tmp/starnav_fakegps_trigger`.

### Timestamp handling

MAVLink spec requires the transmission timestamp to wrap at ≤250 seconds (~10 µs resolution with a 32-bit float). StarNav uses wall-clock time when NTP is available and falls back to monotonic time if the system clock is not yet synced (the board has no RTC).

---

## Hardware Requirements

- **OpenWRT router** with Python 3.11+ (tested on GL.iNet BE9300)
- **Starlink dish** accessible at `192.168.100.1:9200` (standard Starlink LAN)
- **MAVLink autopilot** reachable over UDP from the router (e.g. CubePilot via `udpin:0.0.0.0:14552`)

---

## Installation

### 1. Clone the repository on your development machine

```sh
git clone https://github.com/jack7169/Starlink_Mavlink_PNT.git
cd Starlink_Mavlink_PNT
```

### 2. Copy the project to the router

The router does not have `rsync`. Use `tar` + `scp -O` (legacy SCP protocol):

```sh
tar czf /tmp/starnav-install.tar.gz \
  --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' .

scp -O /tmp/starnav-install.tar.gz root@<router-ip>:/tmp/

ssh root@<router-ip> \
  'mkdir -p /tmp/starnav-install && tar xzf /tmp/starnav-install.tar.gz -C /tmp/starnav-install'
```

### 3. Run the installer on the router

```sh
ssh root@<router-ip> 'cd /tmp/starnav-install && sh install.sh'
```

The installer is idempotent — safe to re-run after updates. It will:

- Install `python3` and `ntpd` via `opkg`
- Install Python packages: `grpcio`, `protobuf`, `yagrc`, `typing-extensions`, `pymavlink`
- Copy `starnav.py`, `starnav.sh`, and the required `starlink-grpc-tools` modules to `/opt/starnav/`
- Install `/etc/starnav.conf` (preserved on re-runs; new defaults saved as `.new`)
- Install and enable the procd init script at `/etc/init.d/starnav`
- Configure a dedicated `uhttpd` instance on port 8081 for the web UI
- Install web UI files to `/www/starnav/`

> **Note:** `grpcio` may need to compile from source on ARM. This can take 10–30 minutes on first install. Subsequent runs skip this step if the package is already installed.

### 4. Configure

```sh
ssh root@<router-ip> vi /etc/starnav.conf
```

At minimum, set `connection` under `[mavlink]` to point at your autopilot. See the [Configuration Reference](#configuration-reference) below.

### 5. Start the service

```sh
ssh root@<router-ip> /etc/init.d/starnav start
```

The service starts automatically on every boot.

---

## Fresh install (removing existing installation)

```sh
ssh root@<router-ip> '
  /etc/init.d/starnav stop 2>/dev/null
  /etc/init.d/starnav disable 2>/dev/null
  rm -rf /opt/starnav /etc/init.d/starnav /etc/starnav.conf /www/starnav
  uci -q delete uhttpd.starnav; uci commit uhttpd
'
```

Then follow steps 2–5 above.

---

## Configuration Reference

`/etc/starnav.conf` uses INI format. All values have built-in defaults.

### `[starlink]`

| Key | Default | Description |
|-----|---------|-------------|
| `dish_address` | `192.168.100.1:9200` | gRPC address of the Starlink dish |
| `gps_mode` | `auto` | Dish GPS behaviour on startup: `auto` (leave as-is), `enable`, or `disable` |

### `[mavlink]`

| Key | Default | Description |
|-----|---------|-------------|
| `connection` | `udpin:0.0.0.0:14552` | pymavlink connection string |
| `source_system` | `242` | MAVLink system ID for StarNav |
| `source_component` | `192` | MAVLink component ID for StarNav |
| `target_system` | `2` | Autopilot system ID |
| `target_component` | `1` | Autopilot component ID |

**Common connection strings:**

| String | Meaning |
|--------|---------|
| `udpin:0.0.0.0:14552` | Listen for autopilot on UDP port 14552 |
| `udp:192.168.1.100:14550` | Send to autopilot at fixed IP |
| `tcp:192.168.1.100:5760` | TCP connection to autopilot |

### `[thresholds]`

| Key | Default | Description |
|-----|---------|-------------|
| `uncertainty_limit` | `200.0` | Max 99% uncertainty (metres) to allow forwarding |
| `min_stable_time` | `3.0` | Seconds uncertainty must stay below limit before sending |
| `accuracy_jump_threshold` | `1.5` | Uncertainty drop (metres) that triggers an immediate correction send |

### `[logging]`

| Key | Default | Description |
|-----|---------|-------------|
| `csv_dir` | `/root/starlink_logs` | Directory for CSV log files |
| `csv_enabled` | `true` | Set to `false` to disable CSV logging (saves flash writes) |
| `max_log_size_mb` | `100` | Maximum total CSV log folder size; oldest files are deleted to stay under the limit |

### `[paths]`

| Key | Default | Description |
|-----|---------|-------------|
| `install_dir` | `/opt/starnav` | Root install directory on the router |
| `grpc_tools_dir` | `starlink-grpc-tools` | Path to starlink-grpc-tools relative to `install_dir` |

---

## Web UI

The web UI runs on port **8081** via a dedicated `uhttpd` instance, separate from the router's main admin interface.

```
http://<router-ip>:8081/
```

### Features

- **Live status** — Process running state, PID, data freshness, dish address, MAVLink connection
- **Starlink position** — Lat/lon/alt, 1σ and 99% uncertainty, stable time countdown, sending status, correction flag, EKF acceptance (last `COMMAND_ACK` result)
- **Aircraft data** — GPS position (`GPS_RAW_INT`), EKF position (`GLOBAL_POSITION_INT`), roll/pitch/yaw (`ATTITUDE`), 3D position error vs Starlink
- **Satellite map** — Live Leaflet map (ESRI satellite imagery) showing Starlink dish position, aircraft GPS position, uncertainty circle, and a 60-second position trail
- **Debug log** — Live log stream via Server-Sent Events with colour-coded severity levels; pauseable
- **Service control** — Start / Stop / Restart buttons (calls `/etc/init.d/starnav`)
- **Fake GPS trigger** — Sends a 5-second GPS2 burst at 5 Hz using the current Starlink position

### CGI endpoints

| Path | Description |
|------|-------------|
| `cgi-bin/status.cgi` | JSON: process state + live position data from `/tmp/starnav_status.json` |
| `cgi-bin/logs.cgi` | SSE stream: recent log history then live `logread` tail |
| `cgi-bin/api.cgi` | POST JSON `{action}`: `start`, `stop`, `restart`, `status`, `fake_gps` |

---

## Logs and diagnostics

```sh
# Live log output
logread -f -e starnav

# Service status
/etc/init.d/starnav status

# CSV logs (if enabled)
ls /root/starlink_logs/
```

The console output format is:

```
HH:MM:SS.mmm | GPS: lat,lon,alt | EKF: lat,lon,alt | Starlink: lat,lon,alt | R/P/Y: r,p,y | 3D Err: Xm | Star 99%: Ym | Correction: N
```

`>>> Sending External Position Estimate <<<` is printed whenever a position update is forwarded to the autopilot.

---

## Project structure

```
.
├── starnav.py              # Main forwarding daemon
├── starnav.sh              # Wrapper: reads config, optional dish GPS control, launches starnav.py
├── starnav.init            # procd init script (/etc/init.d/starnav)
├── starnav.conf            # Default configuration file
├── install.sh              # OpenWRT installer (idempotent)
├── www/
│   └── starnav/
│       ├── index.html      # Web UI (single-page, no build step)
│       └── cgi-bin/
│           ├── status.cgi  # Process + position status API
│           ├── logs.cgi    # SSE log stream
│           └── api.cgi     # Service control API
└── starlink-grpc-tools/    # Submodule: Starlink gRPC client library
    ├── starlink_grpc.py    # gRPC location + control calls
    ├── dish_control.py     # Dish GPS enable/disable
    └── loop_util.py        # Utility helpers
```

---

## Dependencies

### System (installed via `opkg`)

- `python3` (3.11+)
- `ntpd` — required for accurate wall-clock timestamps (no RTC on the router)

### Python (installed via `pip`)

| Package | Purpose |
|---------|---------|
| `grpcio` | gRPC transport for Starlink dish API |
| `protobuf` | Protobuf deserialisation |
| `yagrc` | Reflection-based gRPC client (no `.proto` files needed) |
| `typing-extensions` | Python version compatibility |
| `pymavlink` | MAVLink message encode/decode and connection management |

---

## MAVLink protocol details

| Message | Direction | Usage |
|---------|-----------|-------|
| `HEARTBEAT` | TX | Sent on startup loop to register with the autopilot's UDP server |
| `HEARTBEAT` | RX | Used to confirm autopilot presence before proceeding |
| `SET_GPS_GLOBAL_ORIGIN` | TX | Sets EKF reference frame origin to Starlink position on startup |
| `COMMAND_INT (43003)` | TX | External position estimate: param1=transmission_time, param3=1σ accuracy, param5/6=lat/lon |
| `COMMAND_ACK` | RX | Autopilot acknowledgement of the position estimate; result=0 means accepted |
| `GPS_RAW_INT` | RX | Aircraft raw GPS position (displayed in web UI, logged to CSV) |
| `GLOBAL_POSITION_INT` | RX | EKF-fused aircraft position |
| `ATTITUDE` | RX | Aircraft roll/pitch/yaw |
| `GPS_INPUT` | TX | Fake GPS burst (GPS2 port, 3D fix) for EKF initialisation |
