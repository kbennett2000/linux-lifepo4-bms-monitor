# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-06-22

First tagged release. The project is feature-complete and in daily use.

### Added
- **`--demo` mode** for `dashboard.py` and `battery_monitor.py` — renders realistic
  sample batteries with no Bluetooth or hardware required. Great for trying the UI,
  developing offline, and generating screenshots.
- **Project banner, dashboard screenshots (light + dark), and a terminal screenshot**,
  with reproducible sources under `assets/src/`.
- **`CONTRIBUTING.md`** and this **`CHANGELOG.md`**.
- **`.gitignore`** for Python build artifacts and virtualenvs.

### Features (pre-1.0, summarized)
- Real-time Bluetooth LE monitoring of multiple LiFePO4 batteries.
- Three interfaces: **web dashboard** (`dashboard.py`), **terminal monitor**
  (`battery_monitor.py`), and an Ubuntu **system-tray widget** (`battery_widget.py`).
- Support for **JBD / Jiabaida / Daly / Overkill** (standard JBD protocol) and a
  reverse-engineered **ECO-WORTHY** protocol.
- Per-battery metrics: voltage, current, power, SOC, temperature, per-cell voltages,
  ΔV, and charge cycles, plus a roll-up summary across the bank.
- **In-process BLE-adapter recovery**: a watchdog power-cycles a wedged adapter
  (graduating to a USB reset, falling back to `systemctl restart bluetooth`) instead of
  requiring a manual restart — see
  [docs/adr/0001](docs/adr/0001-ble-battery-disappearance-recovery.md). Missing
  batteries are retained as **stale** cards rather than disappearing.
- **Light/dark theme**, mobile-friendly responsive UI.
- **100% offline** — all assets vendored locally; no cloud, CDN, or telemetry.
- Read-only **JSON API** (`/api/data`, `/api/config`) for integrations.
- Headless-server friendly: configurable host/port (CLI flags + env vars) and a
  documented systemd service with auto-restart.

[Unreleased]: https://github.com/kbennett2000/linux-lifepo4-bms-monitor/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/kbennett2000/linux-lifepo4-bms-monitor/releases/tag/v1.0.0
