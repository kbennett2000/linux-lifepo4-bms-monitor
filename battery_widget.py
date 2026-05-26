#!/usr/bin/env python3
"""
LiFePO4 Battery Tray Widget - System Tray Monitor
=================================================

This script creates a system tray icon (in the top bar on Ubuntu/GNOME) that shows
the State of Charge (SOC) of all your LiFePO4 batteries at a glance.

Clicking the tray icon opens a rich popup menu with detailed information for every battery.

Features:
- Live updating every 12 seconds
- Supports both standard JBD batteries and the custom ECO-WORTHY protocol
- Uses the exact same reliable menu pattern as your working ham_conditions_tray.py
- Clean, readable popup with all important values (voltage, current, power, cells)

Why this design?
- GTK + AppIndicator is the standard way to create system tray apps on Ubuntu.
- We create the menu items once and only update the text (this is the most stable method).

Author: Kris Bennett (May 2026)
"""

import gi
# Tell GTK which version of the libraries we want to use
gi.require_version('Gtk', '3.0')
gi.require_version('AppIndicator3', '0.1')
from gi.repository import Gtk, AppIndicator3, GLib

import asyncio
import threading

# BLE libraries for talking to the batteries
from bleak import BleakScanner, BleakClient
from aiobmsble.bms.jbd_bms import BMS

import webbrowser   # Used to open the web dashboard when user clicks the button

# Shared config (battery list + dashboard port for the "Open Dashboard" button)
from bms_config import battery_tuples, load_config

_CONFIG = load_config()


