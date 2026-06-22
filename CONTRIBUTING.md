# Contributing

Thanks for your interest in improving the LiFePO4 BMS Monitor! This is a small,
hardware-adjacent project and contributions of every size are welcome — bug reports,
docs fixes, a new BMS protocol, or a whole new interface.

## Ways to help

- **Add support for another BMS model** (see [Reporting a new BMS](#reporting-a-new-bms))
- **Improve ECO-WORTHY parsing** for other variants
- **Docker / container** packaging
- **Home Assistant** or other integrations on top of the JSON API
- **Docs, screenshots, and onboarding** improvements

## Dev setup

```bash
git clone https://github.com/kbennett2000/linux-lifepo4-bms-monitor.git
cd linux-lifepo4-bms-monitor
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

You do **not** need a battery to work on the UI, the API, or most of the codebase:

```bash
python3 dashboard.py --demo        # web dashboard with sample batteries
python3 battery_monitor.py --demo  # terminal monitor with sample readings
```

`--demo` injects realistic sample data and never touches Bluetooth, so it runs
anywhere (including CI and headless boxes). It's also how the README screenshots are
generated — see [`assets/src/`](assets/src/).

For changes that involve real Bluetooth, you'll need a Linux host with a BLE adapter,
`bluez` installed, and at least one supported BMS in range. See the README's
Troubleshooting section for adapter tips.

## Coding conventions

- **Python 3.10+**, standard library style. Match the surrounding code — these files
  favor clear, well-commented functions over cleverness.
- Keep the project **dependency-light** and **100% offline**: no CDNs, no telemetry, no
  cloud calls. Front-end assets are vendored locally on purpose.
- All user-facing strings, ports, and the battery list live in `config.json` — don't
  hard-code them in Python.
- The background poll thread must **never** crash the process; failures degrade to a
  "stale" card, not a dead dashboard. Preserve that behavior.
- Run a quick sanity check before opening a PR:

  ```bash
  python3 -m py_compile *.py tools/*.py
  python3 dashboard.py --demo   # loads and renders without errors
  ```

## Reporting a new BMS

Adding a battery is the most valuable contribution. Helpful details to include in an issue:

1. **Brand / model** and where you bought it.
2. The BLE **service and characteristic UUIDs** (the `tools/` scripts can help dump these).
3. A few **raw notification packets** (hex) alongside the values shown in the vendor's app,
   so the byte offsets can be matched.
4. Whether it appears to speak the standard **JBD** protocol or something custom (like
   ECO-WORTHY).

The diagnostic helpers in [`tools/`](tools/) (`clean_scan.py`, `diagnose_ecoworthy.py`,
`test_ecoworthy.py`) are good starting points for capturing this.

## Pull requests

- Branch off `main`, keep PRs focused, and describe **what** changed and **why**.
- Update the README and [`CHANGELOG.md`](CHANGELOG.md) when behavior changes.
- For protocol work, note which hardware you tested against.

## Design decisions

Significant architectural choices are recorded as ADRs under
[`docs/adr/`](docs/adr/). If you're changing something load-bearing (e.g. the BLE
recovery strategy), consider adding or updating an ADR.

By contributing, you agree your work is licensed under the project's [MIT License](LICENSE).
