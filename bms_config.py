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


def battery_entries(cfg: dict) -> dict:
    """Return the full per-battery record, with defaults filled in.

    ``persistent`` asks the driver layer to hold one BLE connection open for that
    battery instead of reconnecting on every poll. It defaults to ``False`` so an
    existing ``config.json`` behaves exactly as before.
    """
    return {
        name: {
            "address": b["address"],
            "protocol": b["protocol"],
            "label": b.get("label", name),
            "persistent": bool(b.get("persistent", False)),
        }
        for name, b in cfg.get("batteries", {}).items()
    }


# Defaults chosen to match the previously hard-coded behaviour, so adding the optional
# "polling" block to config.json is the only way to change any of them.
_POLLING_DEFAULTS = {
    "interval_seconds": 10,     # pause between poll cycles
    "attempts": 2,              # tries per battery per cycle before counting a miss
    "scan_timeout": 8.0,        # seconds to look for a battery's advertisement
    "release_minutes": 5,       # default hold-off when releasing a battery to a phone app
    "release_max_minutes": 30,  # clamp, so a bad request can't release it indefinitely
}


def polling_config(cfg: dict) -> dict:
    """Return the polling/tuning block, with every key defaulted."""
    block = cfg.get("polling", {})
    resolved = dict(_POLLING_DEFAULTS)
    for key, default in _POLLING_DEFAULTS.items():
        if key in block:
            resolved[key] = type(default)(block[key])
    return resolved
