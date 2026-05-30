# LiFePO4 Battery Monitor for Ubuntu / Linux

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform: Linux](https://img.shields.io/badge/Platform-Linux-informational.svg)](#what-you-need-before-you-start)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

**Real-time Bluetooth monitoring for JBD-style and ECO-WORTHY LiFePO4 BMS batteries.**

A lightweight, **100% offline** way to keep an eye on the lithium battery banks in your
**off-grid**, **RV / camper / van**, or **solar** setup — straight from your Ubuntu/Linux
laptop, desktop, or a headless **Raspberry Pi** server. No app, no cloud, no account.

Three ways to view your battery data:

| Interface | Script | Best for |
|---|---|---|
| Terminal monitor | `battery_monitor.py` | Quick checks, testing, debugging |
| Web dashboard | `dashboard.py` | Daily use — view from any phone/laptop on your LAN |
| System tray widget | `battery_widget.py` | Always-visible icon on an Ubuntu desktop |

Highlights:
- Supports **multiple batteries** at the same time
- Light/dark theme, mobile-friendly
- **Runs 100% offline** — no cloud, no CDN, no telemetry
- One `config.json` controls everything

Tested on **Ubuntu 24.04** and **22.04** (server and desktop).

---

## Supported BMS

| Brand / Model | Protocol | Notes |
|---|---|---|
| JBD / Jiabaida | Standard JBD | Most common |
| Daly, Overkill, etc. | JBD compatible | Same protocol |
| **ECO-WORTHY** | Custom | Reverse-engineered in this project |
| Others | — | May work if they use the JBD protocol |

---

# Quick Start (TL;DR)

If you already know what you're doing:

```bash
sudo apt update && sudo apt install -y python3-venv bluez git
git clone https://github.com/kbennett2000/linux-lifepo4-bms-monitor.git
cd linux-lifepo4-bms-monitor
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
# edit config.json with your batteries' MAC addresses
python3 dashboard.py
```

Open http://127.0.0.1:8040 in a browser. Done.

If you're new to this, follow the step-by-step guide below.

---

# Full Step-by-Step Guide

## What you need before you start

1. A computer running **Ubuntu 22.04 or 24.04** (desktop or server edition, both work), with **Python 3.10 or newer**.
   - **Raspberry Pi users:** use **Raspberry Pi OS Bookworm** (ships Python 3.11). The older Bullseye release ships Python 3.9, which is too old — `pip install` or the dashboard will fail.
2. **A working Bluetooth adapter.** Most laptops have one built in. For a desktop or headless server you may need a USB Bluetooth dongle (any cheap BLE 4.0+ dongle from Amazon works).
3. **Your battery's Bluetooth MAC address.** We'll find this in Step 4.
4. About 5 minutes.

> **Tip:** If you've used your battery's phone app before, close it completely before running these scripts. Most BMS chips can only handle one Bluetooth client at a time.

---

## Step 1 — Install system packages

Open a terminal and paste:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip bluez git
```

If you **also** want the desktop system-tray widget, add these (skip on a headless server):

```bash
sudo apt install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-appindicator3-0.1
```

Check that Bluetooth is running:

```bash
systemctl status bluetooth
```

You should see `active (running)` in green. If not:

```bash
sudo systemctl enable --now bluetooth
```

---

## Step 2 — Download the project

```bash
cd ~
git clone https://github.com/kbennett2000/linux-lifepo4-bms-monitor.git
cd linux-lifepo4-bms-monitor
```

You should now be inside the project folder. Confirm with `ls` — you should see `dashboard.py`, `config.example.json`, etc.

---

## Step 3 — Install the Python dependencies

We install everything inside a **virtual environment** (a sandbox folder called `venv`) so it doesn't touch the system Python.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

After this, your shell prompt will start with `(venv)`. That means the sandbox is active.

> **To re-activate it later** (after closing the terminal): `cd ~/linux-lifepo4-bms-monitor && source venv/bin/activate`

---

## Step 4 — Find your batteries' MAC addresses

Power on your batteries and make sure the BMS phone app is **closed** on all phones in range. Then run:

```bash
python3 tools/clean_scan.py
```

> **Make sure the venv is active first** — your prompt should start with `(venv)`. If it doesn't (e.g. you opened a new terminal), run `cd ~/linux-lifepo4-bms-monitor && source venv/bin/activate` first, or you'll get `ModuleNotFoundError: No module named 'bleak'`.

This will list every nearby Bluetooth device. Look for ones whose name matches your battery (often `JBD`, `xiaoxiang`, `BP00`, `BT-TH-...`, or similar) and **copy the MAC address** (the `XX:XX:XX:XX:XX:XX` part after the `|`).

Example output:

```
📡 xiaoxiang BMS          | A4:C1:37:55:C8:D3
📡 xiaoxiang BMS          | A4:C1:37:55:C2:29
📡 BT-TH-EC9C             | E2:E7:79:8A:56:A3
```

Write down the MAC for each of your batteries — you'll need them in the next step.

---

## Step 5 — Create your config file

Copy the example config and edit it:

```bash
cp config.example.json config.json
nano config.json
```

You'll see this:

```json
{
  "server": { "host": "0.0.0.0", "port": 8040 },
  "ui": {
    "page_title": "LiFePO4 Battery Dashboard",
    "header_title": "Jones Big Ass LiFePO4 Monitor",
    "header_subtitle": "Collectin' Some Good Ass Battery Data!",
    "footer_text": "Local-only · No cloud · No internet required",
    "refresh_seconds": 8,
    "default_theme": "system"
  },
  "batteries": {
    "200ah_01":  { "address": "A4:C1:37:55:C8:D3", "protocol": "jbd",       "label": "200Ah #1" },
    "200ah_02":  { "address": "A4:C1:37:55:C2:29", "protocol": "jbd",       "label": "200Ah #2" },
    "330ah":     { "address": "A4:C1:37:25:C4:4D", "protocol": "jbd",       "label": "330Ah" },
    "ecoworthy": { "address": "E2:E7:79:8A:56:A3", "protocol": "ecoworthy", "label": "ECO-WORTHY" }
  }
}
```

**Replace the example batteries with your own.** For each battery, set:

- `address` — the MAC address from Step 4
- `protocol` — `"jbd"` for almost everything, or `"ecoworthy"` for ECO-WORTHY brand
- `label` — whatever name you want shown on the dashboard

> **ECO-WORTHY note:** the `"ecoworthy"` protocol was reverse-engineered from one specific ECO-WORTHY hardware revision. If your ECO-WORTHY battery shows up in the scan but its card stays empty/stale, your unit likely uses a slightly different packet format — please [open a GitHub issue](https://github.com/kbennett2000/linux-lifepo4-bms-monitor/issues) with your battery's model and MAC so the parser can be extended. Standard JBD-protocol batteries are unaffected.

You can also change:

- `server.port` — defaults to **8040**. Pick another if 8040 is in use.
- `ui.*` — the title, subtitle, footer text shown on the dashboard.
- `ui.refresh_seconds` — how often the page refreshes.
- `ui.default_theme` — `"system"`, `"light"`, or `"dark"`. Users can override with the toggle.

Save and exit nano: **Ctrl+O**, **Enter**, **Ctrl+X**.

---

## Step 6 — First run

```bash
python3 dashboard.py
```

You should see:

```
Dashboard running at http://127.0.0.1:8040
Keep this terminal open while using the dashboard.
```

Open **http://127.0.0.1:8040** in a browser on the same machine. Wait 30–60 seconds for the first battery reading to appear (more batteries = longer first poll). You should see cards with SOC, voltage, current, cell bars, etc.

**To stop:** press **Ctrl+C** in the terminal.

If something didn't work, jump to [Troubleshooting](#troubleshooting).

---

# Using the App

### Web dashboard

```bash
python3 dashboard.py
```

Opens on the port from `config.json` (default **8040**). You can override the host and port at runtime:

```bash
python3 dashboard.py --port 9000                        # CLI flag — port only
python3 dashboard.py --host 192.168.1.50 --port 9000    # CLI flags — host and port
BMS_DASHBOARD_PORT=9000 python3 dashboard.py            # env variable (handy for systemd)
BMS_DASHBOARD_HOST=192.168.1.50 python3 dashboard.py    # bind to a specific interface
```

The `--host` / `BMS_DASHBOARD_HOST` override is useful on a multi-homed host (e.g. a Pi with both `eth0` and `wlan0`) when you want the dashboard to listen on one interface only.

### Terminal monitor

```bash
python3 battery_monitor.py
```

Prints a fresh reading for each battery every ~25 seconds. Press Ctrl+C to stop.

### System tray widget (Ubuntu desktop only)

```bash
python3 battery_widget.py
```

> Run this **outside** the venv (`deactivate` first) — the GTK packages live on the system Python.

A `🔋 100% • 85% • ...` indicator appears in your top bar. Click it for full details, or the "Open Dashboard" entry to launch the web view.

You can run any combination of these three tools at the same time.

---

# Run It on a Headless Ubuntu Server (LAN Access)

This section sets up the dashboard to run **automatically at boot** on a Ubuntu Server box, so you can view your batteries from your phone, your laptop, or any other device on your home Wi-Fi.

## Step A — Make sure the server has Bluetooth

A Bluetooth dongle plugged into the server, in range of your batteries. Verify:

```bash
hciconfig
# or
bluetoothctl show
```

You should see a `hci0` adapter listed.

## Step B — Find the server's LAN IP address

```bash
hostname -I
```

You'll get something like `192.168.1.50`. Write it down — that's how other devices will reach the dashboard.

> For convenience, consider giving your server a **static IP** in your router's DHCP settings so the address never changes.

## Step C — Open the firewall (if UFW is enabled)

If `sudo ufw status` shows "active":

```bash
sudo ufw allow 8040/tcp
```

(Substitute your port if you changed it.)

## Step D — Install as a systemd service

This makes the dashboard start at boot and auto-restart if it ever crashes.

> **Prerequisite — the venv must exist on _this_ machine.** The service runs `venv/bin/python`, so if you're setting up a fresh server (rather than the machine where you ran the Quick Start) you must first create and populate the venv here, or the service fails to start with `status=203/EXEC`:
> ```bash
> cd ~/linux-lifepo4-bms-monitor
> python3 -m venv venv
> venv/bin/pip install -r requirements.txt
> ```
> This also installs `bluetooth-auto-recovery` (it's already listed in `requirements.txt`) — the dependency that lets the dashboard recover a wedged Bluetooth adapter on its own. No separate install step is needed.

**1. Find your exact paths:**

```bash
whoami                                   # your username, e.g. "ubuntu"
echo $PWD                                # repo path, e.g. /home/ubuntu/linux-lifepo4-bms-monitor
which python3                            # not used; we want the venv python:
readlink -f venv/bin/python              # e.g. /home/ubuntu/linux-lifepo4-bms-monitor/venv/bin/python
```

**2. Create the service file:**

```bash
sudo nano /etc/systemd/system/bms-dashboard.service
```

Paste the following, replacing `YOUR_USERNAME` and the two paths with the values from step 1:

```ini
[Unit]
Description=LiFePO4 BMS Web Dashboard
After=network-online.target bluetooth.service
Wants=network-online.target bluetooth.service

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/linux-lifepo4-bms-monitor
ExecStart=/home/YOUR_USERNAME/linux-lifepo4-bms-monitor/venv/bin/python dashboard.py
Restart=on-failure
RestartSec=10
# Allow the dashboard to recover a wedged BLE adapter on its own (see below).
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW

[Install]
WantedBy=multi-user.target
```

Save (**Ctrl+O**, **Enter**, **Ctrl+X**).

> **Why the `CAP_NET_ADMIN` lines?** After hours of scanning, some BLE adapters wedge
> and stop returning devices — historically this needed a manual
> `sudo systemctl restart bluetooth` or a reboot. The dashboard now detects this and
> power-cycles the adapter itself, but that requires `CAP_NET_ADMIN` (to talk to the
> kernel Bluetooth management socket). Without these lines the dashboard still runs and
> still recovers via a `systemctl restart bluetooth` fallback — but that fallback needs
> the service to run as **root**, or a passwordless sudoers rule for that one command.
> Granting the capability to a normal-user service is the cleaner option.

**3. Enable and start it:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bms-dashboard
```

**4. Confirm it's running:**

```bash
systemctl status bms-dashboard
```

You should see `active (running)` in green.

**Useful commands:**

```bash
sudo systemctl restart bms-dashboard      # restart after editing config.json
sudo systemctl stop bms-dashboard         # stop it
sudo systemctl disable bms-dashboard      # don't start at boot anymore
journalctl -u bms-dashboard -f            # live logs (Ctrl+C to exit)
```

## Step E — Access from your phone / other devices

On any device connected to the same Wi-Fi:

> **http://YOUR_SERVER_IP:8040**

e.g. `http://192.168.1.50:8040`

That's it — bookmark it on your phone's home screen for one-tap access.

> **Pro tip:** Most home routers let you assign a hostname like `bms.local` or set a custom DNS entry, so you can use `http://bms.local:8040` instead of remembering an IP.

---

# Configuration Reference

All settings live in `config.json` at the project root.

| Field | What it does |
|---|---|
| `server.host` | Network interface to bind. `0.0.0.0` = all interfaces (needed for LAN access). `127.0.0.1` = local only. |
| `server.port` | Port to listen on. Default `8040`. |
| `ui.page_title` | Browser tab title. |
| `ui.header_title` | Big title shown at the top of the dashboard. |
| `ui.header_subtitle` | Small text under the title. |
| `ui.footer_text` | Text shown at the bottom of the page. |
| `ui.refresh_seconds` | How often the browser polls for new data. Default `8`. |
| `ui.default_theme` | `"system"`, `"light"`, or `"dark"`. Per-user toggle still wins. |
| `batteries.<name>.address` | Bluetooth MAC address (`XX:XX:XX:XX:XX:XX`). |
| `batteries.<name>.protocol` | `"jbd"` or `"ecoworthy"`. |
| `batteries.<name>.label` | Display name on the dashboard. |

Two ways to override the port or host without editing `config.json`:

```bash
python3 dashboard.py --port 9000
BMS_DASHBOARD_PORT=9000 python3 dashboard.py

python3 dashboard.py --host 192.168.1.50
BMS_DASHBOARD_HOST=192.168.1.50 python3 dashboard.py
```

`--host` / `BMS_DASHBOARD_HOST` is useful on a multi-homed host (e.g. a Pi with both `eth0` and `wlan0`) to bind the dashboard to one interface only.

After editing `config.json`, restart the dashboard (or `sudo systemctl restart bms-dashboard` if you set up the service).

---

## Advanced tuning

These four constants are defined at the top of `dashboard.py`. They are **not** in `config.json` — edit `dashboard.py` directly to change them.

| Constant | Default | What it controls |
|---|---|---|
| `STALE_AFTER_MISSES` | `1` | Consecutive missed polls before a battery's card is shown as stale/dimmed. |
| `FETCH_ATTEMPTS` | `2` | Read attempts per battery per cycle before it counts as a miss. |
| `RECOVER_AFTER_MISSES` | `3` | Consecutive misses before the BLE adapter is power-cycled. |
| `RECOVER_COOLDOWN_SECONDS` | `300` | Minimum seconds between adapter-recovery attempts. |

> **Common tweak:** bump `STALE_AFTER_MISSES` to `2` to reduce single-miss flicker on a marginal signal — the card won't dim until two polls in a row fail.

---

# How It Works

- Uses **Bluetooth Low Energy (BLE)** via the `bleak` Python library.
- Most batteries use the common **JBD protocol** (parsed by `aiobmsble`).
- The **ECO-WORTHY** BMS uses a custom service (`0000fff0`) and was reverse-engineered for this project — we send the same commands the official phone app uses, then parse the raw notification packets.
- A background thread polls each battery sequentially (BLE only allows one connection at a time per adapter). Each battery is retried up to `FETCH_ATTEMPTS` (2) times per cycle before counting as a miss. On a miss the last-known-good values are **retained** — the card stays visible but is dimmed/marked stale rather than disappearing.
- A background watchdog tracks consecutive misses per battery. When any battery reaches `RECOVER_AFTER_MISSES` (3) consecutive misses, the watchdog power-cycles the BLE adapter in-process — graduating from a gentle power-cycle to a USB reset if needed — rate-limited by `RECOVER_COOLDOWN_SECONDS` (300 s), and falling back to `systemctl restart bluetooth`. See the Troubleshooting section for reading the recovery logs.
- A tiny Flask server serves that data as JSON to the browser at `/api/data`.
- The browser uses vanilla JavaScript + a vendored Tailwind runtime — everything is served locally. **No internet connection is required at any point after install.**

---

# API

The dashboard exposes two read-only JSON endpoints, useful for integrations (e.g. Home Assistant, scripts, or custom UIs).

## `GET /api/data`

Returns a JSON object keyed by battery id (the key names from `config.json`'s `batteries` block). Each value contains:

**Measurement fields** (not all protocols populate all fields — ECO-WORTHY omits `temperature`, `cycles`, and `delta_mv`):

| Field | Type | Description |
|---|---|---|
| `voltage` | float | Pack voltage (V) |
| `current` | float | Charge/discharge current (A) |
| `power` | float | Instantaneous power (W) |
| `soc` | int | State of charge (%) |
| `temperature` | float | BMS temperature (°C) — JBD only |
| `cells` | list[float] | Per-cell voltages (V) |
| `delta_mv` | float | Max cell spread (mV) — JBD only |
| `cycles` | int | Charge cycle count — JBD only |

**Status fields** (always present):

| Field | Type | Description |
|---|---|---|
| `label` | string | Display name from `config.json` |
| `last_seen` | float | Epoch seconds of the last successful read |
| `misses` | int | Consecutive missed polls since the last good reading |
| `stale` | bool | `true` when `misses >= STALE_AFTER_MISSES` |
| `age_seconds` | int | Seconds elapsed since the last successful read |

A battery that has never been read yet does not appear in the response at all.

## `GET /api/config`

Returns the `ui` block from `config.json` — useful for reading dashboard titles, refresh interval, etc. without parsing the config file directly.

---

# Troubleshooting

**The dashboard loads but shows "Waiting for first battery reading…" forever**
- Make sure the BMS phone app is fully closed (force-quit on iOS/Android).
- Run `python3 battery_monitor.py` in a separate terminal — it gives more detailed error messages per battery.
- Toggle the system's Bluetooth off and on: `sudo systemctl restart bluetooth`.

**`bleak.exc.BleakError: Bluetooth device is turned off`**
- Run `sudo systemctl enable --now bluetooth`.
- On a server, make sure the USB dongle is plugged in: `lsusb | grep -i blue`.

**Battery doesn't show up in `tools/clean_scan.py`**
- Phone app is open somewhere — close it.
- Battery is out of Bluetooth range (~10m line of sight).
- Battery's BLE module is asleep — touch the battery terminals briefly or wake it via the phone app, then close the app.

**"InProgress" or "Operation already in progress" errors**
- The scripts already serialize batteries to avoid this. If it persists: `sudo systemctl restart bluetooth`.

**Some batteries drop off after the dashboard has been running for hours (and only a reboot or `systemctl restart bluetooth` brings them back)**
- This is a known BLE-on-Linux failure mode: the adapter/BlueZ stack wedges after extended scanning and stops returning some devices.
- The dashboard now handles this automatically — if a battery misses several consecutive polls it power-cycles the adapter in-process (via `bluetooth-auto-recovery`, falling back to `systemctl restart bluetooth`), and the missing batteries return on their own.
- Watch it work: `journalctl -u bms-dashboard -f | grep -E 'recovery|background_updater'`.
- **Reading the `[recovery]` logs:**
  - Success looks like: `[recovery] recover_adapter(hci0, …, gone_silent=False) -> True` (a gentle power-cycle worked; it escalates to `gone_silent=True` only if that fails).
  - A permission failure looks like: `recover_adapter(...) -> False` or a `PermissionError`, usually followed by `[recovery] 'systemctl restart bluetooth' failed: …`. That means the service can't reach the Bluetooth management socket — add the `AmbientCapabilities` lines to the unit file (see *Install as a systemd service*) and `sudo systemctl daemon-reload && sudo systemctl restart bms-dashboard`.
  - If you see **no** `[recovery]` lines at all when batteries drop, recovery isn't being triggered — confirm the dropped battery's MAC is correct (a wrong address looks the same as a wedge).

**A live battery keeps flickering "Stale" and back to normal**
- A card is marked **Stale** after a single missed poll, because one dropped BLE advertisement is common — this is cosmetic and expected on a marginal signal; the card keeps showing its last reading.
- Automatic adapter recovery only kicks in after **3 consecutive** misses, so brief stale flicker does not trigger a power-cycle. If a battery is *constantly* stale, improve the signal (move the dongle closer / add a USB extension) or check for a phone app holding the connection.

**Tray widget icon is invisible / shows nothing**
- Run it **outside** the venv: `deactivate && python3 battery_widget.py`.
- Make sure the AppIndicator system packages from Step 1 are installed.
- On GNOME, install the **AppIndicator** extension.

**ECO-WORTHY values look wrong (huge current, weird SOC)**
- The phone app is connected — close it. The BMS sends slightly different packet formats depending on which client is asking.

**Dashboard works from the server but not from my phone**
- Check `server.host` is `0.0.0.0` (not `127.0.0.1`).
- Check the firewall: `sudo ufw allow 8040/tcp`.
- Make sure you're on the **same Wi-Fi network** as the server.
- Try `curl http://SERVER_IP:8040/` from another machine on the LAN.

**`Address already in use` when starting the dashboard**
- Something else is using port 8040. Either stop it, or change `server.port` in `config.json`.

**systemd service shows `(code=exited, status=203/EXEC)`**
- The `ExecStart` path is wrong. Re-run `readlink -f venv/bin/python` and update the service file.

---

# Contributing

PRs welcome! Especially:
- Support for additional BMS models
- Better parsing for other ECO-WORTHY variants
- Docker / container support
- Home Assistant integration

---

# License

MIT License — feel free to use, modify, and share.

Made with care for the DIY solar / off-grid community.
If this helped you, please star the repo!
