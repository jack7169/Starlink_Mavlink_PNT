#!/bin/sh
# install.sh - Install or update StarNav on OpenWRT
# Usage (first install): scp -O install.sh root@<router>:/tmp/ && ssh root@<router> 'sh /tmp/install.sh'
# Usage (update):        ssh root@<router> '/opt/starnav/install.sh'
# Safe to re-run (idempotent).

set -e

INSTALL_DIR="/opt/starnav"
CONFIG_FILE="/etc/starnav.conf"
INIT_SCRIPT="/etc/init.d/starnav"
REPO_URL="https://github.com/jack7169/Starlink_Mavlink_PNT.git"

echo "=== StarNav OpenWRT Installer ==="
echo ""

# ---- Section 1: System packages ----
echo "=== Installing system packages ==="
opkg update
opkg install git git-http python3 || true
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

# ---- Section 3: Clone or update repo ----
echo ""
echo "=== Installing project files ==="

if [ -d "${INSTALL_DIR}/.git" ]; then
    echo "Existing installation found — pulling latest from GitHub..."
    git -C "$INSTALL_DIR" pull --recurse-submodules
    git -C "$INSTALL_DIR" submodule update --init --recursive
    echo "Update complete."
else
    if [ -d "$INSTALL_DIR" ]; then
        echo "Removing non-git remnants at $INSTALL_DIR..."
        rm -rf "$INSTALL_DIR"
    fi
    echo "Cloning repo to $INSTALL_DIR..."
    git clone --recurse-submodules "$REPO_URL" "$INSTALL_DIR"
    echo "Clone complete."
fi

chmod +x "${INSTALL_DIR}/starnav.sh"
chmod +x "${INSTALL_DIR}/www/starnav/cgi-bin/"*.cgi

# ---- Section 4: Configuration ----
echo ""
echo "=== Installing configuration ==="
if [ -f "$CONFIG_FILE" ]; then
    echo "Config already exists at $CONFIG_FILE -- preserving."
    cp "${INSTALL_DIR}/starnav.conf" "${CONFIG_FILE}.new"
    echo "New defaults saved to ${CONFIG_FILE}.new for reference."
else
    cp "${INSTALL_DIR}/starnav.conf" "$CONFIG_FILE"
    echo "Config installed to $CONFIG_FILE"
fi

# Create CSV log directory from config
CSV_DIR=$(awk -F '=' '/^\[logging\]/{in_s=1} in_s && /^csv_dir/{gsub(/^[ \t]+|[ \t]+$/,"",$2); print $2; exit}' "$CONFIG_FILE")
CSV_DIR="${CSV_DIR:-/root/starlink_logs}"
mkdir -p "$CSV_DIR"
echo "Log directory: $CSV_DIR"

# ---- Section 5: Init script ----
echo ""
echo "=== Installing init script ==="
cp "${INSTALL_DIR}/starnav.init" "$INIT_SCRIPT"
chmod +x "$INIT_SCRIPT"
"$INIT_SCRIPT" enable
echo "Service enabled for auto-start on boot."

# ---- Section 6: Dedicated uhttpd instance on port 8081 ----
echo ""
echo "=== Configuring dedicated web server (port 8081) ==="

# Serve web UI directly from the git repo — no separate copy needed.
uci set uhttpd.starnav=uhttpd
uci set "uhttpd.starnav.home=${INSTALL_DIR}/www/starnav"
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
echo "  Repo / install: $INSTALL_DIR"
echo "  Config file:    $CONFIG_FILE"
echo "  Init script:    $INIT_SCRIPT"
echo "  Web UI:         http://<router-ip>:8081/"
echo ""
echo "  1. Edit $CONFIG_FILE to set your MAVLink endpoint"
echo "  2. Start:   /etc/init.d/starnav start"
echo "  3. Web UI:  http://<router-ip>:8081/"
echo "  4. Logs:    logread -e starnav"
echo "  5. Stop:    /etc/init.d/starnav stop"
echo "  6. Update:  $INSTALL_DIR/install.sh"
