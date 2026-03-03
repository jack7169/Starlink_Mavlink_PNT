#!/bin/sh
# install.sh - Install StarNav on OpenWRT
# Usage: scp this project directory to the router, then run this script from within it.
# Safe to re-run (idempotent).

set -e

INSTALL_DIR="/opt/starnav"
CONFIG_FILE="/etc/starnav.conf"
INIT_SCRIPT="/etc/init.d/starnav"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== StarNav OpenWRT Installer ==="
echo "Installing from: $SCRIPT_DIR"
echo ""

# ---- Section 1: System packages ----
echo "=== Installing system packages ==="
opkg update
opkg install python3 || true
opkg install ntpd || opkg install sntpd || true
echo "NTP client installed (no RTC on this board -- NTP required for wall-clock time)."

# Bootstrap pip if not available
if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "pip not found, attempting bootstrap..."

    # Try ensurepip first
    if python3 -m ensurepip --default-pip 2>/dev/null; then
        echo "pip bootstrapped via ensurepip"
    else
        echo "ensurepip unavailable, downloading get-pip.py..."
        wget -O /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py
        python3 /tmp/get-pip.py --no-cache-dir
        rm -f /tmp/get-pip.py
    fi
fi

echo "pip version: $(python3 -m pip --version)"

# ---- Section 2: Python dependencies ----
echo ""
echo "=== Installing Python dependencies ==="
echo "Note: grpcio may compile from source on ARM. This can take 10-30 minutes."

# Only the packages starnav.py actually needs.
# --no-cache-dir saves flash storage.
python3 -m pip install --no-cache-dir \
    grpcio \
    protobuf \
    yagrc \
    typing-extensions \
    pymavlink

# Verify critical imports
echo "Verifying Python dependencies..."
python3 -c "import grpc; import google.protobuf; import yagrc; from pymavlink import mavutil; print('All dependencies OK')"

# ---- Section 3: Project files ----
echo ""
echo "=== Installing project files ==="
mkdir -p "$INSTALL_DIR"
mkdir -p "${INSTALL_DIR}/starlink-grpc-tools"

# Copy main files
cp "${SCRIPT_DIR}/starnav.py" "$INSTALL_DIR/"
cp "${SCRIPT_DIR}/starnav.sh" "$INSTALL_DIR/"
chmod +x "${INSTALL_DIR}/starnav.sh"

# Copy only the starlink-grpc-tools files we actually need
cp "${SCRIPT_DIR}/starlink-grpc-tools/starlink_grpc.py" "${INSTALL_DIR}/starlink-grpc-tools/"
cp "${SCRIPT_DIR}/starlink-grpc-tools/dish_control.py" "${INSTALL_DIR}/starlink-grpc-tools/"
cp "${SCRIPT_DIR}/starlink-grpc-tools/loop_util.py" "${INSTALL_DIR}/starlink-grpc-tools/"

# ---- Section 4: Configuration ----
echo ""
echo "=== Installing configuration ==="
if [ -f "$CONFIG_FILE" ]; then
    echo "Config already exists at $CONFIG_FILE -- preserving."
    cp "${SCRIPT_DIR}/starnav.conf" "${CONFIG_FILE}.new"
    echo "New defaults saved to ${CONFIG_FILE}.new for reference."
else
    cp "${SCRIPT_DIR}/starnav.conf" "$CONFIG_FILE"
    echo "Config installed to $CONFIG_FILE"
fi

# ---- Section 5: Init script ----
echo ""
echo "=== Installing init script ==="
cp "${SCRIPT_DIR}/starnav.init" "$INIT_SCRIPT"
chmod +x "$INIT_SCRIPT"
"$INIT_SCRIPT" enable
echo "Service enabled for auto-start on boot."

# ---- Section 6: Web UI files ----
echo ""
echo "=== Installing web UI ==="
WEB_DIR="/www/starnav"
mkdir -p "${WEB_DIR}/cgi-bin"

cp "${SCRIPT_DIR}/www/starnav/index.html" "${WEB_DIR}/"
cp "${SCRIPT_DIR}/www/starnav/cgi-bin/status.cgi" "${WEB_DIR}/cgi-bin/"
cp "${SCRIPT_DIR}/www/starnav/cgi-bin/logs.cgi"   "${WEB_DIR}/cgi-bin/"
cp "${SCRIPT_DIR}/www/starnav/cgi-bin/api.cgi"    "${WEB_DIR}/cgi-bin/"
chmod +x "${WEB_DIR}/cgi-bin/"*.cgi

echo "Web UI files installed to $WEB_DIR"

# ---- Section 7: Dedicated uhttpd instance on port 8081 ----
echo ""
echo "=== Configuring dedicated web server (port 8081) ==="

# Add/update a separate uhttpd UCI config block named 'starnav'.
# This runs alongside the existing uhttpd instance and owns only port 8081.
uci set uhttpd.starnav=uhttpd
uci set uhttpd.starnav.home='/www/starnav'
uci set uhttpd.starnav.cgi_prefix='/cgi-bin'
uci set uhttpd.starnav.script_timeout='60'
uci set uhttpd.starnav.network_timeout='30'
uci set uhttpd.starnav.max_requests='5'
uci set uhttpd.starnav.tcp_keepalive='1'
# Clear any existing listen list before (re-)adding to stay idempotent
uci -q delete uhttpd.starnav.listen_http || true
uci add_list uhttpd.starnav.listen_http='0.0.0.0:8081'
uci add_list uhttpd.starnav.listen_http='[::]:8081'
uci commit uhttpd

/etc/init.d/uhttpd restart
echo "uhttpd restarted — StarNav UI now on port 8081."

# ---- Done ----
echo ""
echo "=== Installation complete ==="
echo ""
echo "  Config file:  $CONFIG_FILE"
echo "  Install dir:  $INSTALL_DIR"
echo "  Init script:  $INIT_SCRIPT"
echo "  Web UI:       http://<router-ip>:8081/"
echo ""
echo "  1. Edit $CONFIG_FILE to set your MAVLink endpoint"
echo "  2. Start:   /etc/init.d/starnav start"
echo "  3. Web UI:  http://<router-ip>:8081/"
echo "  4. Logs:    logread -e starnav"
echo "  5. Stop:    /etc/init.d/starnav stop"
