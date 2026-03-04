#!/bin/sh
#
# StarNav Web UI - Log Streaming API (Server-Sent Events)
# Streams starnav process logs in real-time via a direct pipeline.
#

# SSE headers
printf 'Content-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\nX-Accel-Buffering: no\r\n\r\n'

# Helper: escape string for JSON
json_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/	/\\t/g; s/\r//g'
}

# Helper: send a named SSE event
send_event() {
    printf 'event: %s\ndata: %s\n\n' "$1" "$2"
}

# Helper: classify log level from message content
log_level() {
    case "$1" in
        *ERROR*|*error*|*FAILED*|*failed*|*Exception*|*Traceback*) printf 'error' ;;
        *WARN*|*warn*|*WARNING*|*warning*)                          printf 'warn'  ;;
        *">>> Sending"*|*"ACK Received"*)                          printf 'send'  ;;
        *DEBUG*|*debug*)                                            printf 'debug' ;;
        *)                                                          printf 'info'  ;;
    esac
}

# Helper: emit a log line as an SSE data event
send_log() {
    local msg="$1" src="${2:-starnav}"
    local ts level esc
    ts=$(date '+%Y-%m-%dT%H:%M:%S')
    level=$(log_level "$msg")
    esc=$(json_escape "$msg")
    printf 'data: {"timestamp":"%s","level":"%s","source":"%s","message":"%s"}\n\n' \
        "$ts" "$level" "$src" "$esc"
}

# ── Connection + history ─────────────────────────────────
send_event "connected" "{\"message\":\"Log stream connected\",\"timestamp\":\"$(date '+%Y-%m-%dT%H:%M:%S')\"}"

send_event "history_start" "{\"message\":\"Sending recent log history\"}"
logread 2>/dev/null | grep -i starnav | tail -50 | while IFS= read -r line; do
    [ -n "$line" ] && send_log "$line" "starnav"
done
send_event "history_end" "{\"message\":\"Log history complete\"}"

# ── Live stream ──────────────────────────────────────────
# Direct pipeline: logread -f -> grep -> send_log.
# No FIFO or background processes — output goes straight to the SSE response.
# The browser EventSource reconnects automatically if the connection drops.
cleanup() { kill "$TAIL_PID" 2>/dev/null; exit 0; }
trap cleanup EXIT INT TERM

logread -f 2>/dev/null | grep --line-buffered -i starnav | while IFS= read -r line; do
    [ -n "$line" ] && send_log "$line" "starnav" || true
done &
TAIL_PID=$!

# Heartbeat loop — keeps the TCP connection alive during quiet periods
while kill -0 "$TAIL_PID" 2>/dev/null; do
    printf ': heartbeat\n\n'
    sleep 15
done
