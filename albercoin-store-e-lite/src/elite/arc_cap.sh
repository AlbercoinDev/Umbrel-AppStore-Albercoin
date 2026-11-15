#!/bin/sh
# One-shot host helper: cap the ZFS ARC before e-lite plans its memory.
#
# Umbrel mounts /sys read-only inside containers, so mount a fresh sysfs and
# write there. The cap is 8 GiB on hosts with >= 24 GiB of RAM and a quarter of
# RAM below that, so small Umbrel devices are not starved. This runs on every
# app start/update; it does not modify e-lite.

set -u

path=/sys/module/zfs/parameters/zfs_arc_max
sysroot=/sys

if [ ! -e "$path" ]; then
    mkdir -p /tmp/sysfs
    if mount -t sysfs sysfs /tmp/sysfs 2>/dev/null; then
        sysroot=/tmp/sysfs
        path="$sysroot/module/zfs/parameters/zfs_arc_max"
    fi
fi

if [ ! -e "$path" ]; then
    echo "arc-cap: ZFS ARC parameter not present; nothing to do"
    exit 0
fi

total_kb=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
if [ "${total_kb:-0}" -ge 25165824 ]; then
    cap=8589934592
else
    cap=$((total_kb * 1024 / 4))
fi
if [ "$cap" -lt 1073741824 ]; then
    cap=1073741824
fi

if printf '%s\n' "$cap" > "$path" 2>/dev/null; then
    echo "arc-cap: zfs_arc_max=$cap (total ${total_kb} KiB)"
else
    echo "arc-cap: cannot write $path; ARC cap not applied"
fi

exit 0
