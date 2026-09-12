#!/usr/bin/env python3
"""
Bluetooth stack health report for the BMS dashboard host.

Packages the journal queries used to diagnose the 2026-08 ECO-WORTHY failure so they
can be run in one command instead of reconstructed from scratch. Run it weekly during a
soak; its output is the acceptance criterion for whether the stack fixes took.

    python3 tools/bt_health.py                 # last 7 days
    python3 tools/bt_health.py --days 30
    python3 tools/bt_health.py --since "2026-08-19" --until "2026-08-22"

What each section means, and what "good" looks like:

  bluetoothd crashes    Should be ZERO. Any is a host-stack bug, not a battery problem.
                        BlueZ 5.85 before 5.85-4ubuntu0.2 has a known use-after-free in
                        GATT client teardown that fires when a device disconnects.
  adapter recoveries    Should be rare (≤1/week). Every one power-cycles the radio and
                        can strand a peripheral that had a link open.
  whole-bank outages    Minutes where every battery went missing at once. This is the
                        adapter wedging, and it is the event that killed the ECO-WORTHY.
  kernel wedge lines    Controller/host state desync. Dozens per day means the adapter
                        is struggling; near zero is healthy.
  per-battery failures  One battery far worse than the others is that battery or its
                        position, not the adapter.

Read-only: runs journalctl and nothing else.
"""

import argparse
import collections
import re
import subprocess
import sys

SERVICE = "bms-dashboard"

# Kernel lines that indicate controller/host state desync rather than a peer problem.
# 0x200e = LE_CREATE_CONN_CANCEL, 0x2005 = LE_SET_RANDOM_ADDR, -16 = -EBUSY.
WEDGE_PATTERNS = (
    "command tx timeout",
    "hardware error",
    "Opcode 0x",
    "ACL packet for unknown connection handle",
)

BATTERY_LINE = re.compile(r"\[([a-zA-Z0-9_]+)\] (read failed|not advertising|not found in scan|held connection failed)")
TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})")


def journal(args, unit=None, kernel=False, extra=None):
    cmd = ["journalctl", "--no-pager", "-q", "-o", "short-iso"]
    if kernel:
        cmd += ["-k", "-b", "all"]
    if unit:
        cmd += ["-u", unit]
    if args.since:
        cmd += ["--since", args.since]
    if args.until:
        cmd += ["--until", args.until]
    if not args.since and not args.until:
        cmd += ["--since", f"{args.days} days ago"]
    cmd += extra or []
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    except Exception as exc:  # noqa: BLE001
        print(f"  (journalctl failed: {type(exc).__name__}: {exc})")
        return []
    if out.returncode != 0 and not out.stdout:
        print(f"  (journalctl exited {out.returncode}: {out.stderr.strip()[:200]})")
        return []
    return out.stdout.splitlines()


def by_day(lines):
    counts = collections.Counter()
    for line in lines:
        m = TIMESTAMP.match(line)
        if m:
            counts[m.group(1)] += 1
    return counts


def show_counts(title, counts, good="0", note=""):
    print(f"\n{title}")
    if note:
        print(f"  {note}")
    if not counts:
        print(f"  none — good (expected: {good})")
        return
    for day in sorted(counts):
        print(f"  {day}  {counts[day]:>5}")
    print(f"  total {sum(counts.values())}")


