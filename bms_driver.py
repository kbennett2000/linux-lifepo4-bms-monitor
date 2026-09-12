"""
Shared BLE driver layer for the LiFePO4 BMS monitor.
====================================================

Every battery read in this project goes through :func:`read_battery`. This module owns
protocol dispatch, the connection lifecycle, scanning, and the mapping from an
``aiobmsble`` ``BMSSample`` onto the flat dict the dashboard, terminal monitor and tray
widget consume.

Three things here exist because of specific, observed failures. Do not simplify them
away without reading ``docs/adr/0003-ecoworthy-link-exposure.md``.

**Scanning must prove a device is really advertising.** ``BleakScanner.find_device_by_address``
on the BlueZ backend is signal-driven: *any* ``org.bluez.Device1`` PropertiesChanged
signal — including an RSSI invalidation, or a ``Connected`` change, or the burst of
InterfacesAdded that a restarting ``bluetoothd`` emits for every device it reloads from
storage — is treated as "this device just advertised". So it happily returns a device
that has been silent for hours. :func:`scan` instead requires a real advertisement
(``rssi != -127``, bleak's sentinel for "BlueZ has no RSSI property for this object"),
which is the difference between connecting to a battery and connecting to a ghost.

**Teardown must be bounded and verified.** A ``Disconnect`` that returns without error
only proves BlueZ's bookkeeping completed. :func:`_graceful_disconnect` therefore has its
own timeout, re-checks ``is_connected`` afterwards, falls back to BlueZ's own stale-link
cleanup, and logs loudly when a link will not come down — a link we cannot close is
exactly the condition that strands a peripheral.

**A ``bluetoothd`` restart poisons the client.** bleak registers no ``NameOwnerChanged``
watcher, so after the daemon restarts its D-Bus object and service caches are stale
forever and every subsequent call fails with ``UnknownObject``. :func:`classify_stack_error`
recognises that state so the caller can rebuild the event loop, which is what actually
clears it (bleak keys its manager by running loop).
"""

import asyncio
import contextlib
import importlib
import inspect
from dataclasses import dataclass
from functools import lru_cache

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from aiobmsble.basebms import BaseBMS

# `config.json` uses short, friendly protocol names. Anything not listed here is passed
# through to `aiobmsble.bms.<name>_bms`, so the other ~45 drivers that ship with the
# library (daly, jikong, seplos, …) can be selected without touching this file.
PROTOCOL_ALIASES = {
    "jbd": "jbd_bms",
    "ecoworthy": "ecoworthy_bms",
}

# How long to look for advertisements before giving up on this cycle.
SCAN_TIMEOUT = 8.0

# Hard ceiling on a single read, and separately on a single teardown. aiobmsble retries
# internally across both GATT write modes with escalating timeouts, so an unresponsive
# battery can otherwise stall the poll thread for a minute or more. The teardown budget is
# separate because it used to sit *outside* the read timeout entirely.
READ_TIMEOUT = 45.0
DISCONNECT_TIMEOUT = 10.0

# bleak's stand-in when BlueZ has no RSSI property for a device object. A device reported
# at exactly this value was never actually heard from — it is a cache entry, not a sighting.
NO_RSSI = -127


class UnknownProtocolError(ValueError):
    """Raised when config.json names a protocol with no matching aiobmsble driver."""


@lru_cache(maxsize=None)
def resolve_bms_class(protocol: str) -> type[BaseBMS]:
    """Return the aiobmsble driver class for a ``config.json`` protocol string.

    Resolution is deliberately strict: an unrecognised protocol raises instead of
    silently falling back to a default, because a typo used to mean one BMS's command
    set was sent to a completely different BMS's parser.
    """
    name = (protocol or "").strip().lower()
    if not name:
        raise UnknownProtocolError("battery is missing a 'protocol' value")

    module_name = PROTOCOL_ALIASES.get(name, name)
    if not module_name.endswith("_bms"):
        module_name = f"{module_name}_bms"

    try:
        module = importlib.import_module(f"aiobmsble.bms.{module_name}")
    except ModuleNotFoundError as exc:
        raise UnknownProtocolError(
            f"unknown protocol {protocol!r} (looked for aiobmsble.bms.{module_name}). "
            f"Known short names: {', '.join(sorted(PROTOCOL_ALIASES))}."
        ) from exc

    return module.BMS


