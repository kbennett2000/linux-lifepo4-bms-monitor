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
import os
import signal
import subprocess
import threading
import time

from flask import Flask, jsonify, render_template, request

import bms_driver
from bms_config import battery_entries, load_config, polling_config

# Optional in-process BLE-adapter recovery. If the dependency isn't installed we
# fall back to `systemctl restart bluetooth`, so the dashboard still runs without
# it (e.g. before the deps are refreshed on the server).
try:
    from bluetooth_auto_recovery import recover_adapter
except ImportError:  # pragma: no cover - exercised only on under-provisioned hosts
    recover_adapter = None

# Adapter enumeration is a separate package and often present even when the recovery
# library is not, so import it independently — otherwise a missing recovery library
# would also blind us to which hci index is in use.
try:
    from bluetooth_adapters import get_adapters_from_hci
except ImportError:  # pragma: no cover
    get_adapters_from_hci = None


CONFIG = load_config()
ENTRIES = battery_entries(CONFIG)
POLLING = polling_config(CONFIG)
ADDRESSES = [e["address"] for e in ENTRIES.values()]

# Fail loudly at startup on an unknown protocol. Previously any unrecognised value
# silently fell through to the ECO-WORTHY branch, so a typo produced a battery that
# simply never reported — indistinguishable from one that was out of range.
bms_driver.validate_protocols(ENTRIES)

# A battery that fails to produce a fresh reading for this many consecutive poll
# cycles is shown as "stale" (last-known values, dimmed in the UI) instead of
# disappearing from the dashboard. Counting cycles rather than wall-clock seconds
# keeps the flag accurate even though a full poll cycle can take anywhere from
# ~40s to a couple of minutes depending on BLE scan timing.
STALE_AFTER_MISSES = 1

# Attempts per battery within a single cycle before it counts as a miss. One retry
# absorbs the common case of a single dropped BLE scan / missed advertisement.
# Configurable via the "polling" block in config.json.
FETCH_ATTEMPTS = POLLING["attempts"]

# When a battery has missed this many *consecutive* cycles, the BLE adapter itself
# has likely wedged (BlueZ/HCI stops returning advertisements after hours of
# scanning). At that point no amount of Python retrying helps — the adapter must be
# power-cycled. Set high enough to rule out an ordinary transient miss.
RECOVER_AFTER_MISSES = 3

