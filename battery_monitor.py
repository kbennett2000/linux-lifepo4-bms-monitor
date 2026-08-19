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


def _fmt_runtime(seconds):
    """Estimated time to empty. Only derived while a pack is actually discharging."""
    if not seconds or seconds <= 0:
        return "—"
    hours, minutes = divmod(round(seconds / 60), 60)
    if hours >= 24:
        return f"{hours // 24}d {hours % 24}h"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _fmt_capacity(reading):
    """Available amp-hours, and what fraction of the pack's rated capacity that is.

    Measured against the *rated* figure rather than the capacity the BMS calls full, so
    a healthy pack that exceeds its rating reads above 100% instead of being pinned at
    it. `rated_ah` is merged in by the caller from config.json.
    """
    available = reading.get("capacity_ah")
    if available is None:
        return "—"
    rated = reading.get("rated_ah")
    if not rated:
        return f"{available:.1f} Ah"
    return f"{available:.1f} Ah  ({available / rated * 100:.0f}% of {rated:.0f} Ah rated)"


def _fmt_energy(wh):
    """Stored energy, switching to kWh once the raw watt-hours get unwieldy."""
    if wh is None:
        return "—"
    return f"{wh / 1000:.2f} kWh" if wh >= 1000 else f"{wh:.0f} Wh"


def _switch_states(reading):
    """Charge/discharge MOSFET and balancer state, skipping anything not reported.

    An omitted switch means "this protocol does not report it", never "off", so the two
    must not be collapsed into a single display value.
    """
    return "  ".join(
        f"{name} {'on' if value else 'off'}"
        for name, value in (
            ("charge", reading.get("chrg_mosfet")),
            ("discharge", reading.get("dischrg_mosfet")),
            ("balancer", reading.get("balancer")),
        )
        if value is not None
    )


def print_reading(name, reading):
    """Print one battery's reading in the standard block format."""
    print(f"   Voltage      : {_fmt(reading['voltage'])} V")
    print(f"   Current      : {_fmt(reading['current'])} A")
    print(f"   SOC          : {reading['soc']}%")
    print(f"   Capacity     : {_fmt_capacity(reading)}")
    print(f"   Power        : {_fmt(reading['power'], 1)} W")
    print(f"   Stored       : {_fmt_energy(reading.get('energy_wh'))}")
    print(f"   Runtime      : {_fmt_runtime(reading.get('runtime_seconds'))}")
    print(f"   Temperature  : {_fmt(reading['temperature'], 1)} °C")
    if len(temps := reading.get("temps") or []) > 1:
        print(f"   Sensors      : {' / '.join(f'{t:.1f}' for t in temps)} °C")
    soh = reading.get("soh")
    print(f"   Health       : {f'{soh:.0f}%' if soh is not None else '—'}")
    print(f"   Cell ΔV      : {_fmt(reading['delta_mv'], 1)} mV")
    print(f"   Cycles       : {reading['cycles'] if reading['cycles'] is not None else '—'}")
    if cells := reading.get("cells"):
        print(f"   Cells        : {[f'{v:.3f}' for v in cells]}")
    if switches := _switch_states(reading):
        print(f"   Switches     : {switches}")
    if reading.get("problem"):
        code = reading.get("problem_code")
        detail = "" if code is None else f" (code {code}, 0x{code:X})"
        print(f"   ⚠ BMS reports a fault{detail}")


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
    # Rated capacity is configuration, not battery data, so it is merged here rather
    # than inside the driver — the same place the dashboard merges it.
    reading["rated_ah"] = bms_driver.rated_capacity(entry, reading)
    print_reading(name, reading)


# Sample readings used by --demo mode, so the terminal monitor can be previewed
# (and screenshotted for the README) without a real BMS connected. These are shaped
# exactly like a real reading and go through `print_reading()`, so the demo output can
# never drift away from what the hardware path prints.
DEMO_READINGS = [
    {
        "label": "House Bank · 200Ah", "voltage": 13.21, "current": -22.4, "soc": 85,
        "power": -295.9, "temperature": 23.5, "delta_mv": 4.0, "cycles": 142,
        "cells": [3.301, 3.305, 3.302, 3.303],
        # A slightly aged pack: the BMS's own full capacity has drifted below rated.
        "rated_ah": 200, "capacity_ah": 167.5, "capacity_full_ah": 198,
        "energy_wh": 2213, "soh": None, "runtime_seconds": 26920,
        "problem": False, "problem_code": 0,
        "chrg_mosfet": True, "dischrg_mosfet": True, "balancer": False,
        "temps": [23.2, 23.8],
    },
    {
        "label": "Solar Array · 200Ah", "voltage": 14.05, "current": 18.6, "soc": 92,
        "power": 261.3, "temperature": 25.1, "delta_mv": 3.0, "cycles": 88,
        "cells": [3.512, 3.514, 3.511, 3.513],
        "rated_ah": 200, "capacity_ah": 184.6, "capacity_full_ah": 200,
        "energy_wh": 2594, "soh": None,
        # Charging, so there is no time-to-empty to report.
        "runtime_seconds": None,
        "problem": False, "problem_code": 0,
        "chrg_mosfet": True, "dischrg_mosfet": True, "balancer": True,
        "temps": [24.8, 25.4],
    },
    {
        # The drawn-down pack, and the largest one -- see dashboard.py's note on why
        # this keeps the Capacity summary tile distinct from Avg SOC.
        "label": "Reserve · 330Ah", "voltage": 13.05, "current": 0.0, "soc": 40,
        "power": 0.0, "temperature": 22.0, "delta_mv": 2.0, "cycles": 37,
        "cells": [3.262, 3.264, 3.263, 3.263],
        "rated_ah": 330, "capacity_ah": 132.4, "capacity_full_ah": 331,
        "energy_wh": 1728, "soh": None, "runtime_seconds": None,
        "problem": False, "problem_code": 0,
        "chrg_mosfet": True, "dischrg_mosfet": True, "balancer": False,
        "temps": [22.0],
    },
    {
        # A second protocol, so the demo also covers the fields JBD does not report and
        # the ones it does not: health present, cycle count absent, no switch states, and
        # a healthy pack above its rated capacity (sold as 50 Ah, reports 52 Ah full).
        "label": "Utility · 50Ah", "voltage": 13.86, "current": -3.2, "soc": 100,
        "power": -44.4, "temperature": 34.5, "delta_mv": 12.0, "cycles": None,
        "cells": [3.462, 3.468, 3.456, 3.474],
        # Sold as 50 Ah, reports 52 Ah full — so a full pack reads 104%, not 100%.
        "rated_ah": 50, "capacity_ah": 52.0, "capacity_full_ah": 52,
        "energy_wh": 721, "soh": 100.0, "runtime_seconds": 58500,
        "problem": False, "problem_code": 0,
        "chrg_mosfet": None, "dischrg_mosfet": None, "balancer": None,
        "temps": [35.5, 33.5],
    },
]


def print_demo():
    """Print one cycle of sample readings (no Bluetooth) for previews/screenshots."""
    print(f"\n=== Battery Monitor @ {time.strftime('%H:%M:%S')} ===")
    for reading in DEMO_READINGS:
        print(f"\n🔋 Reading {reading['label']} ...")
        print_reading(reading["label"], reading)
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
