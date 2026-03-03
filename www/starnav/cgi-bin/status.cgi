#!/bin/sh
#
# StarNav Web UI - Status API
# Returns JSON with starnav process state and live position data
#

echo "Content-Type: application/json"
echo "Cache-Control: no-cache"
echo ""

STATUS_FILE="/tmp/starnav_status.json"
PID_FILE="/var/run/starnav.pid"

# Check if starnav is running
PROCESS_RUNNING="false"
STARNAV_PID="null"

# Try pid file first
if [ -f "$PID_FILE" ]; then
    pid=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        PROCESS_RUNNING="true"
        STARNAV_PID=$pid
    fi
fi

# Fallback: scan process list
if [ "$PROCESS_RUNNING" = "false" ]; then
    pid=$(pgrep -f starnav.py 2>/dev/null | head -1)
    if [ -n "$pid" ]; then
        PROCESS_RUNNING="true"
        STARNAV_PID=$pid
    fi
fi

# Read position data from status file if it exists
DATA_AGE="null"
POSITION_DATA="null"

if [ -f "$STATUS_FILE" ]; then
    # Calculate age of status file in seconds
    NOW=$(date +%s)
    # BusyBox: use 'date -r' or 'stat -c %Y'
    STATUS_MTIME=$(date -r "$STATUS_FILE" +%s 2>/dev/null || \
                   stat -c %Y "$STATUS_FILE" 2>/dev/null || \
                   echo 0)
    DATA_AGE=$((NOW - STATUS_MTIME))
    POSITION_DATA=$(cat "$STATUS_FILE" 2>/dev/null)
    [ -z "$POSITION_DATA" ] && POSITION_DATA="null"
fi

# Output combined JSON response
printf '{"process_running": %s, "pid": %s, "data_age_seconds": %s, "position": %s}\n' \
    "$PROCESS_RUNNING" "$STARNAV_PID" "$DATA_AGE" "$POSITION_DATA"