def validate_protocols(entries: dict) -> None:
    """Resolve every configured protocol up front so a typo fails loudly at startup."""
    for name, entry in entries.items():
        try:
            resolve_bms_class(entry["protocol"])
        except UnknownProtocolError as exc:
            raise UnknownProtocolError(f"battery {name!r}: {exc}") from exc


# --------------------------------------------------------------------------------
# aiobmsble compatibility
# --------------------------------------------------------------------------------
# 0.28 replaced the `keep_alive=` keyword with a `BMSConfig` object. The project pins
# >=0.28, but the constructor is resolved at runtime so a host still on an older
# release keeps working instead of failing with an opaque TypeError on every read.

@lru_cache(maxsize=None)
def _ctor_style(bms_class: type[BaseBMS]) -> str:
    params = inspect.signature(bms_class.__init__).parameters
    if "config" in params:
        return "config"
    if "keep_alive" in params:
        return "keep_alive"
    return "plain"


def _make_bms(bms_class: type[BaseBMS], device: BLEDevice, keep_alive: bool) -> BaseBMS:
    """Construct a driver, whichever aiobmsble generation is installed.

    ``keep_alive`` is always True here, even for connect-per-read batteries:
    ``BaseBMS.async_update()`` self-disconnects when it is False, which would run
    *before* our own teardown and leave us unable to bound or verify it. Owning the
    teardown gives every protocol one code path.
    """
    style = _ctor_style(bms_class)
    if style == "config":
        from aiobmsble import BMSConfig  # noqa: PLC0415 - optional, version-dependent

        return bms_class(ble_device=device, config=BMSConfig(keep_alive=keep_alive))
    if style == "keep_alive":
        return bms_class(ble_device=device, keep_alive=keep_alive)
    return bms_class(ble_device=device)


def library_version() -> str:
    """Installed aiobmsble version, for logging and the health endpoint."""
    try:
        from aiobmsble import __version__  # noqa: PLC0415

        return str(__version__)
    except Exception:  # noqa: BLE001 - informational only
        return "unknown"


# --------------------------------------------------------------------------------
# Error classification
# --------------------------------------------------------------------------------

# Substrings that mean "the host Bluetooth stack restarted or went away underneath us",
# taken verbatim from the failures in this system's journal. After any of these, bleak's
# cached D-Bus object paths and GATT service cache are stale for the life of the event
# loop, and every later call fails the same way until the loop is rebuilt.
_STACK_ERROR_SIGNATURES = (
    ("org.freedesktop.DBus.Error.NoReply", "bluetoothd stopped replying"),
    ("disconnected from message bus", "bluetoothd left the bus"),
    ("org.freedesktop.DBus.Error.ServiceUnknown", "bluetoothd is not on the bus"),
    ("org.freedesktop.DBus.Error.UnknownObject", "stale D-Bus object (bluetoothd restarted)"),
    ("org.freedesktop.DBus.Error.UnknownMethod", "stale D-Bus object (bluetoothd restarted)"),
    ("org.bluez was not provided", "bluetoothd is not running"),
)


def classify_stack_error(exc: BaseException) -> str | None:
    """Return a reason string if ``exc`` indicates the host stack restarted, else None.

    Used to decide whether to rebuild the asyncio event loop. Rebuilding is the only
    thing that clears bleak's poisoned caches, because it keys its BlueZ manager (and
    that manager's D-Bus connection) by the running loop.
    """
    text = f"{type(exc).__name__}: {exc}"
    for needle, reason in _STACK_ERROR_SIGNATURES:
        if needle in text:
            return reason
    return None


# --------------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------------

@dataclass
class Sighting:
    """One battery genuinely heard from during a scan."""
    device: BLEDevice
    rssi: int
    name: str | None


def _bluez_args(adapter: str | None) -> dict:
    return {"adapter": adapter} if adapter else {}


