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
