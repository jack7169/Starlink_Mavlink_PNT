#!/bin/sh
#
# StarNav Web UI - Command API
# Handles start/stop/restart commands for the starnav service
#

LOCK_FILE="/tmp/starnav-webui.lock"
INIT_SCRIPT="/etc/init.d/starnav"

# Helper: output JSON response
json_response() {
    echo "Content-Type: application/json"
    echo ""
    echo "$1"
}

# Helper: output error response
json_error() {
    json_response "{\"success\": false, \"error\": \"$1\"}"
    exit 0
}

# Helper: escape string for JSON
json_escape() {
    printf '%s' "$1" | awk '
    BEGIN { ORS="" }
    {
        gsub(/\\/, "\\\\")
        gsub(/"/, "\\\"")
        gsub(/\t/, "\\t")
        gsub(/\r/, "")
        if (NR > 1) print "\\n"
        print
    }
    '
}

# Helper: acquire execution lock
acquire_lock() {
    if [ -f "$LOCK_FILE" ]; then
        local pid
        pid=$(cat "$LOCK_FILE" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            json_error "Another command is currently running"
        fi
    fi
    echo $$ > "$LOCK_FILE"
    trap "rm -f '$LOCK_FILE'" EXIT
}

# Execute starnav init script command
run_starnav_command() {
    local cmd="$1"
    local start_time
    start_time=$(date +%s)

    acquire_lock

    if [ ! -x "$INIT_SCRIPT" ]; then
        rm -f "$LOCK_FILE"
        json_error "Init script not found at $INIT_SCRIPT"
    fi

    local output
    local exit_code
    output=$("$INIT_SCRIPT" "$cmd" 2>&1)
    exit_code=$?

    rm -f "$LOCK_FILE"

    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))
    local escaped_output
    escaped_output=$(json_escape "$output")
    local success="false"
    [ $exit_code -eq 0 ] && success="true"

    json_response "{\"success\": $success, \"command\": \"$cmd\", \"output\": \"$escaped_output\", \"exit_code\": $exit_code, \"duration_seconds\": $duration}"
}

# Parse action from request
action=""

# Read POST data
if [ "$REQUEST_METHOD" = "POST" ]; then
    read -r POST_DATA 2>/dev/null || true
    if [ -n "$POST_DATA" ]; then
        # Extract action field from JSON
        action=$(printf '%s' "$POST_DATA" | sed -n 's/.*"action"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    fi
fi

# Also support GET requests with ?action=...
if [ -z "$action" ] && [ "$REQUEST_METHOD" = "GET" ]; then
    action=$(printf '%s' "$QUERY_STRING" | sed -n 's/.*action=\([^&]*\).*/\1/p')
fi

[ -z "$action" ] && json_error "No action specified"

case "$action" in
    start|stop|restart)
        run_starnav_command "$action"
        ;;
    status)
        run_starnav_command "status"
        ;;
    fake_gps)
        touch /tmp/starnav_fakegps_trigger 2>/dev/null
        json_response "{\"success\": true, \"action\": \"fake_gps\"}"
        ;;
    *)
        json_error "Unknown action: $action (valid: start, stop, restart, status, fake_gps)"
        ;;
esac
