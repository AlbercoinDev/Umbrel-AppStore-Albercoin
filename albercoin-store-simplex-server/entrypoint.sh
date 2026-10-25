#!/bin/sh
set -eu
umask 077
mkdir -p /data /etc/opt/simplex /var/opt/simplex
chown -R 1000:1000 /data /etc/opt/simplex /var/opt/simplex
exec gosu 1000:1000 python3 /app/server.py
