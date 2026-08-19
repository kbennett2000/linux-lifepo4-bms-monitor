"""
Shared BLE driver layer for the LiFePO4 BMS monitor.
====================================================

Every battery read in this project goes through :func:`read_battery`. It owns three
things that used to be copy-pasted (and subtly different) in ``dashboard.py``,
``battery_monitor.py`` and ``battery_widget.py``:

1. **Protocol dispatch** — maps the ``protocol`` string from ``config.json`` onto an
   ``aiobmsble`` driver class.
2. **Connection lifecycle** — optionally holds one long-lived connection per battery
   instead of reconnecting on every poll, and always tears a connection down
   *gracefully*.
3. **Field mapping** — turns an ``aiobmsble`` ``BMSSample`` into the flat dict the
   dashboard JSON API and the terminal/tray views expect.

Why the graceful teardown matters
---------------------------------
BlueZ does **not** close an ACL connection when the owning process dies — see the
comment in ``bleak/backends/bluezdbus/client.py`` referencing bluez/bluez#89. A BLE
peripheral that never receives a link-layer terminate keeps believing it is connected,
and a connected peripheral stops advertising. Some BMS modules never time that state
out, so they vanish from scans until their MCU is power-cycled. Every disconnect here
therefore unsubscribes first, runs from a ``finally``, and is logged when it fails.
:func:`close_stale_links` cleans up after a previous process that did not get the
chance.
"""

import asyncio
import contextlib
import importlib
from dataclasses import dataclass
from functools import lru_cache

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from aiobmsble.basebms import BaseBMS

# `config.json` uses short, friendly protocol names. Anything not listed here is passed
# through to `aiobmsble.bms.<name>_bms`, so the other ~40 drivers that ship with the
# library (daly, jikong, seplos, …) can be selected without touching this file.
PROTOCOL_ALIASES = {
    "jbd": "jbd_bms",
    "ecoworthy": "ecoworthy_bms",
}

# How long to look for a battery's advertisement before giving up on this attempt.
SCAN_TIMEOUT = 8.0

# Hard ceiling on a single read. aiobmsble retries internally across both GATT write
# modes with escalating timeouts, so an unresponsive battery can otherwise stall a poll
# cycle for the better part of a minute and hold up every other battery behind it.
READ_TIMEOUT = 45.0


class UnknownProtocolError(ValueError):
    """Raised when config.json names a protocol with no matching aiobmsble driver."""


@lru_cache(maxsize=None)
def resolve_bms_class(protocol: str) -> type[BaseBMS]:
    """Return the aiobmsble driver class for a ``config.json`` protocol string.

    Resolution is deliberately strict: an unrecognised protocol raises instead of
    silently falling back to a default. The previous ``if proto == "jbd" … else
    ecoworthy`` dispatch meant a typo sent one BMS's command set to a completely
    different BMS's parser.
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
    """Resolve every configured protocol up front so a typo fails loudly at startup.

    Without this a bad protocol name would surface as a battery that simply never
    reports, which looks identical to a battery that is out of range.
    """
    for name, entry in entries.items():
        try:
            resolve_bms_class(entry["protocol"])
        except UnknownProtocolError as exc:
            raise UnknownProtocolError(f"battery {name!r}: {exc}") from exc


# --------------------------------------------------------------------------------
# Persistent session registry
# --------------------------------------------------------------------------------

@dataclass
class _Session:
    """One held BLE connection, reused across poll cycles."""
    bms: BaseBMS
    address: str


# Keyed by the battery name from config.json. Only persistent batteries appear here.
_SESSIONS: dict[str, _Session] = {}


def held_session_names() -> list[str]:
    """Names of batteries currently holding an open connection."""
    return [name for name, s in _SESSIONS.items() if s.bms.is_connected]


def has_open_links() -> bool:
    """True if any battery is currently holding an open BLE connection."""
    return bool(held_session_names())


async def _graceful_disconnect(bms: BaseBMS, *, log=print) -> None:
    """Unsubscribe, then disconnect, never raising.

    ``BaseBMS.disconnect()`` only calls ``BleakClient.disconnect()`` — despite its
    docstring it does not stop notifications — so we write the CCCD back to 0x0000
    ourselves first. That reaches into ``bms._client``, the one private attribute this
    module touches; it is suppressed so a future aiobmsble refactor degrades to "no
    explicit unsubscribe" rather than breaking teardown entirely.
    """
    if not bms.is_connected:
        return

    with contextlib.suppress(Exception):
        await bms._client.stop_notify(bms.uuid_rx())  # noqa: SLF001 - see docstring

    try:
        # reset=True clears the negotiated GATT write mode so a later reconnect
        # re-detects write-with-response vs write-without-response from scratch.
        await bms.disconnect(reset=True)
    except Exception as exc:  # noqa: BLE001 - teardown must never raise
        log(f"[ble] disconnect failed: {type(exc).__name__}: {exc}")


async def drop_session(name: str, *, log=print) -> None:
    """Gracefully close and forget one battery's held connection."""
    session = _SESSIONS.pop(name, None)
    if session is not None:
        await _graceful_disconnect(session.bms, log=log)


