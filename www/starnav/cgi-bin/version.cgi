#!/bin/sh
#
# StarNav Web UI - Git Version API
# Returns current commit hash and whether the remote has newer commits.
# Remote check is cached for 60 s to avoid hammering GitHub on every page load.
#

export PATH=/usr/sbin:/usr/bin:/sbin:/bin

echo "Content-Type: application/json"
echo "Cache-Control: no-cache"
echo ""

INSTALL_DIR="/opt/starnav"
GIT_CACHE="/tmp/starnav_git_remote"
CACHE_TTL=60

# Current local commit
CURRENT_COMMIT=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || echo "")
CURRENT_SHORT=$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")

if [ -z "$CURRENT_COMMIT" ]; then
    printf '{"commit":"unknown","remote_commit":null,"update_available":false,"error":"git unavailable"}\n'
    exit 0
fi

# Remote HEAD check with TTL cache
NOW=$(date +%s)
REMOTE_COMMIT=""
CACHE_VALID=0

if [ -f "$GIT_CACHE" ]; then
    CACHE_TIME=$(sed -n '1p' "$GIT_CACHE" 2>/dev/null)
    CACHE_REMOTE=$(sed -n '2p' "$GIT_CACHE" 2>/dev/null)
    if [ -n "$CACHE_TIME" ] && [ -n "$CACHE_REMOTE" ] && \
       [ $((NOW - CACHE_TIME)) -lt $CACHE_TTL ]; then
        REMOTE_COMMIT="$CACHE_REMOTE"
        CACHE_VALID=1
    fi
fi

if [ "$CACHE_VALID" = "0" ]; then
    # ls-remote is read-only and doesn't modify the local repo
    REMOTE_COMMIT=$(timeout 8 git -C "$INSTALL_DIR" ls-remote origin refs/heads/main 2>/dev/null \
                    | cut -f1 | tr -d '[:space:]')
    if [ -n "$REMOTE_COMMIT" ]; then
        printf '%s\n%s\n' "$NOW" "$REMOTE_COMMIT" > "$GIT_CACHE"
    fi
fi

REMOTE_SHORT=""
UPDATE_AVAILABLE="false"

if [ -n "$REMOTE_COMMIT" ]; then
    REMOTE_SHORT=$(printf '%s' "$REMOTE_COMMIT" | cut -c1-7)
    [ "$REMOTE_COMMIT" != "$CURRENT_COMMIT" ] && UPDATE_AVAILABLE="true"
fi

printf '{"commit":"%s","remote_commit":"%s","update_available":%s}\n' \
    "$CURRENT_SHORT" "${REMOTE_SHORT:-unknown}" "$UPDATE_AVAILABLE"
