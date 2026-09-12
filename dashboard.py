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

Poll loop, in one paragraph
---------------------------
Each adapter is polled in turn. One scan per adapter per cycle collects
advertisements for every battery due a read, and only batteries actually heard
advertising are connected to. Each battery has its own interval and its own
failure backoff, so a fragile pack can be read rarely without slowing the bank and
a chronically failing pack stops generating retry storms. An adapter is only
power-cycled when the whole adapter looks wedged, never because one battery is
missing. Batteries marked ``health_gated`` sit out for a while after any
adapter-level fault. See ``docs/adr/0003-ecoworthy-link-exposure.md``.

Author: Kris Bennett (May 2026)
"""

import argparse
import asyncio
import contextlib
import json
import os
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import bms_driver
from bms_config import (
    adapters,
    batteries_on,
    battery_attempts,
    battery_entries,
    battery_interval,
    load_config,
    polling_config,
)

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
ADAPTERS = adapters(ENTRIES)
ADDRESSES = [e["address"] for e in ENTRIES.values()]
ROOT = Path(__file__).resolve().parent

# Fail loudly at startup on an unknown protocol. Previously any unrecognised value
# silently fell through to the ECO-WORTHY branch, so a typo produced a battery that
# simply never reported — indistinguishable from one that was out of range.
bms_driver.validate_protocols(ENTRIES)

# A battery that fails to produce a fresh reading for this many consecutive reads is
# shown as "stale" (last-known values, dimmed in the UI) instead of disappearing.
STALE_AFTER_MISSES = 1

# Give BlueZ a moment to finish the disconnects we asked for before power-cycling.
RECOVER_SETTLE_SECONDS = 2.0

# After a recovery, something should be advertising again within this long. If nothing
# is, the gentle power-cycle did not take and we escalate to a USB re-enumeration —
# an escalation that, before this was checked, had never actually run in production.
RECOVER_VERIFY_SECONDS = 120.0

# Bounds on how long the poll thread sleeps between cycles. Batteries carry their own
# intervals, so the loop wakes when the earliest one is due rather than on a fixed tick.
MIN_SLEEP_SECONDS = 1.0
MAX_SLEEP_SECONDS = 60.0

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

# Adapter-level health, published on /api/health and consulted by the health gate.
health_lock = threading.Lock()
adapter_health = {}


def log(message: str) -> None:
    """Print a poll-thread line. Always flushed.

    Unflushed ``print()`` cost this project its entire record of adapter recoveries:
    the buffered lines were still in the process when SIGTERM arrived and were lost,
    so months of power-cycles left no trace and the wrong conclusion was drawn from
    their apparent absence.
    """
    print(message, flush=True)


def _adapter_label(adapter) -> str:
    return adapter or "default adapter"


# --------------------------------------------------------------------------------
# Adapter health
# --------------------------------------------------------------------------------

class AdapterHealth:
    """Tracks adapter-level faults, as distinct from one battery misbehaving.

    The distinction matters because the two demand opposite responses. A battery that
    stops answering is that battery's problem and no amount of adapter surgery helps.
    An adapter that has stopped returning advertisements for *everything* takes every
    battery down with it, and takes their open links with it when it is reset.
    """

    KINDS = ("outage", "recovery", "stack_restart")

    def __init__(self, adapter):
        self.adapter = adapter
        self.faults = {kind: 0.0 for kind in self.KINDS}
        self.last_recovery_attempt = 0.0
        self.recovery_count = 0

    def note(self, kind: str, *, detail: str = "") -> None:
        self.faults[kind] = time.time()
        log(f"[health] {_adapter_label(self.adapter)}: {kind}"
            + (f" — {detail}" if detail else ""))

    def seconds_since_fault(self):
        """Seconds since the most recent fault of any kind, or None if never."""
        newest = max(self.faults.values())
        return None if newest == 0.0 else time.time() - newest

    def is_quiet(self, minutes: float) -> bool:
        """True if no adapter-level fault has been recorded in the last ``minutes``."""
        since = self.seconds_since_fault()
        return since is None or since >= minutes * 60

    def snapshot(self) -> dict:
        since = self.seconds_since_fault()
        return {
            "adapter": self.adapter or "default",
            "last_fault_seconds_ago": None if since is None else int(since),
            "last_outage": self.faults["outage"] or None,
            "last_recovery": self.faults["recovery"] or None,
            "last_stack_restart": self.faults["stack_restart"] or None,
            "recovery_count": self.recovery_count,
            "healthy": self.is_quiet(POLLING["health_gate_minutes"]),
        }


# --------------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------------

class Telemetry:
    """Append-only JSONL of readings and events, so a failure leaves evidence.

    Without this, everything the dashboard knows lives in ``latest_data`` and dies with
    the process — which is why the last reported state of a battery before it went
    silent (its fault code, its current, whether it was charging) has never once been
    available afterwards.
    """

    def __init__(self, path: Path, max_bytes: int = 4_000_000):
        self.path = path
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self.enabled = POLLING["telemetry_samples"] > 0
        self.recent = deque(maxlen=max(1, POLLING["telemetry_samples"]))

    def write(self, kind: str, name: str, payload: dict) -> None:
        if not self.enabled:
            return
        record = {"ts": round(time.time(), 3), "kind": kind, "battery": name, **payload}
        self.recent.append(record)
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    self.path.replace(self.path.with_suffix(".jsonl.1"))
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        except Exception as exc:  # noqa: BLE001 - telemetry must never break polling
            log(f"[telemetry] write failed: {type(exc).__name__}: {exc}")


telemetry = Telemetry(ROOT / "state" / "telemetry.jsonl")


# --------------------------------------------------------------------------------
# Release switch
# --------------------------------------------------------------------------------

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


# --------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------

def _fetch_with_retry(loop, name, entry, device, attempts):
    """Read a single battery, retrying a few times before giving up this cycle.

    Each attempt needs a fresh coroutine (a coroutine cannot be awaited twice).
    Returns ``(reading_or_None, stack_error_reason_or_None)``.

    Failures are logged rather than swallowed. Distinguishing "not advertising" from
    "connected but no data" from "the host stack just restarted" is what tells you
    whether a battery is out of range, wedged, or whether the problem is not the
    battery at all — and the last of those needs the event loop rebuilt, not a retry.
    """
    started = time.time()
    for attempt in range(attempts):
        try:
            result = loop.run_until_complete(
                bms_driver.read_battery(
                    name,
                    entry["address"],
                    entry["protocol"],
                    device=device,
                    persistent=entry["persistent"],
                    adapter=entry["adapter"],
                    scan_timeout=POLLING["scan_timeout"],
                    log=log,
                )
            )
        except Exception as exc:  # noqa: BLE001 - read_battery should not raise, but be safe
            reason = bms_driver.classify_stack_error(exc)
            log(f"[{name}] read raised: {type(exc).__name__}: {exc}")
            return None, reason

        if result:
            elapsed = time.time() - started
            log(f"[{name}] read ok in {elapsed:.1f}s (attempt {attempt + 1}/{attempts})")
            return result, None
    return None, None


# --------------------------------------------------------------------------------
# Adapter recovery
# --------------------------------------------------------------------------------

def _resolve_adapter(adapter):
    """Best-effort discovery of an adapter's (hci_index, mac) for recovery.

    Returns (0, None) if the helper library is unavailable or discovery fails;
    recover_adapter tolerates an unknown MAC, and hci0 is the near-universal default.
    """
    wanted = None
    if adapter and adapter.startswith("hci") and adapter[3:].isdigit():
        wanted = int(adapter[3:])

    if get_adapters_from_hci is None:
        return (wanted or 0), None
    try:
        found = get_adapters_from_hci()  # {hci_index: {"bdaddr": "AA:..", ...}}
    except Exception as exc:  # noqa: BLE001 - discovery is advisory only
        log(f"[recovery] could not enumerate adapters: {type(exc).__name__}: {exc}")
        return (wanted or 0), None
    if not found:
        return (wanted or 0), None

    if wanted is not None and wanted in found:
        hci = wanted
    else:
        if wanted is not None:
            log(f"[recovery] configured adapter {adapter} not found among {sorted(found)}")
        hci = 0 if 0 in found else sorted(found)[0]
    return hci, found[hci].get("bdaddr")


def _quiesce_links(loop, addresses):
    """Bring our links down *before* the adapter is power-cycled, and say if we can't.

    This matters more than it looks. ``recover_adapter`` issues no per-link disconnect
    at all, and the kernel's power-off path deliberately does not wait for the ones it
    does issue to complete — so any link still up when the adapter goes down may be
    severed without the peripheral ever being told. For a BMS module that only resumes
    advertising after a clean disconnect, that is the difference between a blip and a
    battery that needs a full discharge to revive.
    """
    try:
        loop.run_until_complete(bms_driver.close_all_sessions(log=log))
        loop.run_until_complete(bms_driver.close_stale_links(addresses, log=log))
        still_up = loop.run_until_complete(
            bms_driver.connected_addresses(addresses, log=log)
        )
    except Exception as exc:  # noqa: BLE001 - quiescing is best-effort
        log(f"[recovery] link teardown failed: {type(exc).__name__}: {exc}")
        return

    if still_up:
        log(f"[recovery] WARNING: still connected before power-cycle: {', '.join(still_up)}"
            f" — these peripherals may be stranded by the reset")
        telemetry.write("recovery_stranding_risk", "-", {"addresses": still_up})


def _recover_adapter(loop, adapter, addresses):
    """Power-cycle one BLE adapter to clear a wedged BlueZ/HCI state.

    Tries the bluetooth-auto-recovery library first, verifies the result by scanning,
    and escalates to a USB re-enumeration only if nothing is heard afterwards. Falls
    back to `systemctl restart bluetooth`. Returns True if a path reported success.
    Never raises.
    """
    if bms_driver.has_open_links():
        log(f"[recovery] closing held links before power-cycle: "
            f"{', '.join(bms_driver.held_session_names())}")
    _quiesce_links(loop, addresses)
    time.sleep(RECOVER_SETTLE_SECONDS)

    hci, mac = _resolve_adapter(adapter)

    if recover_adapter is not None:
        # Graduated recovery: a plain power-cycle clears most wedges. Escalate to
        # gone_silent=True (a USB unbind/rebind) only when the gentle attempt fails
        # *or* when it claims success but nothing is advertising afterwards. The old
        # code returned on the library's say-so, so the escalation never once ran.
        for gone_silent in (False, True):
            try:
                ok = loop.run_until_complete(
                    recover_adapter(hci, mac, gone_silent=gone_silent)
                )
                log(f"[recovery] recover_adapter(hci{hci}, {mac}, "
                    f"gone_silent={gone_silent}) -> {ok}")
            except Exception as exc:  # noqa: BLE001 - fall through / escalate
                log(f"[recovery] recover_adapter(gone_silent={gone_silent}) failed: "
                    f"{type(exc).__name__}: {exc}")
                continue

            if not ok:
                continue
            if _adapter_hears_anything(loop, adapter):
                return True
            log(f"[recovery] adapter reported recovered but heard nothing in "
                f"{RECOVER_VERIFY_SECONDS:.0f}s — escalating")

    # Fallback: the exact command proven to work by hand on this server.
    try:
        subprocess.run(
            ["systemctl", "restart", "bluetooth"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        log("[recovery] 'systemctl restart bluetooth' succeeded")
        return True
    except Exception as exc:  # noqa: BLE001 - log and let the next cycle retry
        log(f"[recovery] 'systemctl restart bluetooth' failed: "
            f"{type(exc).__name__}: {exc}")
        return False


def _adapter_hears_anything(loop, adapter) -> bool:
    """True if *any* BLE device is heard on this adapter shortly after a recovery.

    Deliberately not limited to our batteries: the question is whether the radio is
    receiving at all. A neighbour's phone counts.
    """
    deadline = time.time() + RECOVER_VERIFY_SECONDS
    while time.time() < deadline and not shutdown_event.is_set():
        try:
            heard = loop.run_until_complete(
                bms_driver.scan_all(adapter=adapter, timeout=10.0, log=log)
            )
        except Exception as exc:  # noqa: BLE001 - verification is advisory
            log(f"[recovery] verification scan failed: {type(exc).__name__}: {exc}")
            return False
        if heard:
            log(f"[recovery] {_adapter_label(adapter)} is hearing advertisements again "
                f"({len(heard)} device(s))")
            return True
    return False


# --------------------------------------------------------------------------------
# Poll loop
# --------------------------------------------------------------------------------

class _PollState:
    """Per-battery scheduling and failure bookkeeping."""

    def __init__(self, names):
        now = time.time()
        self.due = {name: now for name in names}
        self.failures = {name: 0 for name in names}
        self.misses = {name: 0 for name in names}
        self.last_seen = {name: 0.0 for name in names}
        self.last_unfiltered_scan = {name: 0.0 for name in names}

    def schedule_next(self, name, interval, *, failed, backoff_cap_minutes):
        """Set the next due time, backing off after consecutive failures.

        Backoff exists to stop a chronically unreachable battery from generating a
        continuous retry storm. Every observed host-stack crash on this system was
        preceded by minutes of exactly that, so quietening a failing battery is not
        just tidiness — it removes the conditions the crash needs.
        """
        if failed:
            self.failures[name] += 1
            factor = min(2 ** min(self.failures[name], 16), backoff_cap_minutes * 60 / max(interval, 1))
            factor = max(1.0, factor)
        else:
            self.failures[name] = 0
            factor = 1.0
        self.due[name] = time.time() + interval * factor
        return factor


def background_updater():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    labels = {name: entry["label"] for name, entry in ENTRIES.items()}
    state = _PollState(ENTRIES)

    with health_lock:
        for adapter in ADAPTERS:
            adapter_health[adapter] = AdapterHealth(adapter)

    log(f"[startup] aiobmsble {bms_driver.library_version()}, "
        f"{len(ENTRIES)} batteries on {len(ADAPTERS)} adapter(s), "
        f"interval {POLLING['interval_seconds']}s")

    # BlueZ keeps an ACL open when the owning process dies, so after a crash, a
    # `kill -9` or a `systemctl restart` the previous run's links may still be
    # attached — which both blocks our reconnect and leaves the peripheral believing
    # it is still connected. Sweep them before the first poll.
    try:
        loop.run_until_complete(bms_driver.close_stale_links(ADDRESSES, log=log))
    except Exception as exc:  # noqa: BLE001 - advisory
        log(f"[startup] stale-link sweep failed: {type(exc).__name__}: {exc}")

    def rebuild_loop(reason):
        """Replace the event loop after the host stack restarted underneath it.

        bleak keys its BlueZ manager — and that manager's D-Bus connection, device
        object cache and GATT service cache — by the running event loop, and registers
        no watcher for bluetoothd leaving the bus. So after a restart every cached path
        is stale forever and each call fails identically. A fresh loop is what actually
        clears it; nothing short of that does.
        """
        nonlocal loop
        log(f"[stack] {reason} — rebuilding event loop")
        try:
            loop.run_until_complete(bms_driver.close_all_sessions(log=log))
        except Exception as exc:  # noqa: BLE001 - the old loop is already suspect
            log(f"[stack] session teardown failed: {type(exc).__name__}: {exc}")
        with contextlib.suppress(Exception):
            loop.close()
        # The registry is module-level and outlives the loop; a driver built on the
        # closed loop would raise cross-loop errors from its internal primitives.
        bms_driver.forget_sessions()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        for adapter in ADAPTERS:
            with health_lock:
                adapter_health[adapter].note("stack_restart", detail=reason)

    try:
        while not shutdown_event.is_set():
            try:
                _poll_once(loop, state, labels, rebuild_loop)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:  # noqa: BLE001 - never let the poll thread die
                # A single bad cycle (including CancelledError, which is a BaseException
                # and would otherwise escape) must not kill the updater thread, or the
                # whole dashboard would freeze until a manual restart.
                log(f"[background_updater] cycle error: {type(exc).__name__}: {exc}")

            shutdown_event.wait(_sleep_seconds(state))
    finally:
        # Last line of defence: never leave a peripheral holding a phantom link.
        try:
            loop.run_until_complete(bms_driver.close_all_sessions(log=log))
        except Exception as exc:  # noqa: BLE001
            log(f"[shutdown] session teardown failed: {type(exc).__name__}: {exc}")
        shutdown_done.set()


def _sleep_seconds(state) -> float:
    """Sleep until the earliest battery is due, within sane bounds."""
    if not state.due:
        return MAX_SLEEP_SECONDS
    wait = min(state.due.values()) - time.time()
    return max(MIN_SLEEP_SECONDS, min(MAX_SLEEP_SECONDS, wait))


def _poll_once(loop, state, labels, rebuild_loop):
    """One pass over every adapter, reading whichever batteries are due."""
    for adapter in ADAPTERS:
        if shutdown_event.is_set():
            return
        _poll_adapter(loop, state, labels, rebuild_loop, adapter)


def _poll_adapter(loop, state, labels, rebuild_loop, adapter):
    now = time.time()
    on_adapter = batteries_on(ENTRIES, adapter)
    health = adapter_health[adapter]

    due = []
    for name, entry in on_adapter.items():
        if state.due[name] > now:
            continue
        if _released_until(name) is not None:
            # Handed to the phone app: drop the link and leave it alone.
            loop.run_until_complete(bms_driver.drop_session(name, log=log))
            state.due[name] = now + battery_interval(entry, POLLING)
            continue
        if entry["health_gated"] and not health.is_quiet(POLLING["health_gate_minutes"]):
            since = health.seconds_since_fault() or 0
            log(f"[{name}] skipped — {_adapter_label(adapter)} had a fault "
                f"{since / 60:.0f} min ago (gate: {POLLING['health_gate_minutes']} min)")
            telemetry.write("skip_health_gate", name, {"seconds_since_fault": int(since)})
            state.due[name] = now + battery_interval(entry, POLLING)
            continue
        due.append(name)

    if not due:
        return

    # One scan for the whole adapter, not one per battery.
    wanted = [on_adapter[n]["address"] for n in due]
    sightings = loop.run_until_complete(
        bms_driver.scan(wanted, adapter=adapter, timeout=POLLING["scan_timeout"], log=log)
    )

    heard = set()
    results = {}
    for name in due:
        if shutdown_event.is_set():
            return
        entry = on_adapter[name]
        sighting = sightings.get(entry["address"].upper())
        if sighting is None:
            log(f"[{name}] not advertising ({entry['address']})")
            results[name] = None
            continue
        heard.add(name)
        state.last_seen[name] = time.time()
        reading, stack_error = _fetch_with_retry(
            loop, name, entry, sighting.device, battery_attempts(entry, POLLING)
        )
        results[name] = reading
        if reading:
            reading["rssi"] = sighting.rssi
        if stack_error:
            rebuild_loop(stack_error)
            return

    _record_results(state, labels, results, adapter)
    _detect_outage(state, health, due, heard, adapter)
    _maybe_recover(loop, state, health, adapter)
    _maybe_identity_check(loop, state, due, heard, adapter)


def _record_results(state, labels, results, adapter):
    now = time.time()
    with update_lock:
        for name, result in results.items():
            entry = ENTRIES[name]
            interval = battery_interval(entry, POLLING)
            if result:
                result["label"] = labels.get(name, name)
                # Rated capacity is config, not battery data, so it is merged here
                # alongside the label rather than inside the driver.
                result["rated_ah"] = bms_driver.rated_capacity(entry, result)
                result["last_seen"] = now
                result["misses"] = 0
                result["adapter"] = adapter or "default"
                latest_data[name] = result
                state.misses[name] = 0
                telemetry.write("reading", name, {
                    k: result.get(k) for k in
                    ("voltage", "current", "soc", "capacity_ah", "temperature",
                     "problem", "problem_code", "rssi")
                })
            else:
                state.misses[name] += 1
                if name in latest_data:
                    latest_data[name]["misses"] = state.misses[name]
                telemetry.write("miss", name, {"consecutive": state.misses[name]})
            factor = state.schedule_next(
                name, interval, failed=not result,
                backoff_cap_minutes=POLLING["backoff_max_minutes"],
            )
            if not result and factor > 1.0:
                log(f"[{name}] backing off {factor:.0f}x → next read in "
                    f"{interval * factor / 60:.1f} min")


def _detect_outage(state, health, due, heard, adapter):
    """Flag a whole-adapter scan outage: every battery we expected went silent at once.

    This is the signature that preceded the ECO-WORTHY's death, and it is
    qualitatively different from one battery missing. Only batteries seen recently
    count, so a battery that is genuinely gone cannot keep re-triggering it.
    """
    expected = [
        n for n in due
        if state.last_seen[n] and
        time.time() - state.last_seen[n] < POLLING["recover_stale_minutes"] * 60
    ]
    if len(expected) >= 2 and not heard.intersection(expected):
        health.note("outage", detail=f"{len(expected)} batteries silent in one scan")
        telemetry.write("adapter_outage", "-", {
            "adapter": adapter or "default", "batteries": expected,
        })


def _maybe_recover(loop, state, health, adapter):
    """Power-cycle the adapter only when the adapter itself looks wedged.

    Two guards that the previous version lacked, both learned the hard way:

    * **A quorum, and only of batteries that were recently alive.** A battery unseen
      for longer than ``recover_stale_minutes`` ages out. Without that, one genuinely
      dead battery plus one flaky one kept the adapter power-cycling every few minutes
      indefinitely — and every one of those cycles is a chance to strand a peripheral.
    * **Never on a single battery.** One battery missing means that battery is
      missing. No adapter reset has ever fixed a dead battery, and on a dedicated
      single-battery adapter this rule means recovery can never fire at all.
    """
    now = time.time()
    on_adapter = batteries_on(ENTRIES, adapter)
    threshold = POLLING["recover_after_misses"]
    stale_after = POLLING["recover_stale_minutes"] * 60

    candidates = [
        n for n in on_adapter
        if state.last_seen[n] and now - state.last_seen[n] < stale_after
    ]
    if len(candidates) < 2:
        return
    missing = [n for n in candidates if state.misses[n] >= threshold]
    if len(missing) < len(candidates):
        return

    cooldown = POLLING["recover_cooldown_minutes"] * 60
    if now - health.last_recovery_attempt < cooldown:
        return

    log(f"[recovery] {_adapter_label(adapter)}: all {len(candidates)} recently-seen "
        f"batteries missing {threshold}+ reads — recovering")
    health.last_recovery_attempt = time.time()
    health.note("recovery", detail=f"{len(missing)} batteries missing")
    telemetry.write("recovery", "-", {"adapter": adapter or "default", "batteries": missing})

    addresses = [e["address"] for e in on_adapter.values()]
    if _recover_adapter(loop, adapter, addresses):
        health.recovery_count += 1
        for name in on_adapter:
            state.misses[name] = 0
            state.failures[name] = 0
            state.due[name] = time.time()
        with update_lock:
            for name in on_adapter:
                if name in latest_data:
                    latest_data[name]["misses"] = 0


def _maybe_identity_check(loop, state, due, heard, adapter):
    """After a battery has been missing a while, look at *everything* advertising.

    An address-filtered scan cannot tell "this battery is silent" from "this battery is
    advertising under a different address or name" — and these modules use random
    static addresses assigned in firmware and ship under several brand names. One
    unfiltered scan settles it, and costs one scan window per hour at most.
    """
    for name in due:
        if name in heard:
            continue
        last = state.last_seen[name]
        if not last or time.time() - last < 600:
            continue
        if time.time() - state.last_unfiltered_scan[name] < 3600:
            continue
        state.last_unfiltered_scan[name] = time.time()
        log(f"[{name}] missing for {(time.time() - last) / 60:.0f} min — "
            f"running one unfiltered scan to check for a changed address")
        rows = loop.run_until_complete(
            bms_driver.scan_all(adapter=adapter, timeout=POLLING["scan_timeout"], log=log)
        )
        for address, dev_name, rssi, uuids in rows:
            log(f"[{name}]   heard {address} {dev_name or '(no name)'} rssi={rssi}")
        telemetry.write("unfiltered_scan", name, {
            "heard": [{"address": r[0], "name": r[1], "rssi": r[2]} for r in rows],
        })
        return  # one per cycle is plenty


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
    # A second protocol, so the demo also covers the fields JBD does not report and
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
    with health_lock:
        adapter_health[None] = AdapterHealth(None)
    while True:
        now = time.time()
        with update_lock:
            for name, base in DEMO_BATTERIES.items():
                entry = dict(base)
                entry["last_seen"] = now
                entry["misses"] = 0
                entry["adapter"] = "default"
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
        # Every configured battery can be released, not just persistent ones. Even a
        # connect-per-read battery is briefly connected, and these modules take one
        # client at a time — so a release is still what guarantees the phone app a
        # clear window.
        entry["releasable"] = name in ENTRIES

    return jsonify(snapshot)


@app.route("/api/health")
def api_health():
    """Adapter-level health, kept separate from /api/data.

    /api/data is keyed by battery id and consumers iterate its keys, so adapter state
    cannot live there without breaking them.
    """
    with health_lock:
        snapshot = [h.snapshot() for h in adapter_health.values()]
    return jsonify({
        "adapters": snapshot,
        "library": bms_driver.library_version(),
        "held_links": bms_driver.held_session_names(),
    })


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
    log(f"[release] {name} released for {minutes:.0f} min")

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
    log(f"[release] {name} resumed")

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
            log(f"[shutdown] signal {signum} — closing BLE links")
            shutdown_event.set()
            shutdown_done.wait(timeout=15)
            raise SystemExit(0)

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, _shutdown)

    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    if demo:
        log("DEMO MODE — showing sample batteries, no Bluetooth required.")
    log(f"Dashboard running at http://{display_host}:{port}")
    log("Keep this terminal open while using the dashboard.")

    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
