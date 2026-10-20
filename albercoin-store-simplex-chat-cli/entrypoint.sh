#!/bin/sh
set -eu
umask 077
mkdir -p /data/simplex /data/manager /data/backups
# Limit ownership changes to this app's volume. Never mount another app here.
chown -R 1000:1000 /data
exec gosu simplex /opt/venv/bin/python -m src.main
