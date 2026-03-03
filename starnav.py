#!/usr/bin/env python3
import time
import math
from datetime import datetime, UTC
import csv
import configparser
import argparse
import os
import sys
import signal
import json

# -------------------------
# Argument Parsing
# -------------------------
parser = argparse.ArgumentParser(description="Starlink MAVLink Position Forwarder")
parser.add_argument(
    "-c", "--config",
    default="/etc/starnav.conf",
    help="Path to config file (default: /etc/starnav.conf)"
)
args = parser.parse_args()

# -------------------------
# Configuration
# -------------------------
config = configparser.ConfigParser()

if not os.path.exists(args.config):
    print(f"Config file not found: {args.config}", file=sys.stderr)
    sys.exit(1)

config.read(args.config)

# Starlink
DISH_ADDRESS = config.get("starlink", "dish_address", fallback="192.168.100.1:9200")

# MAVLink
MAVLINK_CONNECTION = config.get("mavlink", "connection", fallback="udp:127.0.0.1:10004")
SOURCE_SYSTEM = config.getint("mavlink", "source_system", fallback=242)
SOURCE_COMPONENT = config.getint("mavlink", "source_component", fallback=192)
TARGET_SYS = config.getint("mavlink", "target_system", fallback=18)
TARGET_COMP = config.getint("mavlink", "target_component", fallback=1)

# Thresholds
UNCERTAINTY_LIMIT = config.getfloat("thresholds", "uncertainty_limit", fallback=20.0)
MIN_STABLE_TIME = config.getfloat("thresholds", "min_stable_time", fallback=3.0)
ACCURACY_JUMP_THRESHOLD = config.getfloat("thresholds", "accuracy_jump_threshold", fallback=1.5)

# Logging
CSV_DIR = config.get("logging", "csv_dir", fallback=".")
CSV_ENABLED = config.getboolean("logging", "csv_enabled", fallback=True)
MAX_LOG_SIZE_BYTES = config.getfloat("logging", "max_log_size_mb", fallback=100.0) * 1024 * 1024
CSV_FILE = os.path.join(
    CSV_DIR,
    "StarNav_" + datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".csv"
)

def enforce_log_limit():
    """Delete oldest StarNav CSV logs until folder is under MAX_LOG_SIZE_BYTES."""
    try:
        logs = sorted(
            (f for f in os.listdir(CSV_DIR) if f.startswith("StarNav_") and f.endswith(".csv")),
            key=lambda f: os.path.getmtime(os.path.join(CSV_DIR, f))
        )
        total = sum(os.path.getsize(os.path.join(CSV_DIR, f)) for f in logs)
        while total > MAX_LOG_SIZE_BYTES and len(logs) > 1:
            oldest = logs.pop(0)
            path = os.path.join(CSV_DIR, oldest)
            size = os.path.getsize(path)
            os.remove(path)
            total -= size
            print(f"Deleted old log: {oldest} ({size / 1024:.0f} KB)")
    except OSError as e:
        print(f"Log cleanup error: {e}")

# -------------------------
# Imports requiring PYTHONPATH
# -------------------------
os.environ["MAVLINK20"] = "1"
from pymavlink import mavutil
import starlink_grpc

# WGS84 constants
A = 6378137.0          # semi-major axis
E2 = 6.69437999014e-3  # eccentricity squared

# Minimum plausible epoch (2024-01-01 UTC) to detect unsynced RTC
MIN_SANE_EPOCH = 1704067200.0
_ntp_warned = False

