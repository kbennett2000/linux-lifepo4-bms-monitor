#!/usr/bin/env python3
"""
LiFePO4 Battery Dashboard - Web Interface
=========================================

Modern, mobile-friendly web dashboard for monitoring multiple LiFePO4 batteries
over Bluetooth on Ubuntu/Linux.

All static UI strings, the listen port, and the battery list live in
``config.json`` so editing them does not require touching Python code.

The dashboard ships every asset locally; once the Python deps are installed
it runs with zero internet connection.

Author: Kris Bennett (May 2026)
"""

import argparse
import asyncio
import threading
import time

from flask import Flask, jsonify, render_template

from bleak import BleakClient, BleakScanner
from aiobmsble.bms.jbd_bms import BMS

from bms_config import battery_tuples, load_config


CONFIG = load_config()
BATTERIES = battery_tuples(CONFIG)

# A battery that fails to produce a fresh reading for this many consecutive poll
# cycles is shown as "stale" (last-known values, dimmed in the UI) instead of
# disappearing from the dashboard. Counting cycles rather than wall-clock seconds
# keeps the flag accurate even though a full poll cycle can take anywhere from
# ~40s to a couple of minutes depending on BLE scan timing.
STALE_AFTER_MISSES = 1

# Attempts per battery within a single cycle before it counts as a miss. One retry
# absorbs the common case of a single dropped BLE scan / missed advertisement.
FETCH_ATTEMPTS = 2

app = Flask(__name__, static_folder="static", template_folder="templates")

latest_data = {}
update_lock = threading.Lock()


async def fetch_jbd(name, address):
    try:
        device = await BleakScanner.find_device_by_address(address, timeout=8.0)
        if not device:
            return None
        async with BMS(ble_device=device) as bms:
            data = await bms.async_update()
            return {
                "address": address,
                "voltage": round(data.get("voltage", 0), 2),
                "current": round(data.get("current", 0), 2),
                "power": round(data.get("power", 0), 1),
                "soc": data.get("battery_level", 0),
                "temperature": round(data.get("temperature", 0), 1),
                "delta_mv": round(data.get("delta_voltage", 0) * 1000, 1),
                "cycles": data.get("cycles", 0),
                "cells": [round(v, 3) for v in (data.get("cell_voltages") or [])],
            }
    except Exception:
        return None


async def fetch_ecoworthy(name, address):
    try:
        device = await BleakScanner.find_device_by_address(address, timeout=10.0)
        if not device:
            return None

        a1 = a2 = None

        async with BleakClient(device) as client:
            def handler(_sender, data):
                nonlocal a1, a2
                h = data.hex()
                if h.startswith("e2e7798a56a3a1"):
                    a1 = h
                elif h.startswith("e2e7798a56a3a2"):
                    a2 = h

            await client.start_notify("0000fff1-0000-1000-8000-00805f9b34fb", handler)
            await client.write_gatt_char(
                "0000fff2-0000-1000-8000-00805f9b34fb",
                bytes.fromhex("dda50300fffd77"),
            )
            await asyncio.sleep(1.2)
            await client.write_gatt_char(
                "0000fff2-0000-1000-8000-00805f9b34fb",
                bytes.fromhex("dda50400fffc77"),
            )
            await asyncio.sleep(1.5)

        if not a2 or not a1:
            return None

        a2b = bytes.fromhex(a2)
        cells = [
            round(int.from_bytes(a2b[14 + i * 2 : 16 + i * 2], "big") / 1000, 3)
            for i in range(4)
        ]
        voltage = round(sum(cells), 2)

        a1b = bytes.fromhex(a1)
        current = round(int.from_bytes(a1b[20:22], "big", signed=True) / 10, 2)
        power = round(voltage * current, 1)
        soc = a1b[15]

        return {
            "address": address,
            "voltage": voltage,
            "current": current,
            "power": power,
            "soc": soc,
            "cells": cells,
        }
    except Exception:
        return None


def _fetch_with_retry(loop, name, addr, proto, attempts=FETCH_ATTEMPTS):
    """Read a single battery, retrying a few times before giving up this cycle.

    Each attempt needs a fresh coroutine (a coroutine cannot be awaited twice).
    Returns the reading dict on success, or None if every attempt failed.
    """
    for _ in range(attempts):
        coro = fetch_jbd(name, addr) if proto == "jbd" else fetch_ecoworthy(name, addr)
        result = loop.run_until_complete(coro)
        if result:
            return result
    return None


def background_updater():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    labels = {n: b.get("label", n) for n, b in CONFIG["batteries"].items()}

    while True:
        try:
            results = {
                name: _fetch_with_retry(loop, name, addr, proto)
                for name, (addr, proto) in BATTERIES.items()
            }

            now = time.time()
            with update_lock:
                for name, result in results.items():
                    if result:
                        # Fresh reading: store it and reset the miss counter.
                        result["label"] = labels.get(name, name)
                        result["last_seen"] = now
                        result["misses"] = 0
                        latest_data[name] = result
                    elif name in latest_data:
                        # No reading this cycle: keep the last-known-good values
                        # and bump the miss counter so api_data() can flag it stale.
                        latest_data[name]["misses"] = (
                            latest_data[name].get("misses", 0) + 1
                        )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # noqa: BLE001 - never let the poll thread die
            # A single bad cycle (including CancelledError, which is a BaseException
            # and would otherwise escape) must not kill the updater thread, or the
            # whole dashboard would freeze until a manual restart.
            print(f"[background_updater] cycle error: {type(exc).__name__}: {exc}")

        time.sleep(10)


@app.route("/")
def dashboard():
    return render_template("dashboard.html", ui=CONFIG["ui"])


@app.route("/api/data")
def api_data():
    now = time.time()
    with update_lock:
        snapshot = {name: dict(entry) for name, entry in latest_data.items()}

    for entry in snapshot.values():
        misses = entry.get("misses", 0)
        entry["stale"] = misses >= STALE_AFTER_MISSES
        entry["age_seconds"] = int(max(0.0, now - entry.get("last_seen", now)))

    return jsonify(snapshot)


@app.route("/api/config")
def api_config():
    return jsonify({"ui": CONFIG["ui"]})


def main():
    parser = argparse.ArgumentParser(description="LiFePO4 BMS web dashboard")
    parser.add_argument("--host", default=None, help="Override listen host")
    parser.add_argument("--port", type=int, default=None, help="Override listen port")
    args = parser.parse_args()

    host = args.host or CONFIG["server"].get("host", "0.0.0.0")
    port = args.port or CONFIG["server"].get("port", 8040)

    threading.Thread(target=background_updater, daemon=True).start()

    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"Dashboard running at http://{display_host}:{port}")
    print("Keep this terminal open while using the dashboard.")

    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
