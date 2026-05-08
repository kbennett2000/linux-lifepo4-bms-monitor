# linux-lifepo4-bms-monitor

# LiFePO4 Battery Monitor for Ubuntu/Linux

**Real-time Bluetooth monitoring for JBD-style and ECO-WORTHY LiFePO4 BMS batteries**

A complete, easy-to-use solution that gives you:
- Clean terminal output
- Beautiful web dashboard (mobile-friendly)
- System tray widget (always visible in your top bar)

Tested on Ubuntu 24.04 / 22.04.

## Features

- Supports **multiple batteries** at the same time
- Real-time voltage, current, power, SOC, cell voltages, temperature
- Three interfaces:
  - Terminal monitor (`battery_monitor.py`)
  - Web dashboard (`dashboard.py`) with gauges and cell bar charts
  - System tray widget (`battery_widget.py`) with rich popup
- Special support for the **ECO-WORTHY** BMS (which uses a non-standard protocol)
- No cloud services required — everything runs locally
- Lightweight and efficient

## Supported BMS

| Brand / Model       | Protocol     | Fully Supported | Notes |
|---------------------|--------------|------------------|-------|
| JBD / Jiabaida      | Standard JBD | Yes             | Most common |
| Daly, Overkill, etc.| JBD compatible | Yes         | Same protocol |
| **ECO-WORTHY**      | Custom       | Yes             | Reverse-engineered in this project |
| Others              | -            | Partial         | May work if they use JBD protocol |

## Requirements

**System packages:**
```bash
sudo apt update
sudo apt install python3-venv python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-appindicator3-0.1 bluez -y
```

Python packages (installed via pip in a venv or system-wide):
- `bleak`
- `aiobmsble`
- `flask`

## Installation
Clone the repo:
```bash
git clone https://github.com/kbennett2000/linux-lifepo4-bms-monitor.git
cd linux-lifepo4-monitor
```

Create and activate a virtual environment (recommended):
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

(For the tray widget) Install system packages (already listed above).

Usage
1. Terminal Monitor (most reliable for testing)
```bash
python3 battery_monitor.py
```
2. Web Dashboard (recommended daily use)
```bash
python3 dashboard.py
```
Then open `http://127.0.0.1:5000` (or your laptop's IP from your phone).
3. System Tray Widget
```bash
python3 battery_widget.py
```
(Works best when run outside the venv with python3 battery_widget.py)
You can run any combination of the three tools at the same time.

## Configuration
- Edit the `BATTERIES` dictionary at the top of each file to add/remove your batteries.
Example:
```python
BATTERIES = {
    "200ah_01": ("A4:C1:37:55:C8:D3", "jbd"),
    "ecoworthy": ("E2:E7:79:8A:56:A3", "ecoworthy"),
    # Add more here
}
```

How It Works
- Uses Bluetooth Low Energy (BLE) via the bleak library
- Most batteries use the common JBD protocol (handled by aiobmsble)
- The ECO-WORTHY battery uses a custom service (0000fff0) and was reverse-engineered from raw BLE packets in this project
- All data is read directly from the BMS — no cloud, no internet required

# Troubleshooting
- Battery not appearing: Close the phone app completely and toggle Bluetooth off/on
- "InProgress" errors: The scripts already handle this by reading batteries sequentially
- Tray widget shows nothing: Run with plain python3 battery_widget.py (not inside venv)
- ECO-WORTHY shows wrong values: Make sure the phone app is closed

# Contributing
Feel free to open issues or PRs! Especially welcome:
- Support for additional BMS models
- Better parsing for other ECO-WORTHY variants
- Docker support
- Home Assistant integration

License
MIT License — feel free to use, modify, and share.

Made with ❤️ for the DIY solar / off-grid community
If this helped you, please star the repo!
