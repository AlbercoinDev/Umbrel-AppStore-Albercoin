#!/bin/sh

# No set -e: handle failures explicitly so the container stays alive.
LOG="/data/api.log"
echo "--- $(date) ---" >> "$LOG"

export CONFIG_ENV="${CONFIG_ENV:-/data/config.env}"
APP_ID="${APP_ID:-albercoin-store-frigate}"
INIT_SCRIPT="${INIT_SCRIPT:-/init.sh}"

# Handler script: runs per-connection via tcpsvd.
# stdin/stdout are connected to the TCP socket.
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

is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

is_simple_size() {
    case "$1" in
        ''|*[!0-9A-Za-z]*) return 1 ;;
        *) return 0 ;;
    esac
}

write_config() {
    tmp="${CONFIG_ENV}.tmp"
    : > "$tmp" || return 1

    seen=0
    old_ifs="$IFS"
    IFS='&'
    for pair in $query; do
        IFS="$old_ifs"
        key=${pair%%=*}
        val=${pair#*=}

        case "$key" in
            FRIGATE_COMPUTE_BACKEND)
                case "$val" in CPU|GPU|AUTO) ;; *) rm -f "$tmp"; return 2 ;; esac
                ;;
            FRIGATE_CACHE_SIZE)
                is_simple_size "$val" || { rm -f "$tmp"; return 2; }
                ;;
            FRIGATE_MEMORY_LIMIT)
                if [ -n "$val" ]; then
                    is_simple_size "$val" || { rm -f "$tmp"; return 2; }
                fi
                ;;
            FRIGATE_DB_THREADS|FRIGATE_BATCH_SIZE|FRIGATE_RPC_REQUEST_TIMEOUT|FRIGATE_RPC_BATCH_SIZE|FRIGATE_START_HEIGHT)
                if [ -n "$val" ]; then
                    is_uint "$val" || { rm -f "$tmp"; return 2; }
                fi
                ;;
            *)
                IFS='&'
                continue
                ;;
        esac

        printf '%s="%s"\n' "$key" "$val" >> "$tmp" || { rm -f "$tmp"; return 1; }
        seen=1
        IFS='&'
    done
    IFS="$old_ifs"

    if [ "$seen" -ne 1 ]; then
        rm -f "$tmp"
        return 2
    fi

    mv "$tmp" "$CONFIG_ENV" || return 1
    chmod 644 "$CONFIG_ENV"
}

# Read raw HTTP request via dd read() syscall.
raw=$(dd bs=8192 count=1 2>/dev/null)
input=$(echo "$raw" | tr -d '\r')

request_line=$(echo "$input" | sed -n '1p')
method=$(echo "$request_line" | cut -d' ' -f1)
path_full=$(echo "$request_line" | cut -d' ' -f2)
path=${path_full%%\?*}
case "$path_full" in
    *\?*) query=${path_full#*\?} ;;
    *) query="" ;;
esac

if [ "$method" = "GET" ] && [ "$path" = "/save" ]; then
    if [ -z "$query" ]; then
        respond "400 Bad Request" '{"status":"error","message":"no params"}'
        exit 0
    fi

    write_config
    rc=$?
    if [ "$rc" -eq 2 ]; then
        respond "400 Bad Request" '{"status":"error","message":"invalid settings"}'
        exit 0
    elif [ "$rc" -ne 0 ]; then
        respond "500 Internal Server Error" '{"status":"error","message":"could not write config"}'
        exit 0
    fi

    if [ -f "$INIT_SCRIPT" ]; then
        sh "$INIT_SCRIPT" 2>>"$LOG" || true
    fi

    respond "200 OK" '{"status":"ok","message":"Settings saved, server restarting..."}'

    # Restart server via Docker socket when socat is available.
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

# Best-effort install socat for Docker restart capability. If this fails,
# settings can still be saved, but the server restart is skipped.
if ! command -v socat >/dev/null 2>&1; then
    apk add --no-cache socat >> "$LOG" 2>&1 || echo "socat install failed" >> "$LOG"
fi

echo "starting tcpsvd on port 8081" >> "$LOG"

# tcpsvd is built into Alpine's Busybox, so the API can start without packages.
# For each connection, runs handler.sh with stdin/stdout as the TCP socket
exec busybox tcpsvd -v 0 8081 /tmp/handler.sh
