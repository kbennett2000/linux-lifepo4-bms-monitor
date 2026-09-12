"""
Shared configuration loader for the LiFePO4 BMS monitor.

Loads ``config.json`` from the project root. If it is missing, falls back to
``config.example.json`` so a fresh clone still runs. Environment variables
(BMS_DASHBOARD_HOST, BMS_DASHBOARD_PORT) override the server block.
"""

import json
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_CONFIG_PATH = _ROOT / "config.json"
_EXAMPLE_PATH = _ROOT / "config.example.json"


def load_config() -> dict:
    path = _CONFIG_PATH if _CONFIG_PATH.exists() else _EXAMPLE_PATH
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if env_host := os.environ.get("BMS_DASHBOARD_HOST"):
        cfg.setdefault("server", {})["host"] = env_host
    if env_port := os.environ.get("BMS_DASHBOARD_PORT"):
        cfg.setdefault("server", {})["port"] = int(env_port)

    return cfg


def battery_tuples(cfg: dict) -> dict:
    """Return ``{name: (address, protocol)}`` for all configured batteries."""
    return {
        name: (b["address"], b["protocol"])
        for name, b in cfg.get("batteries", {}).items()
    }


def _opt_float(value):
    """Coerce to float, mapping absent/blank/zero to None ("not configured")."""
    return float(value) if value else None


def _opt_int(value):
    return int(value) if value else None


def battery_entries(cfg: dict) -> dict:
    """Return the full per-battery record, with defaults filled in.

    Per-battery keys beyond ``address`` and ``protocol``:

    ``persistent``
        Hold one BLE connection open instead of reconnecting every poll. Defaults
        to ``False``. This is a per-adapter judgement call, not a per-battery one:
        a held link is the upstream recommendation and is right on a *dedicated*
        adapter, but on an adapter that intermittently stops returning
        advertisements it guarantees the link is open when the adapter wedges.
        See ``docs/adr/0003-*`` before changing it.

    ``rated_capacity_ah``
        The capacity the battery is *sold* as, which is not what the BMS reports:
        a 50 Ah ECO-WORTHY reports 52.02 Ah full. Used as the denominator of the
        capacity readouts so a healthy pack can read above 100%. ``None`` when
        unset, which falls back to the BMS's own reported capacity.

    ``interval_seconds`` / ``attempts``
        Per-battery overrides of the matching ``polling`` values, so one fragile
        battery can be polled rarely without slowing the whole bank down.

    ``adapter``
        Which host adapter to use, e.g. ``"hci1"``. ``None`` means the default.
        Scanning, connecting and adapter recovery are all grouped by this value.

    ``health_gated``
        Skip this battery's reads for a while after an adapter-level fault
        (a whole-bank scan outage, an adapter recovery, or a bluetoothd restart).
        For a battery whose BLE module does not survive an unclean link loss,
        not connecting during a sick period is worth more than the missed reading.
    """
    return {
        name: {
            "address": b["address"],
            "protocol": b["protocol"],
            "label": b.get("label", name),
            "persistent": bool(b.get("persistent", False)),
            "rated_capacity_ah": _opt_float(b.get("rated_capacity_ah")),
            # None => inherit the corresponding polling default.
            "interval_seconds": _opt_float(b.get("interval_seconds")),
            "attempts": _opt_int(b.get("attempts")),
            "adapter": b.get("adapter") or None,
            "health_gated": bool(b.get("health_gated", False)),
        }
        for name, b in cfg.get("batteries", {}).items()
    }


def adapters(entries: dict) -> list:
    """Distinct adapters in use, in config order. ``None`` = the default adapter."""
    seen = []
    for entry in entries.values():
        if entry["adapter"] not in seen:
            seen.append(entry["adapter"])
    return seen


def batteries_on(entries: dict, adapter) -> dict:
    """The subset of ``entries`` bound to one adapter."""
    return {n: e for n, e in entries.items() if e["adapter"] == adapter}


# Defaults for the optional "polling" block in config.json.
#
# `interval_seconds` is 30 rather than the original 10 to match the reference
# deployment of these same drivers (Home Assistant's bms_ble polls every 30 s).
# Polling three batteries every 10 s, each with its own active scan, is a large
# continuous load on one adapter and this bank's adapter does not tolerate it.
_POLLING_DEFAULTS = {
    "interval_seconds": 30,          # pause between poll cycles
    "attempts": 2,                   # tries per battery per cycle before counting a miss
    "scan_timeout": 8.0,             # seconds to look for advertisements
    "release_minutes": 5,            # default hold-off when releasing to a phone app
    "release_max_minutes": 30,       # clamp, so a bad request can't release indefinitely
    "recover_after_misses": 3,       # consecutive misses before a battery counts as missing
    "recover_cooldown_minutes": 30,  # minimum gap between adapter recovery attempts
    "recover_stale_minutes": 60,     # a battery unseen this long stops counting toward recovery
    "health_gate_minutes": 15,       # quiet period required after an adapter fault
    "backoff_max_minutes": 10,       # ceiling on per-battery failure backoff
    "telemetry_samples": 500,        # per-battery samples retained on disk (0 disables)
}


def polling_config(cfg: dict) -> dict:
    """Return the polling/tuning block, with every key defaulted."""
    block = cfg.get("polling", {})
    resolved = dict(_POLLING_DEFAULTS)
    for key, default in _POLLING_DEFAULTS.items():
        if key in block:
            resolved[key] = type(default)(block[key])
    return resolved


def battery_interval(entry: dict, polling: dict) -> float:
    """Seconds between reads for one battery (its override, else the default)."""
    return float(entry.get("interval_seconds") or polling["interval_seconds"])


def battery_attempts(entry: dict, polling: dict) -> int:
    """Read attempts per cycle for one battery (its override, else the default)."""
    return int(entry.get("attempts") or polling["attempts"])
