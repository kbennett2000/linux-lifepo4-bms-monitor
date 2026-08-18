#!/usr/bin/env python3
"""
LiFePO4 Battery Monitor - Terminal Version
==========================================

This script monitors multiple LiFePO4 batteries over Bluetooth Low Energy (BLE)
on Ubuntu/Linux.

Supported batteries:
- Standard JBD / Jiabaida / Daly / Overkill BMS (most common)
- ECO-WORTHY BMS

Features:
- Reads voltage, current, power, SOC, individual cell voltages
- Supports multiple batteries simultaneously
- Clean, readable output updated every ~25 seconds
- Designed to be easy to understand and extend

How it works:
Every battery is read through `bms_driver.read_battery()`, which picks the right
`aiobmsble` driver for the battery's `protocol` in config.json, connects, reads,
and disconnects gracefully. Adding a new BMS model is a config change, not a code
change, as long as `aiobmsble` supports it.

Author: Kris Bennett (May 2026)
"""

import argparse
import asyncio
import time

import bms_driver
from bms_config import battery_entries, load_config, polling_config

CONFIG = load_config()
ENTRIES = battery_entries(CONFIG)
POLLING = polling_config(CONFIG)

# Fail loudly at startup rather than silently never reporting a battery.
bms_driver.validate_protocols(ENTRIES)


def _fmt(value, digits=2):
    """Format a reading, showing an em dash for values this protocol doesn't report."""
    return "—" if value is None else f"{value:.{digits}f}"


def print_reading(name, reading):
    """Print one battery's reading in the standard block format."""
    print(f"   Voltage      : {_fmt(reading['voltage'])} V")
    print(f"   Current      : {_fmt(reading['current'])} A")
    print(f"   SOC          : {reading['soc']}%")
    print(f"   Power        : {_fmt(reading['power'], 1)} W")
    print(f"   Temperature  : {_fmt(reading['temperature'], 1)} °C")
    print(f"   Cell ΔV      : {_fmt(reading['delta_mv'], 1)} mV")
    print(f"   Cycles       : {reading['cycles'] if reading['cycles'] is not None else '—'}")
    if cells := reading.get("cells"):
        print(f"   Cells        : {[f'{v:.3f}' for v in cells]}")


async def read_and_print(name, entry):
    """Read one battery and print it, whatever protocol it speaks."""
    print(f"\n🔋 Reading {entry['label']} ({entry['address']}) ...")
    reading = await bms_driver.read_battery(
        name,
        entry["address"],
        entry["protocol"],
        persistent=False,          # the terminal view is short-lived; don't hold links
        scan_timeout=POLLING["scan_timeout"],
        log=lambda msg: print(f"   {msg}"),
    )
    if reading is None:
        print("   ❌ No reading this cycle")
        return
    print_reading(name, reading)


# Sample readings used by --demo mode, so the terminal monitor can be previewed
# (and screenshotted for the README) without a real BMS connected.
DEMO_READINGS = [
    ("House Bank · 200Ah", 13.21, -22.4, 85, -295.9, 23.5, 4.0, 142,
     [3.301, 3.305, 3.302, 3.303]),
    ("Solar Array · 200Ah", 14.05, 18.6, 92, 261.3, 25.1, 3.0, 88,
     [3.512, 3.514, 3.511, 3.513]),
    ("Reserve · 330Ah", 13.40, 0.0, 99, 0.0, 22.0, 2.0, 37,
     [3.349, 3.351, 3.350, 3.350]),
]


def print_demo():
    """Print one cycle of sample readings (no Bluetooth) for previews/screenshots."""
    print(f"\n=== Battery Monitor @ {time.strftime('%H:%M:%S')} ===")
    for name, v, c, soc, p, temp, dv, cyc, cells in DEMO_READINGS:
        print(f"\n🔋 Reading {name} ...")
        print(f"   Voltage      : {v:.2f} V")
        print(f"   Current      : {c:.2f} A")
        print(f"   SOC          : {soc}%")
        print(f"   Power        : {p:.1f} W")
        print(f"   Temperature  : {temp:.1f} °C")
        print(f"   Cell ΔV      : {dv:.1f} mV")
        print(f"   Cycles       : {cyc}")
        print(f"   Cells        : {[f'{x:.3f}' for x in cells]}")
    print(f"\n--- All batteries read — sleeping 25 seconds ---\n")


async def main(demo=False):
    """
    Main loop of the program.
    Continuously reads all batteries in sequence and prints the results.
    """
    if not demo:
        # Clear any link BlueZ still holds from a previous run — it does not close
        # them when a process dies, and a peripheral that never learns the link went
        # away can keep believing it is still connected.
        await bms_driver.close_stale_links(
            [e["address"] for e in ENTRIES.values()],
            log=lambda msg: print(msg),
        )

    try:
        while True:
            if demo:
                print_demo()
                await asyncio.sleep(25)
                continue

            print(f"\n=== Battery Monitor @ {time.strftime('%H:%M:%S')} ===")

            # Read each battery one at a time (prevents Bluetooth "InProgress" errors)
            for name, entry in ENTRIES.items():
                await read_and_print(name, entry)
                await asyncio.sleep(3)          # Small delay between batteries

            print(f"\n--- All batteries read — sleeping 25 seconds ---\n")
            await asyncio.sleep(25)
    finally:
        # Never exit leaving a peripheral holding a connection it thinks is live.
        await bms_driver.close_all_sessions()


# =============================================================================
# Program entry point
# =============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LiFePO4 BMS terminal monitor")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Show sample readings instead of polling Bluetooth (no hardware needed)",
    )
    args = parser.parse_args()
    # This is the standard way to run an asyncio program
    asyncio.run(main(demo=args.demo))