# ...but a *single* battery missing means that battery is missing, not that the
# adapter has wedged — a wedged adapter loses every battery at once. Requiring a
# quorum stops one flaky pack from power-cycling the adapter for the whole bank,
# which used to rip the link out from under the healthy packs (and, for a peripheral
# that is mid-connection, is exactly how it ends up holding a phantom link).
RECOVER_MIN_BATTERIES = max(2, (len(ENTRIES) + 1) // 2)

# Give BlueZ a moment to finish the disconnects we asked for before power-cycling.
RECOVER_SETTLE_SECONDS = 2.0

# Don't attempt adapter recovery more than once per this many seconds, so a battery
# that is genuinely offline (e.g. removed) can't trigger a power-cycle every cycle.
RECOVER_COOLDOWN_SECONDS = 300

app = Flask(__name__, static_folder="static", template_folder="templates")

latest_data = {}
update_lock = threading.Lock()

# --- Release switch -------------------------------------------------------------
# These BMS modules accept a single BLE connection at a time, so while the dashboard
# holds one the phone app cannot connect. `release_until` lets a user hand a battery
# back temporarily: the poll loop drops the link and skips that battery until the
# deadline passes. Flask serves requests on its own threads, so this needs a lock.
release_lock = threading.Lock()
release_until = {}          # {battery_name: epoch_seconds}

# Set when the process is shutting down, so the poll loop can close its BLE links
# before exit rather than leaving peripherals holding phantom connections.
shutdown_event = threading.Event()
shutdown_done = threading.Event()


def _released_until(name):
    """Epoch until which `name` is released, or None if it is not released."""
    now = time.time()
    with release_lock:
        deadline = release_until.get(name)
        if deadline is None:
            return None
        if deadline <= now:
            del release_until[name]
            return None
        return deadline


def _fetch_with_retry(loop, name, entry, attempts=FETCH_ATTEMPTS):
    """Read a single battery, retrying a few times before giving up this cycle.

    Each attempt needs a fresh coroutine (a coroutine cannot be awaited twice).
    Returns the reading dict on success, or None if every attempt failed.

    Failures are logged rather than swallowed. Distinguishing "not found in scan"
    from "connected but no data" is what tells you whether a battery is out of
    range, wedged, or talking a protocol we're misreading — and the same signal
    drives the adapter-recovery decision below.
    """
    for _ in range(attempts):
        result = loop.run_until_complete(
            bms_driver.read_battery(
                name,
                entry["address"],
                entry["protocol"],
                persistent=entry["persistent"],
                scan_timeout=POLLING["scan_timeout"],
                log=lambda msg: print(msg, flush=True),
            )
        )
        if result:
            return result
    return None


def _resolve_adapter():
    """Best-effort discovery of the BLE adapter's (hci_index, mac) for recovery.

    Returns (0, None) if the helper library is unavailable or discovery fails;
    recover_adapter tolerates an unknown MAC, and hci0 is the near-universal default.
    """
    if get_adapters_from_hci is None:
        return 0, None
    try:
        adapters = get_adapters_from_hci()  # {hci_index: {"bdaddr": "AA:..", ...}}
    except Exception as exc:  # noqa: BLE001 - discovery is advisory only
        print(f"[recovery] could not enumerate adapters: {type(exc).__name__}: {exc}")
        return 0, None
    if not adapters:
        return 0, None
    # NOTE: assumes a single BLE adapter. We pick hci0 (or the lowest index), which
    # matches Bleak's default adapter. On a host with more than one adapter, recovery
    # could target the wrong one — warn so that case is visible in the logs.
    if len(adapters) > 1:
        print(f"[recovery] multiple BLE adapters found ({sorted(adapters)}); "
              f"recovery assumes the lowest index — verify it's the one in use")
    hci = 0 if 0 in adapters else sorted(adapters)[0]
    return hci, adapters[hci].get("bdaddr")


def _recover_adapter(loop):
    """Power-cycle the BLE adapter to clear a wedged BlueZ/HCI state.

    Tries the bluetooth-auto-recovery library first, escalating from a gentle power
    cycle to a USB reset only if the gentle attempt fails. Falls back to the
    `systemctl restart bluetooth` command that is already known to fix this by hand.
    Returns True if a recovery path reported success. Never raises.

    Every path here yanks the adapter out from under any open link *without* the
    peripheral being told, and a peripheral that never learns the link is gone can
    keep believing it is connected — and a connected peripheral stops advertising.
    So we always quiesce first.
    """
    # Close everything we know we hold, then ask BlueZ to drop anything it is still
    # holding on our behalf (e.g. an ACL leaked by a previous process generation).
    if bms_driver.has_open_links():
        print(f"[recovery] closing held links before power-cycle: "
              f"{', '.join(bms_driver.held_session_names())}")
    try:
        loop.run_until_complete(bms_driver.close_all_sessions())
        loop.run_until_complete(bms_driver.close_stale_links(ADDRESSES))
    except Exception as exc:  # noqa: BLE001 - quiescing is best-effort
        print(f"[recovery] link teardown failed: {type(exc).__name__}: {exc}")
    time.sleep(RECOVER_SETTLE_SECONDS)

    hci, mac = _resolve_adapter()

    if recover_adapter is not None:
        # Graduated recovery: a plain power-cycle (gone_silent=False) clears most
        # wedges. Only if that fails do we escalate to gone_silent=True, which adds a
        # disruptive USB unbind/rebind that can re-enumerate the adapter.
        for gone_silent in (False, True):
            try:
                ok = loop.run_until_complete(
                    recover_adapter(hci, mac, gone_silent=gone_silent)
                )
                print(f"[recovery] recover_adapter(hci{hci}, {mac}, "
                      f"gone_silent={gone_silent}) -> {ok}")
                if ok:
                    return True
            except Exception as exc:  # noqa: BLE001 - fall through / escalate
                print(f"[recovery] recover_adapter(gone_silent={gone_silent}) failed: "
                      f"{type(exc).__name__}: {exc}")

    # Fallback: the exact command proven to work by hand on this server.
    try:
        subprocess.run(
            ["systemctl", "restart", "bluetooth"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        print("[recovery] 'systemctl restart bluetooth' succeeded")
        return True
    except Exception as exc:  # noqa: BLE001 - log and let the next cycle retry
        print(f"[recovery] 'systemctl restart bluetooth' failed: "
              f"{type(exc).__name__}: {exc}")
        return False


def background_updater():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    labels = {name: entry["label"] for name, entry in ENTRIES.items()}
    last_recovery = 0.0  # epoch of the last adapter-recovery attempt (cooldown)
    # Consecutive-miss counter for *every* configured battery, used to detect a
    # wedged adapter. Kept independent of latest_data so a battery that has never
    # been read once (adapter already wedged at startup) still counts toward
    # recovery, not just ones with a last-known-good reading.
    miss_counts = {name: 0 for name in ENTRIES}

    # BlueZ keeps an ACL open when the owning process dies, so after a crash, a
    # `kill -9` or a `systemctl restart` the previous run's links may still be
    # attached — which both blocks our reconnect and leaves the peripheral believing
    # it is still connected. Sweep them before the first poll.
    try:
        loop.run_until_complete(bms_driver.close_stale_links(ADDRESSES))
    except Exception as exc:  # noqa: BLE001 - advisory
        print(f"[startup] stale-link sweep failed: {type(exc).__name__}: {exc}")

    try:
        while not shutdown_event.is_set():
            try:
                results = {}
                for name, entry in ENTRIES.items():
                    if shutdown_event.is_set():
                        break
                    # Released to the phone app: hand the link back and skip it.
                    if _released_until(name) is not None:
                        loop.run_until_complete(bms_driver.drop_session(name))
                        continue
                    results[name] = _fetch_with_retry(loop, name, entry)

                now = time.time()
                with update_lock:
                    for name, result in results.items():
                        if result:
                            # Fresh reading: store it and reset the miss counter.
                            result["label"] = labels.get(name, name)
                            # Rated capacity is config, not battery data, so it is merged
                            # here alongside the label rather than inside the driver.
                            result["rated_ah"] = bms_driver.rated_capacity(
                                ENTRIES[name], result
                            )
                            result["last_seen"] = now
                            result["misses"] = 0
                            latest_data[name] = result
                            miss_counts[name] = 0
                        else:
                            miss_counts[name] += 1
                            if name in latest_data:
                                # Keep the last-known-good values and mirror the miss
                                # count so api_data() can flag the card stale.
                                latest_data[name]["misses"] = miss_counts[name]

                # A wedged adapter loses *every* battery at once. One battery missing
                # just means that battery is missing, and power-cycling the adapter for
                # it tears down the healthy packs' links too. Require a quorum.
                at_threshold = sum(
                    1 for c in miss_counts.values() if c >= RECOVER_AFTER_MISSES
                )
                if (
                    at_threshold >= min(RECOVER_MIN_BATTERIES, len(ENTRIES))
                    and now - last_recovery >= RECOVER_COOLDOWN_SECONDS
                ):
                    print(
                        f"[recovery] {at_threshold}/{len(ENTRIES)} batteries missing "
                        f"{RECOVER_AFTER_MISSES}+ cycles — recovering BLE adapter"
                    )
                    # Stamp the cooldown from the actual attempt time, not the pre-scan
                    # `now` (a full scan can take minutes), so the cooldown reflects real
                    # elapsed time between recovery attempts.
                    last_recovery = time.time()
                    if _recover_adapter(loop):
                        # Recovery restarts bluetoothd / re-enumerates the adapter out
                        # from under this loop, invalidating the D-Bus connection bleak
                        # holds inside it. Sessions were already closed by
                        # _recover_adapter, so it is safe to start a fresh loop.
                        loop.close()
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        # Reset miss counters so we wait a full RECOVER_AFTER_MISSES
                        # window before deciding the recovery didn't take.
                        miss_counts = {name: 0 for name in ENTRIES}
                        with update_lock:
                            for entry in latest_data.values():
                                entry["misses"] = 0
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:  # noqa: BLE001 - never let the poll thread die
                # A single bad cycle (including CancelledError, which is a BaseException
                # and would otherwise escape) must not kill the updater thread, or the
                # whole dashboard would freeze until a manual restart.
                print(f"[background_updater] cycle error: {type(exc).__name__}: {exc}")

            shutdown_event.wait(POLLING["interval_seconds"])
    finally:
        # Last line of defence: never leave a peripheral holding a phantom link.
        try:
            loop.run_until_complete(bms_driver.close_all_sessions())
        except Exception as exc:  # noqa: BLE001
            print(f"[shutdown] session teardown failed: {type(exc).__name__}: {exc}")
        shutdown_done.set()


# Sample batteries used by --demo mode. They tell a complete story at a glance:
# a bank being drawn down, a bank charging from solar, and a full bank sitting idle.
# This lets anyone preview the dashboard without a real BMS, and is what the README
# screenshots are generated from.
DEMO_BATTERIES = {
    "house_200ah": {
        "address": "A4:C1:37:55:C8:D3",
        "voltage": 13.21, "current": -22.4, "power": -295.9, "soc": 85,
        "temperature": 23.5, "delta_mv": 4.0, "cycles": 142,
        "cells": [3.301, 3.305, 3.302, 3.303], "label": "House Bank · 200Ah",
        # A slightly aged pack: the BMS's own full capacity has drifted below rated.
        "rated_ah": 200, "capacity_ah": 167.5, "capacity_full_ah": 198,
        "energy_wh": 2213, "soh": None, "runtime_seconds": 26920,
        "problem": False, "problem_code": 0,
        "chrg_mosfet": True, "dischrg_mosfet": True, "balancer": False,
        "temps": [23.2, 23.8],
    },
    "solar_200ah": {
        "address": "A4:C1:37:55:C2:29",
        "voltage": 14.05, "current": 18.6, "power": 261.3, "soc": 92,
        "temperature": 25.1, "delta_mv": 3.0, "cycles": 88,
        "cells": [3.512, 3.514, 3.511, 3.513], "label": "Solar Array · 200Ah",
        "rated_ah": 200, "capacity_ah": 184.6, "capacity_full_ah": 200,
        "energy_wh": 2594, "soh": None,
        # Charging, so there is no time-to-empty to report.
        "runtime_seconds": None,
        "problem": False, "problem_code": 0,
        "chrg_mosfet": True, "dischrg_mosfet": True, "balancer": True,
        "temps": [24.8, 25.4],
    },
    # Deliberately the drawn-down pack, and deliberately the *largest* one: it is what
    # makes the Capacity tile differ from Avg SOC (69% vs 79%). Keep them divergent if
    # you edit these numbers, or the demo stops demonstrating anything.
    "reserve_330ah": {
        "address": "A4:C1:37:25:C4:4D",
        "voltage": 13.05, "current": 0.0, "power": 0.0, "soc": 40,
        "temperature": 22.0, "delta_mv": 2.0, "cycles": 37,
        "cells": [3.262, 3.264, 3.263, 3.263], "label": "Reserve · 330Ah",
        "rated_ah": 330, "capacity_ah": 132.4, "capacity_full_ah": 331,
        "energy_wh": 1728, "soh": None, "runtime_seconds": None,
        "problem": False, "problem_code": 0,
        "chrg_mosfet": True, "dischrg_mosfet": True, "balancer": False,
        "temps": [22.0],
    },
    # A second protocol, so the demo also covers the fields JBD does *not* report and
    # the ones it does not: SoH present, cycle count absent, and a healthy pack sitting
    # above its rated capacity (sold as 50 Ah, reports 52 Ah full).
    "utility_50ah": {
        "address": "E2:E7:79:8A:56:A3",
        "voltage": 13.86, "current": -3.2, "power": -44.4, "soc": 100,
        "temperature": 34.5, "delta_mv": 12.0, "cycles": None,
        "cells": [3.462, 3.468, 3.456, 3.474], "label": "Utility · 50Ah",
        # Sold as 50 Ah, reports 52 Ah full — so a full pack reads 104%, not 100%.
        "rated_ah": 50, "capacity_ah": 52.0, "capacity_full_ah": 52,
        "energy_wh": 721, "soh": 100.0, "runtime_seconds": 58500,
        "problem": False, "problem_code": 0,
        # This protocol reports neither MOSFET states nor balancer status.
        "chrg_mosfet": None, "dischrg_mosfet": None, "balancer": None,
        "temps": [35.5, 33.5],
    },
}


def demo_updater():
    """Populate `latest_data` with realistic sample batteries (no Bluetooth).

    Mirrors the contract of `background_updater()` — same fields, same locking —
    but never touches BLE. last_seen is refreshed every cycle so the cards always
    read as fresh rather than going stale. Used by `--demo` / BMS_DEMO_MODE.
    """
    while True:
        now = time.time()
        with update_lock:
            for name, base in DEMO_BATTERIES.items():
                entry = dict(base)
                entry["last_seen"] = now
                entry["misses"] = 0
                latest_data[name] = entry
        time.sleep(5)


@app.route("/")
def dashboard():
    return render_template("dashboard.html", ui=CONFIG["ui"])


@app.route("/api/data")
def api_data():
    now = time.time()
    with update_lock:
        snapshot = {name: dict(entry) for name, entry in latest_data.items()}

    for name, entry in snapshot.items():
        misses = entry.get("misses", 0)
        entry["stale"] = misses >= STALE_AFTER_MISSES
        entry["age_seconds"] = int(max(0.0, now - entry.get("last_seen", now)))
        # Tell the UI whether this battery is deliberately handed to the phone app,
        # so a released card reads as released rather than as broken.
        deadline = _released_until(name)
        entry["released"] = deadline is not None
        entry["release_seconds_left"] = int(deadline - now) if deadline else 0
        entry["releasable"] = bool(ENTRIES.get(name, {}).get("persistent"))

    return jsonify(snapshot)


@app.route("/api/ble/release", methods=["POST"])
def api_release():
    """Temporarily drop a battery's BLE link so the phone app can connect.

    These BMS modules accept one connection at a time, so a persistently-held link
    locks the official app out entirely. The poll loop notices the release on its
    next pass, disconnects gracefully, and skips the battery until the deadline.
    """
    payload = request.get_json(silent=True) or {}
    name = payload.get("battery")
    if name not in ENTRIES:
        return jsonify({"error": f"unknown battery {name!r}"}), 404

    try:
        minutes = float(payload.get("minutes", POLLING["release_minutes"]))
    except (TypeError, ValueError):
        return jsonify({"error": "minutes must be a number"}), 400
    # Clamp so a stray request can't hand the battery away indefinitely.
    minutes = max(1.0, min(minutes, POLLING["release_max_minutes"]))

    deadline = time.time() + minutes * 60
    with release_lock:
        release_until[name] = deadline
    print(f"[release] {name} released for {minutes:.0f} min")

    return jsonify({"battery": name, "released_until": deadline, "minutes": minutes})


@app.route("/api/ble/resume", methods=["POST"])
def api_resume():
    """Cancel a release early and let the dashboard reconnect on the next cycle."""
    payload = request.get_json(silent=True) or {}
    name = payload.get("battery")
    if name not in ENTRIES:
        return jsonify({"error": f"unknown battery {name!r}"}), 404

    with release_lock:
        release_until.pop(name, None)
    print(f"[release] {name} resumed")

    return jsonify({"battery": name, "released_until": None})


@app.route("/api/config")
def api_config():
    return jsonify({"ui": CONFIG["ui"]})


def main():
    parser = argparse.ArgumentParser(description="LiFePO4 BMS web dashboard")
    parser.add_argument("--host", default=None, help="Override listen host")
    parser.add_argument("--port", type=int, default=None, help="Override listen port")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Show sample batteries instead of polling Bluetooth (no hardware needed)",
    )
    args = parser.parse_args()

    host = args.host or CONFIG["server"].get("host", "0.0.0.0")
    port = args.port or CONFIG["server"].get("port", 8040)

    demo = args.demo or os.environ.get("BMS_DEMO_MODE")
    threading.Thread(
        target=demo_updater if demo else background_updater, daemon=True
    ).start()

    if not demo:
        # systemctl stop/restart sends SIGTERM, and Python's default handler exits
        # without unwinding — so `finally` blocks never run and BLE links are dropped
        # with the peripheral never told. Give the poll thread a moment to close them.
        def _shutdown(signum, _frame):
            print(f"[shutdown] signal {signum} — closing BLE links")
            shutdown_event.set()
            shutdown_done.wait(timeout=15)
            raise SystemExit(0)

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, _shutdown)

    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    if demo:
        print("DEMO MODE — showing sample batteries, no Bluetooth required.")
    print(f"Dashboard running at http://{display_host}:{port}")
    print("Keep this terminal open while using the dashboard.")

    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
