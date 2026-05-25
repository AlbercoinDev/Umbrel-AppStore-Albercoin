#!/bin/sh
set -e

apk add --no-cache socat curl >/dev/null 2>&1

export CONFIG_ENV="${CONFIG_ENV:-/data/config.env}"
export APP_ID="${APP_ID:-albercoin-store-frigate}"

cat > /tmp/handler.sh << 'SCRIPT'
#!/bin/sh
CONFIG_ENV="${CONFIG_ENV:-/data/config.env}"
APP_ID="${APP_ID:-albercoin-store-frigate}"
INIT_SCRIPT="${INIT_SCRIPT:-/init.sh}"

read -r request_line
method=$(echo "$request_line" | cut -d' ' -f1)
path=$(echo "$request_line" | cut -d' ' -f2)

content_length=0
while read -r header; do
    header=$(echo "$header" | tr -d '\r')
    [ -z "$header" ] && break
    lower=$(echo "$header" | tr 'A-Z' 'a-z')
    case "$lower" in
        content-length:*) content_length=$(echo "$header" | cut -d: -f2 | tr -d ' ') ;;
    esac
done

if [ "$method" = "POST" ] && [ "$path" = "/save" ]; then
    body=$(dd bs=1 count="$content_length" 2>/dev/null)

    echo "$body" | sed 's/[{}]//g; s/","/\n/g; s/":"/="/g; s/"//g' > "$CONFIG_ENV"
    chmod 644 "$CONFIG_ENV"

    sh "$INIT_SCRIPT" 2>/dev/null || true

    SERVER_CONTAINER="${APP_ID}_server_1"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
      --unix-socket /var/run/docker.sock \
      "http://localhost/containers/${SERVER_CONTAINER}/restart" 2>/dev/null || echo "000")

    if [ "$HTTP_CODE" = "204" ]; then
        printf 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n\r\n{"status":"ok","message":"Settings saved, server restarting..."}\r\n'
    else
        printf 'HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n\r\n{"status":"error","message":"Failed to restart server (HTTP %s)"}\r\n' "$HTTP_CODE"
    fi
elif [ "$method" = "GET" ] && [ "$path" = "/health" ]; then
    printf 'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{"status":"healthy"}\r\n'
else
    printf 'HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\n\r\n{"error":"not found"}\r\n'
fi
SCRIPT

chmod +x /tmp/handler.sh

exec socat TCP-LISTEN:8081,reuseaddr,fork EXEC:/tmp/handler.sh