async def scan(
    addresses,
    *,
    adapter: str | None = None,
    timeout: float = SCAN_TIMEOUT,
    log=print,
) -> dict:
    """Listen once for advertisements from ``addresses``; return ``{address: Sighting}``.

    One scan serves the whole bank on an adapter, instead of one 8-second scan per
    battery per cycle. Returns early once every wanted address has been heard.

    Only devices that produced a real advertisement are returned — see this module's
    docstring on why a cached BlueZ device object is not evidence that a battery is
    reachable, and why connecting to one is actively harmful.
    """
    wanted = {a.upper() for a in addresses}
    if not wanted:
        return {}

    found: dict[str, Sighting] = {}
    done = asyncio.Event()

    def _on_detect(device: BLEDevice, adv) -> None:
        address = (device.address or "").upper()
        if address not in wanted or address in found:
            return
        if adv.rssi == NO_RSSI:
            # A BlueZ cache entry, not a sighting. Ignore it.
            return
        found[address] = Sighting(device=device, rssi=adv.rssi, name=adv.local_name)
        if len(found) == len(wanted):
            done.set()

    try:
        async with BleakScanner(
            detection_callback=_on_detect, bluez=_bluez_args(adapter)
        ):
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), timeout)
    except Exception as exc:  # noqa: BLE001 - a failed scan is a miss, not a crash
        log(f"[scan] failed on {adapter or 'default adapter'}: {type(exc).__name__}: {exc}")

    return found


async def scan_all(*, adapter: str | None = None, timeout: float = SCAN_TIMEOUT, log=print) -> list:
    """Unfiltered scan: every device heard, as ``(address, name, rssi, uuids)`` tuples.

    Used when a battery has been missing for a while, to answer a question an
    address-filtered lookup structurally cannot: is it advertising under a *different*
    address or name? These modules use random static addresses assigned in firmware and
    ship under several brand names, so that is a real possibility worth ruling out
    rather than assuming.
    """
    seen: dict[str, tuple] = {}

    def _on_detect(device: BLEDevice, adv) -> None:
        if adv.rssi == NO_RSSI:
            return
        seen[(device.address or "").upper()] = (
            device.address,
            adv.local_name or device.name,
            adv.rssi,
            tuple(adv.service_uuids or ()),
        )

    try:
        async with BleakScanner(
            detection_callback=_on_detect, bluez=_bluez_args(adapter)
        ):
            await asyncio.sleep(timeout)
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        log(f"[scan] unfiltered scan failed: {type(exc).__name__}: {exc}")

    return sorted(seen.values(), key=lambda row: -row[2])


# --------------------------------------------------------------------------------
# Session registry
# --------------------------------------------------------------------------------

@dataclass
class _Session:
    """One held BLE connection, reused across poll cycles."""
    bms: BaseBMS
    address: str


# Keyed by the battery name from config.json. Only persistent batteries appear here.
_SESSIONS: dict[str, _Session] = {}


def held_session_names() -> list:
    """Names of batteries currently holding an open connection."""
    return [name for name, s in _SESSIONS.items() if s.bms.is_connected]


def has_open_links() -> bool:
    """True if any battery is currently holding an open BLE connection."""
    return bool(held_session_names())


def forget_sessions() -> None:
    """Drop every session record without touching BLE.

    Called after the event loop is rebuilt. The registry is module-level and outlives a
    loop, so a driver built on a closed loop would otherwise stay registered and raise
    cross-loop errors from its internal asyncio primitives on the next read.
    """
    _SESSIONS.clear()