def get_transmission_time():
    """Return transmission timestamp for MAVLink, wrapping at 250 seconds.

    Uses NTP wall-clock time when available. Falls back to monotonic
    time if the system clock looks unsynced (no RTC, pre-2024 epoch),
    so position estimates are never blocked by NTP issues.

    Spec requires wrap at no more than 250 seconds (~10us accuracy
    with 32-bit float).
    """
    global _ntp_warned
    now = time.time()
    if now >= MIN_SANE_EPOCH:
        if _ntp_warned:
            print("NTP time now available -- switching to wall-clock timestamps.")
            _ntp_warned = False
        return now % 250.0
    else:
        if not _ntp_warned:
            print("WARNING: System clock not synced (no RTC/NTP). Using monotonic time.")
            _ntp_warned = True
        return time.monotonic() % 250.0

# -------------------------
# Convert LLA to ECEF
# -------------------------
def lla_to_ecef(lat, lon, alt):
    lat = math.radians(lat)
    lon = math.radians(lon)

    N = A / math.sqrt(1 - E2 * math.sin(lat)**2)

    x = (N + alt) * math.cos(lat) * math.cos(lon)
    y = (N + alt) * math.cos(lat) * math.sin(lon)
    z = (N * (1 - E2) + alt) * math.sin(lat)

    return x, y, z

# -------------------------
# Compute 3D Distance
# -------------------------
def distance_3d(lat1, lon1, alt1, lat2, lon2, alt2):
    x1, y1, z1 = lla_to_ecef(lat1, lon1, alt1)
    x2, y2, z2 = lla_to_ecef(lat2, lon2, alt2)

    return math.sqrt(
        (x2 - x1)**2 +
        (y2 - y1)**2 +
        (z2 - z1)**2
    )

# -------------------------
# Web UI status file
# -------------------------
STATUS_FILE          = "/tmp/starnav_status.json"
FAKEGPS_TRIGGER_FILE = "/tmp/starnav_fakegps_trigger"

# GPS epoch constants (for GPS_INPUT time fields)
_GPS_EPOCH_UNIX = 315964800   # 1980-01-06 00:00:00 UTC as Unix timestamp
_GPS_SECS_PER_WEEK = 604800

def send_fake_gps(lat, lon, alt):
    """Send a single GPS_INPUT message with a fake 3D fix to GPS2 (gps_id=1)."""
    gps_secs   = time.time() - _GPS_EPOCH_UNIX
    week       = int(gps_secs / _GPS_SECS_PER_WEEK)
    ms_in_week = int((gps_secs % _GPS_SECS_PER_WEEK) * 1000)
    # ignore_flags: ignore velocity (8|16) and speed accuracy (32)
    mav.mav.gps_input_send(
        int(time.time() * 1e6),  # time_usec
        1,                        # gps_id  (GPS2 = index 1)
        56,                       # ignore_flags
        ms_in_week,               # time_week_ms
        week,                     # time_week
        3,                        # fix_type  (3 = 3D fix)
        int(lat * 1e7),           # lat  degE7
        int(lon * 1e7),           # lon  degE7
        float(alt),               # alt  metres MSL
        6.9,                      # hdop
        6.9,                      # vdop
        0.0, 0.0, 0.0,            # vn, ve, vd  (ignored)
        0.0,                      # speed_accuracy (ignored)
        6.9,                      # horiz_accuracy  m
        9.0,                      # vert_accuracy   m
        20,                       # satellites_visible
        0,                        # yaw  (0 = not set)
    )


