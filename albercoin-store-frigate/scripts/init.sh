#!/bin/sh
set -e

mkdir -p /data
touch /data/frigate.log
chmod 644 /data/frigate.log
chmod 755 /data
chown -R 1000:1000 /data

if [ -f /data/config.env ]; then
    . /data/config.env
fi

NETWORK="${APP_BITCOIN_NETWORK:-mainnet}"
COMPUTE_BACKEND="${FRIGATE_COMPUTE_BACKEND:-CPU}"
CACHE_SIZE="${FRIGATE_CACHE_SIZE:-10M}"
MEMORY_LIMIT="${FRIGATE_MEMORY_LIMIT:-}"
DB_THREADS="${FRIGATE_DB_THREADS:-}"
BATCH_SIZE="${FRIGATE_BATCH_SIZE:-300000}"
RPC_TIMEOUT="${FRIGATE_RPC_REQUEST_TIMEOUT:-60}"
RPC_BATCH="${FRIGATE_RPC_BATCH_SIZE:-100}"
START_HEIGHT="${FRIGATE_START_HEIGHT:-}"
BITCOIN_IP="${APP_BITCOIN_NODE_IP:-127.0.0.1}"
BITCOIN_RPC="${APP_BITCOIN_RPC_PORT:-8332}"
ZMQ_PORT="${APP_BITCOIN_ZMQ_SEQUENCE_PORT:-28336}"
ELECTRS_IP="${APP_ELECTRS_NODE_IP:-127.0.0.1}"
ELECTRS_PORT="${APP_ELECTRS_NODE_PORT:-50001}"

cat > /data/config.toml << EOF
[core]
connect = true
server = "http://${BITCOIN_IP}:${BITCOIN_RPC}"
authType = "COOKIE"
dataDir = "/data/.bitcoin"
zmqSequenceEndpoint = "tcp://${BITCOIN_IP}:${ZMQ_PORT}"
rpcRequestTimeoutSeconds = ${RPC_TIMEOUT}
rpcBatchSize = ${RPC_BATCH}

[index]
cacheSize = "${CACHE_SIZE}"
EOF

if [ -n "$START_HEIGHT" ]; then
    echo "startHeight = ${START_HEIGHT}" >> /data/config.toml
fi

cat >> /data/config.toml << EOF

[scan]
computeBackend = "${COMPUTE_BACKEND}"
batchSize = ${BATCH_SIZE}
EOF

if [ -n "$DB_THREADS" ]; then
    echo "dbThreads = ${DB_THREADS}" >> /data/config.toml
fi

if [ -n "$MEMORY_LIMIT" ]; then
    echo "memoryLimit = \"${MEMORY_LIMIT}\"" >> /data/config.toml
fi

cat >> /data/config.toml << EOF

[server]
tcp = "tcp://0.0.0.0:57001"
backendElectrumServer = "tcp://${ELECTRS_IP}:${ELECTRS_PORT}"
EOF

cat > /data/config.json << JSONEOF
{
  "FRIGATE_COMPUTE_BACKEND": "${COMPUTE_BACKEND}",
  "FRIGATE_CACHE_SIZE": "${CACHE_SIZE}",
  "FRIGATE_MEMORY_LIMIT": "${MEMORY_LIMIT}",
  "FRIGATE_DB_THREADS": "${DB_THREADS}",
  "FRIGATE_BATCH_SIZE": "${BATCH_SIZE}",
  "FRIGATE_RPC_REQUEST_TIMEOUT": "${RPC_TIMEOUT}",
  "FRIGATE_RPC_BATCH_SIZE": "${RPC_BATCH}",
  "FRIGATE_START_HEIGHT": "${START_HEIGHT}",
  "APP_BITCOIN_NETWORK": "${NETWORK}"
}
JSONEOF

echo "Frigate config generated: computeBackend=${COMPUTE_BACKEND} cacheSize=${CACHE_SIZE}"
