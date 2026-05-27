#!/bin/bash
set -euo pipefail

# Publish script for Onion Rotator
# Requires: GH_TOKEN environment variable set, or gh CLI authenticated
#
# Usage:
#   export GH_TOKEN=ghp_xxx
#   ./scripts/publish.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
REPO="AlbercoinDev/Umbrel-AppStore-Albercoin"

echo "=== Publish to GitHub ==="

if [ -z "${GH_TOKEN:-}" ] && ! gh auth status &>/dev/null; then
    echo "Error: GH_TOKEN not set and gh CLI not authenticated."
    echo "Set GH_TOKEN environment variable or run 'gh auth login' first."
    exit 1
fi

cd "$APP_DIR"

# Ensure we're in the right repo
if [ "$(git remote get-url origin 2>/dev/null)" != "https://github.com/$REPO.git" ]; then
    echo "Warning: This doesn't appear to be the $REPO repository."
    echo "Remote: $(git remote get-url origin 2>/dev/null || echo 'none')"
fi

echo "Checking for uncommitted changes..."
if [ -n "$(git status --porcelain)" ]; then
    echo "There are uncommitted changes. Commit first or stash them."
    echo
    echo "To commit:"
    echo "  git add albercoin-store-onion-rotator/"
    echo "  git commit -m \"feat: add onion-rotator app\""
    exit 1
fi

echo "Pushing to origin..."
git push origin main

echo "Done! Store URL for Umbrel:"
echo "  https://github.com/$REPO"
echo
echo "To add this store in Umbrel:"
echo "  sudo ~/umbrel/scripts/repo add https://github.com/$REPO"
echo "  sudo ~/umbrel/scripts/repo update"
