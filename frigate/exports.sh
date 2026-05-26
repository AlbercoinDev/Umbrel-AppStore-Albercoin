export APP_FRIGATE_IP="10.21.22.40"
export APP_FRIGATE_ELECTRUM_PORT="50005"

electrum_hidden_service_file="${EXPORTS_TOR_DATA_DIR}/app-${EXPORTS_APP_ID}-electrum/hostname"
export APP_FRIGATE_ELECTRUM_HIDDEN_SERVICE="$(cat "${electrum_hidden_service_file}" 2>/dev/null || echo "notyetset.onion")"
