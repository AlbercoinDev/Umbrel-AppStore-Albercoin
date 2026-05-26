#!/bin/sh
set -eu

network="${APP_BITCOIN_NETWORK:-mainnet}"
home_dir="/data/frigate"
config_dir="${home_dir}"
network_args=""

if [ "${network}" != "mainnet" ]; then
  config_dir="${home_dir}/${network}"
  network_args="-n ${network}"
fi

mkdir -p "${config_dir}"

cat > "${config_dir}/config.toml" <<EOF
[core]
connect = true
server = "http://${APP_BITCOIN_NODE_IP}:${APP_BITCOIN_RPC_PORT}"
authType = "USERPASS"
auth = "${APP_BITCOIN_RPC_USER}:${APP_BITCOIN_RPC_PASS}"
zmqSequenceEndpoint = "tcp://${APP_BITCOIN_NODE_IP}:${APP_BITCOIN_ZMQ_SEQUENCE_PORT}"

[index]
cacheSize = "10M"

[scan]
computeBackend = "AUTO"

[server]
tcp = "tcp://0.0.0.0:${APP_FRIGATE_ELECTRUM_PORT}"
backendElectrumServer = "tcp://${APP_ELECTRS_NODE_IP}:${APP_ELECTRS_NODE_PORT}"
EOF

# shellcheck disable=SC2086
exec /opt/frigate/bin/frigate -d "${home_dir}" ${network_args}