def write_status_file(data):
    """Atomically write current position/state to JSON for the web UI."""
    def sf(v, d=7):
        """Convert float to rounded value, or None if NaN/Inf."""
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return round(v, d) if isinstance(v, float) else v

    obj = {
        "timestamp": data["ts"].strftime("%Y-%m-%dT%H:%M:%S.") + data["ts"].strftime("%f")[:3],
        "dish_address": DISH_ADDRESS,
        "mavlink_connection": MAVLINK_CONNECTION,
        "uncertainty_limit": UNCERTAINTY_LIMIT,
        "min_stable_time": MIN_STABLE_TIME,
        "starlink": {
            "lat": sf(data["star_lat"]),
            "lon": sf(data["star_lon"]),
            "alt": sf(data["star_alt"], 2),
            "uncertainty_1sigma": sf(data["star_unc_1sigma"], 3),
            "uncertainty_99": sf(data["star_unc_99"], 3),
        },
        "gps": {
            "lat": sf(data["gps_lat"]),
            "lon": sf(data["gps_lon"]),
            "alt": sf(data["gps_alt"], 2),
        },
        "ekf": {
            "lat": sf(data["ekf_lat"]),
            "lon": sf(data["ekf_lon"]),
            "alt": sf(data["ekf_alt"], 2),
        },
        "attitude": {
            "roll":  sf(data["roll"],  2),
            "pitch": sf(data["pitch"], 2),
            "yaw":   sf(data["yaw"],   2),
        },
        "accuracy_3d":     sf(data["accuracy"], 3),
        "sending":         data["sending"],
        "correction":      data["correction"],
        "stable_seconds":  sf(data["stable_secs"], 1),
        "last_ack_result": data["last_ack_result"],
        "fake_gps_active": data["fake_gps_active"],
    }
    try:
        tmp = STATUS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, STATUS_FILE)
    except OSError:
        pass

# -------------------------
# Starlink gRPC connection
# -------------------------
starlink_context = starlink_grpc.ChannelContext(target=DISH_ADDRESS)
print(f"Starlink dish target: {DISH_ADDRESS}")

# -------------------------
# MAVLink connection
# -------------------------
mav = mavutil.mavlink_connection(
    MAVLINK_CONNECTION,
    source_system=SOURCE_SYSTEM,
    source_component=SOURCE_COMPONENT
)
print(f"MAVLink endpoint: {MAVLINK_CONNECTION}")
print("Waiting for autopilot heartbeat...")

# Send heartbeats to register with the Cube's UDP server.
# The server won't send anything back until it receives a packet from us.
while True:
    mav.mav.heartbeat_send(
        mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
        0, 0,
        mavutil.mavlink.MAV_STATE_ACTIVE
    )
    hb = mav.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
    if hb and hb.get_srcSystem() == TARGET_SYS:
        print(f"Heartbeat received from system {hb.get_srcSystem()}!")
        break

# -------------------------
# Set GPS global origin from Starlink
# -------------------------
origin = starlink_grpc.get_location(context=starlink_context)
ref_lat = float(origin.lla.lat)
ref_lon = float(origin.lla.lon)
ref_alt = float(origin.lla.alt)

print(f"Setting origin to Starlink: {ref_lat}, {ref_lon}, {ref_alt}")

mav.mav.set_gps_global_origin_send(
    TARGET_SYS,
    int(ref_lat * 1e7),             # latitude (degE7)
    int(ref_lon * 1e7),             # longitude (degE7)
    int(ref_alt * 1000),            # altitude (mm, MSL)
    int(time.time() * 1e6)          # time_usec
)

print("Origin set.")

# -------------------------
# Cleanup handler
# -------------------------
def cleanup(signum=None, frame=None):
    print("Shutting down...")
    starlink_context.close()
    sys.exit(0)

signal.signal(signal.SIGTERM, cleanup)
signal.signal(signal.SIGINT, cleanup)

# -------------------------
# CSV setup
# -------------------------
if CSV_ENABLED:
    with open(CSV_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Date",
            "Time",
            "GPS Lat",
            "GPS Lon",
            "GPS Alt (m)",
            "EKF Lat",
            "EKF Lon",
            "EKF Alt (m)",
            "Starlink Lat",
            "Starlink Lon",
            "Starlink Alt (m)",
            "Roll (deg)",
            "Pitch (deg)",
            "Yaw (deg)",
            "3D Accuracy (m)",
            "Starlink Uncertainty (m, 99%)",
            "Correction"
        ])
    print(f"Logging to {CSV_FILE}")
    enforce_log_limit()
else:
    print("CSV logging disabled")

