#!/bin/sh
set -e

apk add --no-cache socat >/dev/null 2>&1

export CONFIG_ENV="${CONFIG_ENV:-/data/config.env}"
export APP_ID="${APP_ID:-albercoin-store-frigate}"
export INIT_SCRIPT="${INIT_SCRIPT:-/init.sh}"

cat > /tmp/handler.sh << 'SCRIPT'
#!/bin/sh
CONFIG_ENV="${CONFIG_ENV:-/data/config.env}"
APP_ID="${APP_ID:-albercoin-store-frigate}"
INIT_SCRIPT="${INIT_SCRIPT:-/init.sh}"

respond() {
    printf 'HTTP/1.1 %s\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: %d\r\n\r\n%s' "$1" "${#2}" "$2"
}

input=$(cat)
input=$(echo "$input" | tr -d '\r')

request_line=$(echo "$input" | head -n1)
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

    if [ ! -S /var/run/docker.sock ]; then
        respond "500 Internal Server Error" '{"status":"error","message":"docker socket not found"}'
        exit 0
    fi

    CONTAINER="${APP_ID}_server_1"
    HTTP_CODE=$(printf 'POST /containers/%s/restart HTTP/1.0\r\nHost: localhost\r\n\r\n' "$CONTAINER" | socat - UNIX-CONNECT:/var/run/docker.sock 2>/dev/null | head -n1 | awk '{print $2}')

    if [ "$HTTP_CODE" = "204" ]; then
        respond "200 OK" '{"status":"ok","message":"Settings saved, server restarting..."}'
    else
        respond "500 Internal Server Error" '{"status":"error","message":"restart failed (HTTP '"$HTTP_CODE"')"}'
    fi
elif [ "$method" = "GET" ] && [ "$path" = "/health" ]; then
    respond "200 OK" '{"status":"healthy"}'
else
    respond "404 Not Found" '{"status":"error","message":"not found"}'
fi
SCRIPT

chmod +x /tmp/handler.sh

exec socat TCP-LISTEN:8081,reuseaddr,fork EXEC:/tmp/handler.sh
