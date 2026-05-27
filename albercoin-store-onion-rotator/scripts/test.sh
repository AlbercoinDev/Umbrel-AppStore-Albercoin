#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Onion Rotator - Tests ==="

cd "$APP_DIR"

echo "Installing dependencies..."
pip install -r requirements.txt pytest -q

echo "Running tests..."
PYTHONPATH="${APP_DIR}/src:${PYTHONPATH:-}" python -m pytest tests/ -v --tb=short "$@"