star_unc_prev = None
unc_below_threshold_start = None
_last_log_cleanup = 0

def wait_for_ack(mav, command_id, timeout=1.0):
    start_time = time.monotonic()

    while time.monotonic() - start_time < timeout:
        ack = mav.recv_match(type="COMMAND_ACK", blocking=False)
        if ack and ack.command == command_id:
            return ack

    return None

# -------------------------
# Main loop
# -------------------------
try:
    # Persistent state for latest MAVLink messages
    gps_lat = gps_lon = gps_alt = float("nan")
    ekf_lat = ekf_lon = ekf_alt = float("nan")
    roll = pitch = yaw = float("nan")

    # Persistent state for Starlink and derived values (fallback when inner try fails)
    star_lat = star_lon = star_alt = float("nan")
    star_unc_1sigma = star_unc_99 = float("nan")
    accuracy = float("nan")
    correction = "N"
    timestamp = datetime.now(UTC)
    last_ack_result = None   # None = never sent; 0 = accepted; other = rejected
    fake_gps_until  = None   # monotonic time when fake GPS burst should stop

    while True:
        try:
            timestamp = datetime.now(UTC)

            # ---- Drain MAVLink buffer, keep latest of each type ----
            while True:
                msg = mav.recv_match(
                    type=["GPS_RAW_INT", "GLOBAL_POSITION_INT", "ATTITUDE"],
                    blocking=False
                )
                if msg is None:
                    break
                if msg.get_srcSystem() != TARGET_SYS:
                    continue

                msg_type = msg.get_type()
                if msg_type == "GPS_RAW_INT":
                    gps_lat = msg.lat / 1e7
                    gps_lon = msg.lon / 1e7
                    gps_alt = msg.alt / 1000.0  # mm -> meters
                elif msg_type == "GLOBAL_POSITION_INT":
                    ekf_lat = msg.lat / 1e7
                    ekf_lon = msg.lon / 1e7
                    ekf_alt = msg.alt / 1000.0  # mm -> meters
                elif msg_type == "ATTITUDE":
                    roll = math.degrees(msg.roll)
                    pitch = math.degrees(msg.pitch)
                    yaw = math.degrees(msg.yaw)

            # ---- Read Starlink ----
            loc = starlink_grpc.get_location(context=starlink_context)
            star_lat = float(loc.lla.lat)
            star_lon = float(loc.lla.lon)
            star_alt = float(loc.lla.alt)
            star_unc_1sigma = float(loc.sigma_m)    # 1 standard deviation
            star_unc_99 = star_unc_1sigma * 3        # 99% confidence

            # ---- Calculate 3D Accuracy ----
            accuracy = distance_3d(
                gps_lat, gps_lon, gps_alt,
                star_lat, star_lon, star_alt
            )

            # ---- Determine Correction Flag ----
            correction = "N"
            if star_unc_prev is not None:
                if star_unc_prev - star_unc_99 > ACCURACY_JUMP_THRESHOLD:
                    correction = "Y"

            star_unc_prev = star_unc_99

            # ---- Log to CSV ----
            if CSV_ENABLED:
                with open(CSV_FILE, "a", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        timestamp.strftime("%Y-%m-%d"),
                        timestamp.strftime("%H:%M:%S.%f")[:-3],
                        gps_lat,
                        gps_lon,
                        gps_alt,
                        ekf_lat,
                        ekf_lon,
                        ekf_alt,
                        star_lat,
                        star_lon,
                        star_alt,
                        roll,
                        pitch,
                        yaw,
                        accuracy,
                        star_unc_99,
                        correction
                    ])
                now_mono = time.monotonic()
                if now_mono - _last_log_cleanup > 60:
                    _last_log_cleanup = now_mono
                    enforce_log_limit()

            print(
                f"{timestamp.strftime('%H:%M:%S.%f')[:-3]} | "
                f"GPS: {gps_lat},{gps_lon},{gps_alt} | "
                f"EKF: {ekf_lat},{ekf_lon},{ekf_alt} | "
                f"Starlink: {star_lat},{star_lon},{star_alt} | "
                f"R/P/Y: {roll:.1f},{pitch:.1f},{yaw:.1f} | "
                f"3D Err: {accuracy:.2f}m | "
                f"Star 99%: {star_unc_99:.2f}m |"
                f"Correction: {correction}"
            )

        except Exception as e:
            print(f"Logging error: {e}")

        now_monotonic = time.monotonic()

        # Track continuous low uncertainty duration
        if star_unc_99 < UNCERTAINTY_LIMIT:
            if unc_below_threshold_start is None:
                unc_below_threshold_start = now_monotonic
        else:
            unc_below_threshold_start = None

        stable_long_enough = (
            unc_below_threshold_start is not None and
            (now_monotonic - unc_below_threshold_start) >= MIN_STABLE_TIME
        )

        # Only send if BOTH conditions are satisfied
        if correction == "Y" or stable_long_enough:

            unc_below_threshold_start = None
            print(">>> Sending External Position Estimate <<<")

            mav.mav.command_int_send(
                TARGET_SYS, TARGET_COMP,
                0, 43003,
                0, 0,
                get_transmission_time(),    # param1: transmission_time (wraps at 250s per spec)
                0,                          # param2: processing_time (0 = unknown)
                star_unc_1sigma,            # param3: accuracy (1 standard deviation per spec)
                0,                          # param4: empty
                int(star_lat * 1e7),        # param5: latitude
                int(star_lon * 1e7),        # param6: longitude
                math.nan                    # param7: altitude (NaN, not yet supported)
            )

            # ---- Wait for ACK ----
            ack = wait_for_ack(mav, 43003, timeout=1.0)

            if ack:
                last_ack_result = ack.result
                print(
                    f"ACK Received | "
                    f"Command: {ack.command} | "
                    f"Result: {ack.result}"
                )
            else:
                print("No COMMAND_ACK received.")

        # ---- Fake GPS trigger ----
        if os.path.exists(FAKEGPS_TRIGGER_FILE):
            try:
                os.remove(FAKEGPS_TRIGGER_FILE)
            except OSError:
                pass
            fake_gps_until = now_monotonic + 5.0
            print(">>> Fake GPS burst started (5 s @ 5 Hz on GPS2) <<<")

        if fake_gps_until is not None:
            if now_monotonic < fake_gps_until:
                if not (math.isnan(star_lat) or math.isnan(star_lon) or math.isnan(star_alt)):
                    send_fake_gps(star_lat, star_lon, star_alt)
            else:
                fake_gps_until = None
                print(">>> Fake GPS burst ended <<<")

        # Write status file for web UI (every iteration, ~5 Hz)
        _stable_secs = None
        if unc_below_threshold_start is not None:
            _stable_secs = now_monotonic - unc_below_threshold_start
        write_status_file({
            "ts":             timestamp,
            "star_lat":       star_lat,       "star_lon":       star_lon,
            "star_alt":       star_alt,
            "star_unc_1sigma": star_unc_1sigma, "star_unc_99":   star_unc_99,
            "gps_lat":        gps_lat,         "gps_lon":       gps_lon,
            "gps_alt":        gps_alt,
            "ekf_lat":        ekf_lat,         "ekf_lon":       ekf_lon,
            "ekf_alt":        ekf_alt,
            "roll":           roll,             "pitch":         pitch,
            "yaw":            yaw,
            "accuracy":       accuracy,
            "sending":        (correction == "Y" or stable_long_enough),
            "correction":     correction,
            "stable_secs":    _stable_secs,
            "last_ack_result": last_ack_result,
            "fake_gps_active": fake_gps_until is not None,
        })

        time.sleep(0.2)
finally:
    starlink_context.close()
