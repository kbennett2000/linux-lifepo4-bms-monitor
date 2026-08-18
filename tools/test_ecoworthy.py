#!/usr/bin/env python3
"""
test_ecoworthy.py - BMS driver probe
====================================

Connects to one battery using the same `aiobmsble` driver the dashboard uses, and
prints what comes back. Use it to check that a battery is understood *before*
wiring it into config.json, and to diagnose one that reports nothing.

With -v it also prints every raw BLE frame and, crucially, any
`invalid checksum` line. The ECO-WORTHY driver validates a MODBUS CRC on every
frame; if your hardware revision computes it differently, every frame is silently
discarded and the battery looks permanently offline. That log line is the way to
tell those two situations apart.

Usage:
    python3 tools/test_ecoworthy.py                     # first ecoworthy battery in config.json
    python3 tools/test_ecoworthy.py -v                  # + raw frames and CRC diagnostics
    python3 tools/test_ecoworthy.py AA:BB:CC:DD:EE:FF   # explicit MAC
    python3 tools/test_ecoworthy.py --protocol jbd AA:BB:CC:DD:EE:FF
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow running as `python3 tools/test_ecoworthy.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bms_driver
from bms_config import battery_entries, load_config


def pick_battery(address, protocol):
    """Resolve which battery to probe: explicit args win, else config.json."""
    entries = battery_entries(load_config())

    if address:
        return "probe", {"address": address, "protocol": protocol or "ecoworthy"}

    for name, entry in entries.items():
        if entry["protocol"] == (protocol or "ecoworthy"):
            return name, entry

    sys.exit(
        f"No battery with protocol {protocol or 'ecoworthy'!r} in config.json — "
        f"pass a MAC address instead."
    )


async def main():
    parser = argparse.ArgumentParser(description="Probe one BMS with its aiobmsble driver")
    parser.add_argument("address", nargs="?", help="MAC address (default: from config.json)")
    parser.add_argument("--protocol", help="Protocol name (default: ecoworthy)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show raw BLE frames and CRC validation failures")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")
        for noisy in ("aiobmsble", "bleak", "bleak_retry_connector"):
            logging.getLogger(noisy).setLevel(logging.DEBUG)

    name, entry = pick_battery(args.address, args.protocol)
    print(f"Probing {entry['address']} as protocol {entry['protocol']!r} ...\n")

    reading = await bms_driver.read_battery(
        name, entry["address"], entry["protocol"], persistent=False
    )

    if reading is None:
        print("\n❌ No reading.")
        print("   Re-run with -v. If you see 'invalid checksum' lines the driver is")
        print("   reaching the battery but rejecting its frames — report that upstream")
        print("   to aiobmsble with the raw frames. If you see nothing at all, the")
        print("   battery is out of range, powered down, or held by another client")
        print("   (close the phone app).")
        return 1

    print("✅ Decoded reading:\n")
    for key, value in reading.items():
        print(f"   {key:<12}: {value}")
    print("\nSanity checks:")
    cells = reading.get("cells") or []
    if cells:
        print(f"   sum(cells)  = {sum(cells):.2f} V  (should be close to voltage)")
    print(f"   cell count  = {len(cells)}")
    print("   Compare SOC, voltage and current against the phone app before trusting it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