async def close_all_sessions(*, log=print) -> None:
    """Close every held connection. Used on shutdown and before adapter recovery."""
    for name in list(_SESSIONS):
        await drop_session(name, log=log)


async def close_stale_links(addresses, *, log=print) -> None:
    """Ask BlueZ to drop any connection it still holds to our batteries.

    BlueZ keeps an ACL open when the owning process dies, so after a crash, a ``kill
    -9`` or a ``systemctl restart`` the previous incarnation's links can still be
    attached. Run this at startup and before power-cycling the adapter.
    """
    try:
        from bleak_retry_connector import close_stale_connections_by_address
    except ImportError:
        return

    for address in dict.fromkeys(addresses):
        try:
            await close_stale_connections_by_address(address)
        except Exception as exc:  # noqa: BLE001 - advisory cleanup only
            log(f"[ble] stale-link sweep failed for {address}: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------

async def _find_device(address: str, timeout: float) -> BLEDevice | None:
    """Locate a battery's advertisement so we can connect to it."""
    return await BleakScanner.find_device_by_address(address, timeout=timeout)


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

    Values not read from the BMS directly are still trustworthy: ``BaseBMS``
    back-fills ``cycle_charge``, ``cycle_capacity``, ``runtime`` and friends from the
    fields a protocol *does* report, and only when their inputs are present.
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


def rated_capacity(entry: dict, reading: dict) -> float | None:
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


async def read_battery(
    name: str,
    address: str,
    protocol: str,
    *,
    persistent: bool = False,
    scan_timeout: float = SCAN_TIMEOUT,
    read_timeout: float = READ_TIMEOUT,
    log=print,
) -> dict | None:
    """Read one battery, reusing a held connection when ``persistent`` is set.

    Returns the reading dict, or ``None`` if the battery could not be read this time.
    Raises only on programmer error (an unknown protocol); BLE failures are reported as
    ``None`` after being logged, because a missed read is normal and the caller's retry
    and staleness logic already handles it.
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
            await drop_session(name, log=log)
    elif session is not None:
        # Stale entry (link dropped, or the address changed in config) — clear it out.
        await drop_session(name, log=log)

    # --- Slow path: find the battery and connect ---
    device = await _find_device(address, scan_timeout)
    if device is None:
        log(f"[{name}] not found in scan ({address})")
        return None

    # Always construct with keep_alive=True, even for non-persistent batteries.
    # BaseBMS.async_update() self-disconnects when keep_alive is False, which would run
    # before our stop_notify. Owning the teardown here gives every protocol one code
    # path and guarantees the unsubscribe actually happens.
    bms = bms_class(ble_device=device, keep_alive=True)

    keep_open = False
    try:
        data = await asyncio.wait_for(bms.async_update(), read_timeout)
        reading = sample_to_reading(address, data)
        keep_open = persistent
        if keep_open:
            _SESSIONS[name] = _Session(bms=bms, address=address)
        return reading
    except Exception as exc:  # noqa: BLE001 - a failed read is a miss, not a crash
        log(f"[{name}] read failed: {type(exc).__name__}: {exc}")
        return None
    finally:
        if not keep_open:
            await _graceful_disconnect(bms, log=log)
