#!/bin/sh

# No set -e: handle failures explicitly so the container stays alive.
LOG="/data/api.log"
echo "--- $(date) ---" >> "$LOG"

apk add --no-cache socat >> "$LOG" 2>&1 || echo "apk add socat FAILED" >> "$LOG"

export CONFIG_ENV="${CONFIG_ENV:-/data/config.env}"
export APP_ID="${APP_ID:-albercoin-store-frigate}"
export INIT_SCRIPT="${INIT_SCRIPT:-/init.sh}"

cat > /tmp/handler.sh << 'SCRIPT'
#!/bin/sh

CONFIG_ENV="${CONFIG_ENV:-/data/config.env}"
APP_ID="${APP_ID:-albercoin-store-frigate}"
INIT_SCRIPT="${INIT_SCRIPT:-/init.sh}"

respond() {
    # dd writes to fd 1 via write() syscall, bypassing FILE* buffering.
    msg=$(printf 'HTTP/1.1 %s\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: %d\r\n\r\n%s' "$1" "${#2}" "$2")
    echo "$msg" | dd bs=4096 2>/dev/null
}

raw=$(dd bs=8192 count=1 2>/dev/null)
input=$(echo "$raw" | tr -d '\r')

request_line=$(echo "$input" | sed -n '1p')
method=$(echo "$request_line" | cut -d' ' -f1)
path=$(echo "$request_line" | cut -d' ' -f2)

headers=$(echo "$input" | sed '/^$/q')
body=$(echo "$input" | sed '1,/^$/d')

content_length=0
while read -r h; do
    [ -z "$h" ] && continue
    lower=$(echo "$h" | tr 'A-Z' 'a-z')
    case "$lower" in
        content-length:*) content_length=$(echo "$h" | cut -d: -f2 | tr -d ' ') ;;
    esac
done <<HEADERS
$headers
HEADERS

if [ "$method" = "POST" ] && [ "$path" = "/save" ]; then
    if [ -z "$body" ] || [ "$content_length" -eq 0 ] 2>/dev/null; then
        respond "400 Bad Request" '{"status":"error","message":"empty body"}'
        exit 0
    fi

    echo "$body" | sed 's/[{}]//g; s/","/\n/g; s/":"/="/g; s/"//g' > "$CONFIG_ENV"
    chmod 644 "$CONFIG_ENV"

    if [ -f "$INIT_SCRIPT" ]; then
        sh "$INIT_SCRIPT" 2>/dev/null || true
    fi

    # Response is written via dd write() syscall — delivered immediately.
    respond "200 OK" '{"status":"ok","message":"Settings saved, server restarting..."}'

    # Restart server (timeout 15s to accommodate stop_grace_period).
    if [ -S /var/run/docker.sock ]; then
        CONTAINER="${APP_ID}_server_1"
        timeout 15 sh -c 'printf "POST /containers/%s/restart HTTP/1.0\r\nHost: localhost\r\n\r\n" "$1" | socat - UNIX-CONNECT:/var/run/docker.sock' _ "$CONTAINER" >/dev/null 2>&1 || true
    fi

    exit 0
elif [ "$method" = "GET" ] && [ "$path" = "/health" ]; then
    respond "200 OK" '{"status":"healthy"}'
else
    respond "404 Not Found" '{"status":"error","message":"not found"}'
fi
SCRIPT

chmod +x /tmp/handler.sh
echo "handler written" >> "$LOG"

if command -v socat >/dev/null 2>&1; then
    echo "starting socat" >> "$LOG"
    exec socat TCP-LISTEN:8081,reuseaddr,fork EXEC:/tmp/handler.sh
fi

echo "FATAL: socat not available" >> "$LOG"
sleep infinity
