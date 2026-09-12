#!/usr/bin/env bash
# Rotating HCI capture, so the next BLE failure leaves evidence.
#
# Every diagnosis in this project has so far been blocked by the same missing artifact:
# nobody has ever captured what happened on the air when a battery went silent. This
# writes a rolling window of HCI traffic and prunes it, so the trace is already there
# when it is needed instead of being started after the fact.
#
# Install:  see tools/README.md
# Cost:     a few MB/hour, one btmon process. It does not connect to anything.
set -euo pipefail

DIR="${BTMON_DIR:-/var/log/btmon}"
KEEP_HOURS="${BTMON_KEEP_HOURS:-168}"   # 7 days
ROTATE_SECONDS="${BTMON_ROTATE_SECONDS:-3600}"

mkdir -p "$DIR"

# Prune first, so a restart loop cannot fill the disk.
find "$DIR" -name 'hci-*.btsnoop' -type f -mmin "+$((KEEP_HOURS * 60))" -delete 2>/dev/null || true

while true; do
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    out="$DIR/hci-$stamp.btsnoop"
    # -w writes btsnoop, readable later with:  btmon -r <file>
    timeout "$ROTATE_SECONDS" btmon -w "$out" >/dev/null 2>&1 || true
    find "$DIR" -name 'hci-*.btsnoop' -type f -mmin "+$((KEEP_HOURS * 60))" -delete 2>/dev/null || true
done
