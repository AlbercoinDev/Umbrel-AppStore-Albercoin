#!/bin/sh
set -e

apk add --no-cache socat curl >/dev/null 2>&1

export CONFIG_ENV="${CONFIG_ENV:-/data/config.env}"
export APP_ID="${APP_ID:-albercoin-store-frigate}"
export INIT_SCRIPT="${INIT_SCRIPT:-/init.sh}"

cat > /tmp/handler.sh << 'SCRIPT'
#!/bin/sh
CONFIG_ENV="${CONFIG_ENV:-/data/config.env}"
APP_ID="${APP_ID:-albercoin-store-frigate}"
INIT_SCRIPT="${INIT_SCRIPT:-/init.sh}"

respond() {
    local http_code="$1" body="$2"
    printf 'HTTP/1.1 %s\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: %d\r\n\r\n%s' "$http_code" "${#body}" "$body"
}

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
    [ "$content_length" -gt 0 ] 2>/dev/null || respond "400 Bad Request" '{"status":"error","message":"missing content-length"}'
    [ "$content_length" -gt 0 ] 2>/dev/null || exit 0

    body=$(head -c "$content_length" 2>/dev/null)
    [ -n "$body" ] || respond "400 Bad Request" '{"status":"error","message":"empty body"}'
    [ -n "$body" ] || exit 0

    echo "$body" | sed 's/[{}]//g; s/","/\n/g; s/":"/="/g; s/"//g' > "$CONFIG_ENV"
    chmod 644 "$CONFIG_ENV"

    if [ -f "$INIT_SCRIPT" ]; then
        sh "$INIT_SCRIPT" 2>/dev/null || true
    fi

    if [ -S /var/run/docker.sock ]; then
        CONTAINER="${APP_ID}_server_1"
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
          --unix-socket /var/run/docker.sock \
          "http://localhost/containers/${CONTAINER}/restart" 2>/dev/null || echo "000")
        if [ "$HTTP_CODE" != "204" ]; then
            respond "500 Internal Server Error" '{"status":"error","message":"restart returned HTTP '"$HTTP_CODE"'"}'
            exit 0
        fi
    else
        respond "500 Internal Server Error" '{"status":"error","message":"docker socket not found"}'
        exit 0
    fi

    respond "200 OK" '{"status":"ok","message":"Settings saved, server restarting..."}'
elif [ "$method" = "GET" ] && [ "$path" = "/health" ]; then
    respond "200 OK" '{"status":"healthy"}'
else
    respond "404 Not Found" '{"status":"error","message":"not found"}'
fi
SCRIPT

chmod +x /tmp/handler.sh

exec socat TCP-LISTEN:8081,reuseaddr,fork EXEC:/tmp/handler.sh
