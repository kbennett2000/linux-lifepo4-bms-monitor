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
