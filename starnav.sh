#!/bin/sh

# starnav.sh - Wrapper script for StarNav position forwarding
# Reads configuration from starnav.conf, disables dish GPS, launches starnav.py

# Default config path (overridable via environment)
CONFIG_FILE="${STARNAV_CONFIG:-/etc/starnav.conf}"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Config file not found: $CONFIG_FILE"
    exit 1
fi

# Parse a value from the INI config file
# Usage: get_config <section> <key> <default>
get_config() {
    section="$1"
    key="$2"
    default="$3"
    value=$(awk -F '=' -v section="$section" -v key="$key" '
        /^\[/ {
            gsub(/\[/, "")
            gsub(/\]/, "")
            gsub(/^[ \t]+/, "")
            gsub(/[ \t]+$/, "")
            current = $0
        }
        current == section {
            k = $1
            gsub(/^[ \t]+/, "", k)
            gsub(/[ \t]+$/, "", k)
            if (k == key) {
                sub(/^[^=]*=/, "")
                gsub(/^[ \t]+/, "")
                gsub(/[ \t]*#.*$/, "")
                gsub(/[ \t]+$/, "")
                print
                exit
            }
        }
    ' "$CONFIG_FILE")
    echo "${value:-$default}"
}

INSTALL_DIR=$(get_config "paths" "install_dir" "/opt/starnav")
GRPC_TOOLS_DIR=$(get_config "paths" "grpc_tools_dir" "starlink-grpc-tools")
DISH_ADDRESS=$(get_config "starlink" "dish_address" "192.168.100.1:9200")
GPS_MODE=$(get_config "starlink" "gps_mode" "auto")

GRPC_TOOLS_PATH="${INSTALL_DIR}/${GRPC_TOOLS_DIR}"

# Add starlink-grpc-tools to Python module path
export PYTHONPATH="${GRPC_TOOLS_PATH}:${PYTHONPATH:-}"

cd "$GRPC_TOOLS_PATH" || exit 1

# Attempt NTP sync (no RTC on this board). Non-blocking -- continue regardless.
echo "Attempting NTP time sync..."
ntpd -n -q -p pool.ntp.org -p time.google.com >/dev/null 2>&1 &
NTP_PID=$!
sleep 5
if kill -0 "$NTP_PID" 2>/dev/null; then
    kill "$NTP_PID" 2>/dev/null
    echo "NTP sync timed out -- continuing with unsynchronized clock."
else
    echo "NTP sync complete."
fi

case "$GPS_MODE" in
    disable)
        echo "Disabling GPS on dish at ${DISH_ADDRESS}..."
        python3 dish_control.py --target "$DISH_ADDRESS" set_gps --no-enable
        if [ $? -ne 0 ]; then
            echo "dish_control failed. Exiting."
            exit 1
        fi
        ;;
    enable)
        echo "Enabling GPS on dish at ${DISH_ADDRESS}..."
        python3 dish_control.py --target "$DISH_ADDRESS" set_gps --enable
        if [ $? -ne 0 ]; then
            echo "dish_control failed. Exiting."
            exit 1
        fi
        ;;
    *)
        echo "GPS mode: auto (keeping current dish state)"
        ;;
esac

echo "Starting starnav..."
cd "$INSTALL_DIR" || exit 1
exec python3 -u starnav.py --config "$CONFIG_FILE"
