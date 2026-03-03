#!/bin/sh
#
# StarNav Web UI - Log Streaming API (Server-Sent Events)
# Streams starnav process logs in real-time
#

# SSE headers
echo "Content-Type: text/event-stream"
echo "Cache-Control: no-cache"
echo "Connection: keep-alive"
echo "X-Accel-Buffering: no"
echo ""

# Helper: escape string for JSON
json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/	/\\t/g; s/\r//g'
}

# Helper: send SSE event
send_event() {
    local event_type="$1"
    local data="$2"
    echo "event: $event_type"
    echo "data: $data"
    echo ""
}

# Helper: classify log level from message content
log_level() {
    local msg="$1"
    case "$msg" in
        *ERROR*|*error*|*FAILED*|*failed*|*Exception*|*Traceback*) echo "error" ;;
        *WARN*|*warn*|*WARNING*|*warning*) echo "warn" ;;
        *">>> Sending"*|*"ACK Received"*) echo "send" ;;
        *DEBUG*|*debug*) echo "debug" ;;
        *) echo "info" ;;
    esac
}

# Helper: send log line as SSE
send_log() {
    local message="$1"
    local source="$2"
    local timestamp
    timestamp=$(date '+%Y-%m-%dT%H:%M:%S')
    local escaped_msg
    escaped_msg=$(json_escape "$message")
    local level
    level=$(log_level "$message")

    echo "data: {\"timestamp\": \"$timestamp\", \"level\": \"$level\", \"source\": \"$source\", \"message\": \"$escaped_msg\"}"
    echo ""
}

# Send initial connection event
send_event "connected" "{\"message\": \"Log stream connected\", \"timestamp\": \"$(date '+%Y-%m-%dT%H:%M:%S')\"}"

# Send recent log history from system log
send_event "history_start" "{\"message\": \"Sending recent log history\"}"

logread 2>/dev/null | grep -i starnav | tail -50 | while IFS= read -r line; do
    [ -n "$line" ] && send_log "$line" "starnav"
done

send_event "history_end" "{\"message\": \"Log history complete\"}"

# Cleanup function
cleanup() {
    for pid in $(jobs -p 2>/dev/null); do
        kill "$pid" 2>/dev/null
    done
    rm -f "$LOG_FIFO"
    exit 0
}

trap cleanup EXIT INT TERM

# Create FIFO for log aggregation
LOG_FIFO="/tmp/starnav-logs-$$.fifo"
mkfifo "$LOG_FIFO" 2>/dev/null || true

# Start background log tailer - stream system logs filtered for starnav
(
    logread -f 2>/dev/null | grep --line-buffered -i starnav 2>/dev/null | while IFS= read -r line; do
        echo "starnav|$line"
    done
) > "$LOG_FIFO" 2>/dev/null &

TAILER_PID=$!

# Read from FIFO and send as SSE
while IFS='|' read -r source line <&3; do
    [ -n "$line" ] && send_log "$line" "$source"
done 3< "$LOG_FIFO" &

READER_PID=$!

# Keep connection alive with heartbeat
while true; do
    echo ": heartbeat" 2>/dev/null || break
    echo ""
    kill -0 $TAILER_PID 2>/dev/null || break
    sleep 15
done

# Cleanup
rm -f "$LOG_FIFO"
kill $TAILER_PID $READER_PID 2>/dev/null