class BatteryTray:
    """
    Main class that manages the system tray icon and all battery monitoring.
    """

    def __init__(self):
        # Create the system tray indicator
        self.indicator = AppIndicator3.Indicator.new(
            "battery-monitor",                  # Internal ID
            "battery-good",                     # Icon name (can be changed later)
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_label("🔋 --", "battery-monitor-label")

        # Create the menu that appears when you click the tray icon
        self.menu = Gtk.Menu()

        # === Create the details area once (this is the key pattern from your ham widget) ===
        # We create one label and update its text instead of rebuilding the whole menu every time.
        self.details_label = Gtk.Label()
        self.details_item = Gtk.MenuItem()
        self.details_item.add(self.details_label)
        self.menu.append(self.details_item)

        # Add a separator line
        separator = Gtk.SeparatorMenuItem()
        self.menu.append(separator)

        # Button to open the web dashboard
        open_dash = Gtk.MenuItem(label="🌐 Open Dashboard")
        open_dash.connect("activate", lambda x: self.open_dashboard())
        self.menu.append(open_dash)

        # Quit option
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda x: Gtk.main_quit())
        self.menu.append(quit_item)

        # Make the menu visible
        self.menu.show_all()
        self.indicator.set_menu(self.menu)

        # List of all batteries we want to monitor (from config.json)
        self.BATTERIES = battery_tuples(_CONFIG)

        self.latest_data = {}   # Will hold the most recent data for each battery

        # Start periodic updates (every 12 seconds)
        GLib.timeout_add_seconds(12, self.update)
        self.update()   # Do an immediate first update

    # ====================== DATA FETCHING ======================

    async def fetch_jbd(self, name: str, address: str):
        """Fetch data from a standard JBD-style BMS using the aiobmsble library."""
        try:
            device = await BleakScanner.find_device_by_address(address, timeout=8.0)
            if not device:
                return None
            async with BMS(ble_device=device) as bms:
                data = await bms.async_update()
                return {
                    "name": name,
                    "soc": data.get('battery_level', 0),
                    "voltage": round(data.get('voltage', 0), 2),
                    "current": round(data.get('current', 0), 2),
                    "power": round(data.get('power', 0), 1),
                    "cells": [round(v, 3) for v in (data.get('cell_voltages') or [])]
                }
        except Exception:
            return None

    async def fetch_ecoworthy(self, name: str, address: str):
        """Fetch data from the ECO-WORTHY BMS using raw BLE commands + notifications."""
        try:
            device = await BleakScanner.find_device_by_address(address, timeout=10.0)
            if not device:
                return None

            a1 = a2 = None
            async with BleakClient(device) as client:
                def handler(sender, data):
                    nonlocal a1, a2
                    h = data.hex()
                    if h.startswith("e2e7798a56a3a1"):
                        a1 = h
                    elif h.startswith("e2e7798a56a3a2"):
                        a2 = h

                await client.start_notify("0000fff1-0000-1000-8000-00805f9b34fb", handler)
                await client.write_gatt_char("0000fff2-0000-1000-8000-00805f9b34fb", bytes.fromhex("dda50300fffd77"))
                await asyncio.sleep(1.2)
                await client.write_gatt_char("0000fff2-0000-1000-8000-00805f9b34fb", bytes.fromhex("dda50400fffc77"))
                await asyncio.sleep(1.5)

            if not a2 or not a1:
                return None

            a2b = bytes.fromhex(a2)
            cells = [round(int.from_bytes(a2b[14 + i*2:16 + i*2], "big") / 1000, 3) for i in range(4)]
            voltage = round(sum(cells), 2)

            a1b = bytes.fromhex(a1)
            current = round(int.from_bytes(a1b[20:22], "big", signed=True) / 10, 2)
            power = round(voltage * current, 1)
            soc = a1b[15]

            return {
                "name": name,
                "soc": soc,
                "voltage": voltage,
                "current": current,
                "power": power,
                "cells": cells
            }
        except Exception:
            return None

    # ====================== UPDATE LOGIC ======================

    def update(self):
        """Called by GLib every 12 seconds to trigger a background data refresh."""
        threading.Thread(target=self._fetch_and_update, daemon=True).start()
        return True   # Return True to keep the timer running

    def _fetch_and_update(self):
        """
        Runs in a background thread.
        Fetches fresh data from all batteries and then updates the UI on the main thread.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        new_data = {}
        for name, (addr, proto) in self.BATTERIES.items():
            if proto == "jbd":
                result = loop.run_until_complete(self.fetch_jbd(name, addr))
            else:
                result = loop.run_until_complete(self.fetch_ecoworthy(name, addr))
            if result:
                new_data[name] = result

        loop.close()

        self.latest_data = new_data
        # Schedule the UI update to run on the main GTK thread
        GLib.idle_add(self._update_ui)

    def _update_ui(self):
        """
        Updates the tray icon label and the popup menu text.
        This runs on the main GTK thread (required for GUI operations).
        """
        if not self.latest_data:
            self.indicator.set_label("🔋 --", "battery-monitor-label")
            self.details_label.set_markup(
                "<span size='large' weight='bold'>🔋 Battery Status</span>\n\nNo data yet..."
            )
            return False

        # Build the detailed text shown when you click the tray icon
        text = "<span size='large' weight='bold'>🔋 Battery Status</span>\n\n"
        for batt in self.latest_data.values():
            text += f"<b>{batt['name']}</b>\n"
            text += f"   🔋 <b>{batt['soc']}%</b>   {batt['voltage']} V\n"
            text += f"   {batt['current']:.2f} A   {batt.get('power', 0):.1f} W\n"
            text += f"   Cells: {' | '.join(map(str, batt['cells']))}\n\n"

        self.details_label.set_markup(text)

        # Update the short label shown in the system tray itself
        socs = [f"{d['soc']}%" for d in self.latest_data.values()]
        self.indicator.set_label("🔋 " + " • ".join(socs), "battery-monitor-label")

        return False   # We don't need to keep calling this function

    def open_dashboard(self):
        """Open the web dashboard in the default browser."""
        port = _CONFIG.get("server", {}).get("port", 8040)
        webbrowser.open(f"http://127.0.0.1:{port}")


# =============================================================================
# Program entry point
# =============================================================================
if __name__ == "__main__":
    BatteryTray()
    Gtk.main()   # Start the GTK main loop (keeps the tray icon alive)