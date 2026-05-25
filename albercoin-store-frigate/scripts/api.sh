#!/bin/sh

# No set -e: handle failures explicitly so the container stays alive.
LOG="/data/api.log"
echo "--- $(date) ---" >> "$LOG"

export CONFIG_ENV="${CONFIG_ENV:-/data/config.env}"
APP_ID="${APP_ID:-albercoin-store-frigate}"
INIT_SCRIPT="${INIT_SCRIPT:-/init.sh}"

# Handler script — runs per-connection via tcpsvd
# stdin/stdout = TCP socket directly (no FILE* buffering concerns for dd reads)
cat > /tmp/handler.sh << 'SCRIPT'
#!/bin/sh

CONFIG_ENV="${CONFIG_ENV:-/data/config.env}"
APP_ID="${APP_ID:-albercoin-store-frigate}"
INIT_SCRIPT="${INIT_SCRIPT:-/init.sh}"
LOG="/data/api.log"

respond() {
    msg=$(printf 'HTTP/1.1 %s\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: %d\r\n\r\n%s' "$1" "${#2}" "$2")
    printf '%s\r\n' "$msg"
}

# Read raw HTTP request via dd read() syscall — no FILE buffering
raw=$(dd bs=8192 count=1 2>/dev/null)
input=$(echo "$raw" | tr -d '\r')

request_line=$(echo "$input" | sed -n '1p')
method=$(echo "$request_line" | cut -d' ' -f1)
path_full=$(echo "$request_line" | cut -d' ' -f2)
path=$(echo "$path_full" | cut -d? -f1)
query=$(echo "$path_full" | cut -d? -f2-)

if [ "$method" = "GET" ] && [ "$path" = "/save" ]; then
    if [ -z "$query" ]; then
        respond "400 Bad Request" '{"status":"error","message":"no params"}'
        exit 0
    fi

    echo "$query" | tr '&' '\n' | sed 's/=/="/; s/$/"/' > "$CONFIG_ENV"
    chmod 644 "$CONFIG_ENV"

    if [ -f "$INIT_SCRIPT" ]; then
        sh "$INIT_SCRIPT" 2>>"$LOG" || true
    fi

    respond "200 OK" '{"status":"ok","message":"Settings saved, server restarting..."}'

    # Restart server via Docker socket (if socat was installed by background job)
    if [ -S /var/run/docker.sock ] && command -v socat >/dev/null 2>&1; then
        (
            sleep 1
            CONTAINER="${APP_ID}_server_1"
            timeout 15 sh -c 'printf "POST /containers/%s/restart HTTP/1.0\r\nHost: localhost\r\n\r\n" "$1" | socat - UNIX-CONNECT:/var/run/docker.sock' _ "$CONTAINER" >/dev/null 2>&1 || echo "restart failed" >> "$LOG"
        ) &
    fi

    exit 0
elif [ "$method" = "GET" ] && [ "$path" = "/health" ]; then
    respond "200 OK" '{"status":"healthy"}'
else
    respond "404 Not Found" '{"status":"error","message":"not found"}'
fi
SCRIPT
chmod +x /tmp/handler.sh

# Best-effort install socat for Docker restart capability
# Runs in background so it doesn't block startup
apk add --no-cache socat >> "$LOG" 2>&1 &

echo "starting tcpsvd on port 8081" >> "$LOG"

# tcpsvd is built into Alpine's Busybox — always available, no packages needed
# For each connection, runs handler.sh with stdin/stdout as the TCP socket
exec busybox tcpsvd -vE 0 8081 /tmp/handler.sh