async def _graceful_disconnect(bms: BaseBMS, *, reset: bool = False, log=print) -> bool:
    """Close one connection, bounded and verified. Returns True if the link is down.

    ``reset`` is for teardown *after a failure*: it makes aiobmsble re-probe the GATT
    write mode on the next connection and close any stale link BlueZ still holds. It is
    deliberately off for a normal teardown, because resetting the write mode forces a
    full re-probe (up to twelve GATT writes) on the following read of a battery whose
    write mode never changes.

    This does not call ``stop_notify``. Notification subscriptions do not persist across
    connections for an unbonded peer, so it achieved nothing, and StopNotify immediately
    followed by Disconnect on a link BlueZ is already tearing down is the reentrant
    sequence implicated in the ``bluetoothd`` use-after-free this system was hitting.
    """
    if not bms.is_connected:
        return True

    try:
        await asyncio.wait_for(bms.disconnect(reset=reset), DISCONNECT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - teardown must never raise
        log(f"[ble] disconnect failed: {type(exc).__name__}: {exc}")

    if not bms.is_connected:
        return True

    # BlueZ still believes the link is up. Ask it directly to drop the connection; a
    # link we cannot close is the exact condition that leaves a peripheral believing a
    # central is still attached, so this is worth a loud line in the journal.
    log(f"[ble] link to {bms._ble_device.address} did not close — forcing stale cleanup")  # noqa: SLF001
    await close_stale_links([bms._ble_device.address], log=log)  # noqa: SLF001
    if bms.is_connected:
        log(f"[ble] WARNING: {bms._ble_device.address} is still connected after cleanup")  # noqa: SLF001
        return False
    return True


async def drop_session(name: str, *, reset: bool = False, log=print) -> None:
    """Gracefully close and forget one battery's held connection."""
    session = _SESSIONS.pop(name, None)
    if session is not None:
        await _graceful_disconnect(session.bms, reset=reset, log=log)


async def close_all_sessions(*, log=print) -> None:
    """Close every held connection. Used on shutdown and before adapter recovery."""
    for name in list(_SESSIONS):
        await drop_session(name, log=log)


async def close_stale_links(addresses, *, log=print) -> None:
    """Ask BlueZ to drop any connection it still holds to our batteries.

    BlueZ keeps an ACL open when the owning process dies, so after a crash, a ``kill -9``
    or a ``systemctl restart`` the previous run's links can still be attached. Run this
    at startup and before power-cycling the adapter.
    """
    try:
        from bleak_retry_connector import close_stale_connections_by_address  # noqa: PLC0415
    except ImportError:
        return

    for address in dict.fromkeys(addresses):
        try:
            await close_stale_connections_by_address(address)
        except Exception as exc:  # noqa: BLE001 - advisory cleanup only
            log(f"[ble] stale-link sweep failed for {address}: {type(exc).__name__}: {exc}")


async def connected_addresses(addresses, *, log=print) -> list:
    """Which of ``addresses`` BlueZ currently reports as connected.

    Used before an adapter power-cycle: the power-cycle itself issues no per-link
    disconnect, and the kernel's power-off path does not wait for one to complete, so
    anything still connected at that moment is at risk of being stranded.
    """
    try:
        from bleak_retry_connector import get_device  # noqa: PLC0415
    except ImportError:
        return []

    still_up = []
    for address in dict.fromkeys(addresses):
        try:
            device = await get_device(address)
        except Exception as exc:  # noqa: BLE001 - advisory only
            log(f"[ble] could not query {address}: {type(exc).__name__}: {exc}")
            continue
        if device is not None and (device.details or {}).get("props", {}).get("Connected"):
            still_up.append(address)
    return still_up


# --------------------------------------------------------------------------------
# Field mapping
# --------------------------------------------------------------------------------

def sample_to_reading(address: str, data: dict) -> dict:
    """Flatten an aiobmsble ``BMSSample`` into the dict the UIs consume.

    The null-vs-zero rule here is load-bearing and must not be "tidied up":

    * ``soc`` falls back to ``0``, never ``None`` — ``dashboard.js`` interpolates it
      straight into the SOC ring, so a null would render the text "null" and force the
      low-charge red accent.
    * **Every other scalar falls back to ``None``, never ``0``.** The UI renders "—" for
      absent values, so a ``0`` default would invent readings a protocol does not
      actually report — a fabricated "Cycles: 0" for ECO-WORTHY (which has no cycle
      count), or a "SoH 0%" for JBD (which does not report health) reading as a dead
      pack rather than as missing data.
    * ``problem`` is the one exception, and is a genuine bool: ``BaseBMS`` always sets
      it, deriving it from ``problem_code`` plus its own sanity checks, so ``False``
      here means "the BMS says it is fine", not "unknown".
    """
    def rounded(value, digits):
        return None if value is None else round(value, digits)

    cells = [round(v, 3) for v in (data.get("cell_voltages") or [])]

    # Prefer the pack-voltage register, but fall back to the sum of the cells so a
    # missing or zero register can never render as "0.00 V".
    voltage = data.get("voltage")
    if not voltage and cells:
        voltage = sum(cells)

    delta_v = data.get("delta_voltage")

    return {
        "address": address,
        "voltage": rounded(voltage, 2),
        "current": rounded(data.get("current"), 2),
        "power": rounded(data.get("power"), 1),
        # Never None: dashboard.js interpolates this straight into the SOC ring.
        "soc": data.get("battery_level") if data.get("battery_level") is not None else 0,
        "temperature": rounded(data.get("temperature"), 1),
        "delta_mv": rounded(delta_v * 1000 if delta_v is not None else None, 1),
        "cycles": data.get("cycles"),
        "cells": cells,

        # --- Capacity ---
        # `cycle_charge` is a real coulomb-counted register on JBD, but on ECO-WORTHY it
        # is derived by BaseBMS as design_capacity * SOC/100 — it tracks the SOC exactly
        # and carries no extra information. See the README before over-trusting it.
        "capacity_ah": rounded(data.get("cycle_charge"), 1),
        # The BMS's own idea of a full pack. Both drivers floor-divide this by 100, so
        # it lands on whole amp-hours: an ECO-WORTHY reporting 52.02 Ah reads as 52.
        "capacity_full_ah": data.get("design_capacity"),
        # Whole watt-hours: round(x, 0) would leave a float, and "721.0 Wh" in the API
        # implies a precision the underlying reading does not have.
        "energy_wh": (
            None if data.get("cycle_capacity") is None
            else round(data["cycle_capacity"])
        ),

        # --- Health / detail ---
        "soh": rounded(data.get("battery_health"), 1),
        # Only derived while discharging; None means "charging, idle, or not reported".
        "runtime_seconds": data.get("runtime"),
        "problem": bool(data.get("problem", False)),
        "problem_code": data.get("problem_code"),
        "chrg_mosfet": data.get("chrg_mosfet"),
        "dischrg_mosfet": data.get("dischrg_mosfet"),
        "balancer": data.get("balancer"),
        # Individual sensors; `temperature` above is their mean.
        "temps": [round(v, 1) for v in (data.get("temp_values") or [])],
    }


def rated_capacity(entry: dict, reading: dict):
    """Rated Ah for a battery: the config value, else the BMS's own design capacity.

    Kept here rather than in each UI so the dashboard, terminal monitor and tray widget
    can never disagree about what a capacity percentage is measured against. Returns
    ``None`` when neither source knows, which the UIs render as "no capacity data"
    rather than guessing.
    """
    configured = entry.get("rated_capacity_ah")
    if configured:
        return float(configured)
    reported = reading.get("capacity_full_ah")
    return float(reported) if reported else None


# --------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------

async def read_battery(
    name: str,
    address: str,
    protocol: str,
    *,
    device: BLEDevice | None = None,
    persistent: bool = False,
    adapter: str | None = None,
    scan_timeout: float = SCAN_TIMEOUT,
    read_timeout: float = READ_TIMEOUT,
    log=print,
):
    """Read one battery, reusing a held connection when ``persistent`` is set.

    ``device`` is a ``BLEDevice`` from this cycle's shared :func:`scan`. Passing one is
    strongly preferred: it means we only ever connect to a battery we have just heard
    advertise, and it replaces a per-battery scan with one scan for the whole bank. When
    it is omitted a scan is run here, which is fine for the one-shot terminal and tray
    views but is not what the dashboard should do.

    Returns the reading dict, or ``None`` if the battery could not be read this time.
    Raises only on programmer error (an unknown protocol); BLE failures are logged and
    reported as ``None``, because a missed read is normal and the caller's retry and
    staleness logic already handles it.
    """
    bms_class = resolve_bms_class(protocol)

    # --- Fast path: we already hold a live connection to this battery ---
    session = _SESSIONS.get(name)
    if session is not None and session.address == address and session.bms.is_connected:
        try:
            data = await asyncio.wait_for(session.bms.async_update(), read_timeout)
            return sample_to_reading(address, data)
        except Exception as exc:  # noqa: BLE001 - fall through to a fresh connection
            log(f"[{name}] held connection failed: {type(exc).__name__}: {exc}")
            # reset=True: this link misbehaved, so clear the negotiated write mode and
            # let the library clean up anything BlueZ still holds for it.
            await drop_session(name, reset=True, log=log)
    elif session is not None:
        # Stale entry (link dropped, or the address changed in config) — clear it out.
        await drop_session(name, log=log)

    # --- Slow path: find the battery and connect ---
    if device is None:
        sighting = (await scan([address], adapter=adapter, timeout=scan_timeout, log=log)).get(
            address.upper()
        )
        if sighting is None:
            log(f"[{name}] not advertising ({address})")
            return None
        device = sighting.device

    bms = _make_bms(bms_class, device, keep_alive=True)

    keep_open = False
    failed = False
    try:
        data = await asyncio.wait_for(bms.async_update(), read_timeout)
        reading = sample_to_reading(address, data)
        keep_open = persistent
        if keep_open:
            _SESSIONS[name] = _Session(bms=bms, address=address)
        return reading
    except Exception as exc:  # noqa: BLE001 - a failed read is a miss, not a crash
        failed = True
        log(f"[{name}] read failed: {type(exc).__name__}: {exc}")
        return None
    finally:
        if not keep_open:
            await _graceful_disconnect(bms, reset=failed, log=log)
