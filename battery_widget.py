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

# Shared BLE driver layer (protocol dispatch + graceful connection teardown)
import bms_driver

import webbrowser   # Used to open the web dashboard when user clicks the button

# Shared config (battery list + dashboard port for the "Open Dashboard" button)
from bms_config import battery_entries, load_config, polling_config

_CONFIG = load_config()
_POLLING = polling_config(_CONFIG)


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
        self.BATTERIES = battery_entries(_CONFIG)
        bms_driver.validate_protocols(self.BATTERIES)

        # Guards against overlapping refreshes: one pass over the bank takes far
        # longer than the 12-second timer, so without this the widget would pile up
        # threads all driving the same BLE adapter at once.
        self._busy = threading.Lock()

        self.latest_data = {}   # Will hold the most recent data for each battery

        # Start periodic updates (every 12 seconds)
        GLib.timeout_add_seconds(12, self.update)
        self.update()   # Do an immediate first update

    # ====================== DATA FETCHING ======================

    # ====================== UPDATE LOGIC ======================

    def update(self):
        """Called by GLib every 12 seconds to trigger a background data refresh."""
        # Skip this tick if the previous refresh is still running. A full pass over
        # four batteries takes much longer than the timer interval, and overlapping
        # passes mean several BLE scanners and clients fighting over one adapter.
        if self._busy.locked():
            return True
        threading.Thread(target=self._fetch_and_update, daemon=True).start()
        return True   # Return True to keep the timer running

    def _fetch_and_update(self):
        """
        Runs in a background thread.
        Fetches fresh data from all batteries and then updates the UI on the main thread.
        """
        with self._busy:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            new_data = {}
            try:
                for name, entry in self.BATTERIES.items():
                    # Always non-persistent here: this thread builds a fresh event
                    # loop every refresh and closes it below, so a held connection
                    # created in one pass would be unusable in the next.
                    result = loop.run_until_complete(
                        bms_driver.read_battery(
                            name,
                            entry["address"],
                            entry["protocol"],
                            persistent=False,
                            scan_timeout=_POLLING["scan_timeout"],
                        )
                    )
                    if result:
                        result["name"] = entry["label"]
                        new_data[name] = result
            finally:
                # Must run even if a read raises, or the loop and its D-Bus
                # transport leak while the GLib timer keeps making replacements.
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
            def num(value, digits):
                # Not every protocol reports every field; show an em dash instead
                # of crashing on a None.
                return "—" if value is None else f"{value:.{digits}f}"

            text += f"<b>{batt['name']}</b>\n"
            text += f"   🔋 <b>{batt['soc']}%</b>   {num(batt['voltage'], 2)} V\n"
            text += f"   {num(batt['current'], 2)} A   {num(batt.get('power'), 1)} W\n"
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