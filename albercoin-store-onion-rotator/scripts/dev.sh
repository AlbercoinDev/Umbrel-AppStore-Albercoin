#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Onion Rotator - Dev Environment ==="

cd "$APP_DIR"

echo "Installing dependencies..."
pip install -r requirements.txt -q

echo "Starting dev server with hot reload..."
export ONION_ROTATOR_DRY_RUN="${ONION_ROTATOR_DRY_RUN:-true}"
export ONION_ROTATOR_DEBUG="${ONION_ROTATOR_DEBUG:-true}"
export UMBREL_ROOT="${UMBREL_ROOT:-/home/umbrel/umbrel}"

# Create a mock tor data dir for testing if it doesn't exist
MOCK_TOR_DIR="/tmp/onion-rotator-dev-tor"
if [ ! -d "$MOCK_TOR_DIR" ]; then
    mkdir -p "$MOCK_TOR_DIR"
    for app in bitcoin electrs lnd cln; do
        mkdir -p "$MOCK_TOR_DIR/app-$app"
        echo "${app}${app}${app}${app}${app}${app}${app}${app}${app}${app}${app}${app}${app}${app}.onion" > "$MOCK_TOR_DIR/app-$app/hostname"
    done
    echo "Created mock tor data at $MOCK_TOR_DIR"
fi

export TOR_DATA_DIR="$MOCK_TOR_DIR"

uvicorn src.main:app --host 0.0.0.0 --port 8900 --reload --log-level debug