def main():
    ap = argparse.ArgumentParser(description="Bluetooth stack health report")
    ap.add_argument("--days", type=int, default=7, help="how far back to look (default 7)")
    ap.add_argument("--since", help="journalctl --since value (overrides --days)")
    ap.add_argument("--until", help="journalctl --until value")
    ap.add_argument("--unit", default=SERVICE, help=f"systemd unit (default {SERVICE})")
    args = ap.parse_args()

    window = args.since or f"{args.days} days ago"
    print(f"Bluetooth health — unit {args.unit}, since {window}"
          + (f", until {args.until}" if args.until else ""))
    print("=" * 72)

    # 1. bluetoothd crashes.
    crashes = [l for l in journal(args, kernel=True) if "bluetoothd[" in l and "segfault" in l]
    coredumps = [
        l for l in journal(args, unit="bluetooth")
        if "Main process exited" in l or "core-dump" in l
    ]
    show_counts("bluetoothd segfaults (kernel)", by_day(crashes),
                note="Any crash here is a host-stack bug. BlueZ 5.85 before "
                     "5.85-4ubuntu0.2 has a known GATT-teardown use-after-free.")
    show_counts("bluetooth.service abnormal exits", by_day(coredumps))

    dash = journal(args, unit=args.unit)

    # 2. Adapter recoveries and health events from the dashboard itself.
    show_counts("adapter recoveries fired", by_day([l for l in dash if "[recovery]" in l and "recovering" in l]),
                good="0-1 per week",
                note="Each one power-cycles the radio; a link open at that moment may be lost.")
    show_counts("adapter-level faults noted", by_day([l for l in dash if "[health]" in l]))
    show_counts("event-loop rebuilds (bluetoothd restarted under us)",
                by_day([l for l in dash if "[stack]" in l]))
    show_counts("reads skipped by the health gate",
                by_day([l for l in dash if "skipped —" in l]),
                good="a handful after each fault",
                note="Expected right after a fault; constant skipping means the gate is too wide.")

    # 3. Whole-bank outages: minutes in which 3+ distinct batteries went missing.
    per_minute = collections.defaultdict(set)
    failures = collections.Counter()
    for line in dash:
        m = BATTERY_LINE.search(line)
        if not m:
            continue
        name, kind = m.group(1), m.group(2)
        failures[(name, kind)] += 1
        if kind in ("not advertising", "not found in scan"):
            ts = TIMESTAMP.match(line)
            if ts:
                per_minute[f"{ts.group(1)} {ts.group(2)}"].add(name)
    outages = {k: v for k, v in per_minute.items() if len(v) >= 3}
    print("\nwhole-bank scan outages (3+ batteries missing in the same minute)")
    print("  This is the adapter wedging. It is the event that preceded the "
          "ECO-WORTHY's death.")
    if not outages:
        print("  none — good")
    else:
        for minute in sorted(outages):
            print(f"  {minute}  {', '.join(sorted(outages[minute]))}")
        print(f"  total {len(outages)} minute(s)")

    # 4. Kernel wedge signatures.
    kernel_lines = journal(args, kernel=True)
    wedges = [l for l in kernel_lines if any(p in l for p in WEDGE_PATTERNS)]
    show_counts("kernel controller-wedge lines", by_day(wedges),
                good="near zero",
                note="Opcode 0x200e/0x2005 failing with -16 is host/controller desync.")

    # 5. Per-battery failure profile.
    print("\nper-battery failures")
    if not failures:
        print("  none")
    else:
        names = sorted({n for n, _ in failures})
        kinds = sorted({k for _, k in failures})
        width = max(len(n) for n in names)
        print(f"  {'battery'.ljust(width)}  " + "  ".join(k[:18].rjust(18) for k in kinds))
        for name in names:
            row = "  ".join(str(failures[(name, k)]).rjust(18) for k in kinds)
            print(f"  {name.ljust(width)}  {row}")
        print("\n  One battery far worse than the rest is that battery or where it sits,"
              "\n  not the adapter. All of them together is the adapter.")

    print("\n" + "=" * 72)
    verdict = []
    if crashes or coredumps:
        verdict.append(f"{len(crashes) + len(coredumps)} bluetoothd crash(es) — "
                       "check the bluez package version")
    if outages:
        verdict.append(f"{len(outages)} whole-bank outage(s) — the adapter is wedging")
    print("VERDICT: " + ("; ".join(verdict) if verdict
                         else "no host-stack faults in this window"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
