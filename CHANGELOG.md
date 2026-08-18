# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **ECO-WORTHY batteries no longer get their BLE module wedged.** The ECO-WORTHY path was
  writing JBD protocol frames (`dd a5 03 …`) at a BMS that speaks CRC-checked MODBUS, and
  reconnecting 1,500–3,000 times a day with no `stop_notify` and no reliable disconnect.
  Some modules ended up believing a link was still open — and a connected peripheral stops
  advertising, recoverable only by discharging the pack to cutoff and recharging. See
  [ADR 0002](docs/adr/0002-ecoworthy-persistent-connection.md).
- Adapter recovery no longer fires when a **single** battery is missing. It required only
  `max(misses)`, so one flaky pack power-cycled the adapter for the whole bank — tearing down
  the healthy packs' links in the process. It now needs a quorum.
- The poll loop's event loop is recreated after an adapter recovery, which restarts bluetoothd
  underneath it and invalidates the D-Bus connection `bleak` holds inside.
- `fetch_jbd` could raise `TypeError` on a driver that reports no temperature, which was
  swallowed as a silent miss.
- Tray widget: refreshes no longer overlap. A new thread and event loop were spawned every
  12 s while one pass over the bank takes ~40 s, so several scanners and clients ended up
  fighting over one adapter. `loop.close()` now runs from a `finally`.
- `battery_monitor.py` no longer reprints the previous cycle's ECO-WORTHY values as if live
  (module-global packet buffers were never reset between cycles).

### Added
- **`bms_driver.py`** — one shared BLE layer for all three front-ends. `protocol` now accepts
  any [`aiobmsble`](https://pypi.org/p/aiobmsble/) driver name (`"daly"`, `"jikong"`, …), and
  an unrecognised value stops the dashboard at startup instead of silently routing to a
  default branch.
- **Per-battery `"persistent": true`** — hold one BLE link open instead of reconnecting each
  poll, the way the phone app does. Enabled for ECO-WORTHY.
- **Release switch** — a *Release for phone app* button on the card, plus
  `POST /api/ble/release` and `POST /api/ble/resume`, so the official app can still connect to
  a battery whose link the dashboard is holding.
- **Graceful shutdown** — SIGTERM/SIGINT handler closes BLE links before exit, and a startup
  sweep clears links BlueZ is still holding from a previous run (it does not close them when a
  process dies — bluez/bluez#89).
- Optional **`polling`** config block: `interval_seconds`, `attempts`, `scan_timeout`,
  `release_minutes`, `release_max_minutes`.
- `tools/test_ecoworthy.py` rewritten as a driver probe (`-v` surfaces CRC validation
  failures); both `tools/` scripts now take a MAC on the command line instead of hardcoding one.

### Changed
- ECO-WORTHY readings now include **temperature** and **ΔV**. Fields a protocol does not
  report are `null` (rendered `—`) rather than a fabricated `0`.

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
