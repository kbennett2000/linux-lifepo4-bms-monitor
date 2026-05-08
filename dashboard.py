#!/usr/bin/env python3
"""
LiFePO4 Battery Dashboard - Web Interface
=========================================

This is a beautiful, real-time web dashboard for monitoring multiple LiFePO4 batteries
via Bluetooth on Ubuntu/Linux.

Features:
- Modern, mobile-friendly dark interface using Tailwind CSS
- Live updating every 8 seconds (no page refresh)
- SOC gauges with color coding (green/yellow/red)
- Individual cell voltage bar charts
- Supports both standard JBD batteries and the custom ECO-WORTHY protocol
- Runs as a local Flask web server

How it works:
1. A background thread continuously reads all batteries (using the same logic as the terminal monitor)
2. Data is stored in `latest_data` (protected by a thread lock)
3. The web page uses JavaScript to fetch `/api/data` and dynamically render beautiful cards

Author: Kris Bennett (May 2026)
"""

import asyncio
import time
import threading

# Flask is a lightweight web framework - used here to serve the dashboard
from flask import Flask, render_template_string, jsonify

# BLE libraries for communicating with the batteries
from bleak import BleakScanner, BleakClient
from aiobmsble.bms.jbd_bms import BMS


app = Flask(__name__)

# =============================================================================
# CONFIGURATION - Edit this to add or remove your batteries
# =============================================================================

BATTERIES = {
    # Format: "friendly_name": ("MAC_ADDRESS", "protocol_type")
    "200ah_01": ("A4:C1:37:55:C8:D3", "jbd"),
    "200ah_02": ("A4:C1:37:55:C2:29", "jbd"),
    "330ah":    ("A4:C1:37:25:C4:4D", "jbd"),
    "ecoworthy": ("E2:E7:79:8A:56:A3", "ecoworthy"),
}

# Shared dictionary that holds the latest data for all batteries.
# The background thread writes to it, the web routes read from it.
latest_data = {}

# Threading lock to prevent race conditions when reading/writing latest_data
update_lock = threading.Lock()


# =============================================================================
# HTML TEMPLATE - This is the entire web page as a Python string
# =============================================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>LiFePO4 Battery Dashboard</title>
    <!-- Tailwind CSS for modern styling -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Chart.js for the cell voltage bar charts -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
        body { background: #0a0a0a; color: #e5e7eb; font-family: system-ui, sans-serif; }
        .card { transition: all 0.3s ease; }
        .card:hover { transform: translateY(-4px); }
        .gauge { position: relative; width: 170px; height: 170px; }
    </style>
</head>
<body class="min-h-screen p-8">
    <div class="max-w-7xl mx-auto">
        <div class="flex justify-between items-center mb-10">
            <h1 class="text-5xl font-bold text-emerald-400">
                ⚡⚡⚡ Jones Big Ass LiFePO4 Monitor ⚡⚡⚡
            </h1>
        </div>
        
        <div class="mb-6">
            <p class="text-xl text-amber-300">Collectin' Some Good Ass Battery Data!</p>
            <p id="last-updated" class="text-gray-400">Loading data...</p>
        </div>

        <!-- This is where all battery cards are dynamically inserted -->
        <div id="battery-container" class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-8"></div>
    </div>

    <script>
        let chartInstances = [];

        function destroyOldCharts() {
            chartInstances.forEach(chart => chart && chart.destroy());
            chartInstances = [];
        }

        // Fetch latest data from the Flask backend and re-render the page
        async function fetchData() {
            try {
                const res = await fetch('/api/data');
                const data = await res.json();
                renderAllBatteries(data);
                document.getElementById('last-updated').textContent = 
                    'Last updated: ' + new Date().toLocaleTimeString();
            } catch(e) {
                console.error("Fetch error:", e);
            }
        }

        // Dynamically build all battery cards with gauges and cell charts
        function renderAllBatteries(batteries) {
            destroyOldCharts();
            const container = document.getElementById('battery-container');
            container.innerHTML = '';

            Object.entries(batteries).forEach(([name, d]) => {
                const socColor = d.soc >= 80 ? 'emerald' : d.soc >= 30 ? 'amber' : 'red';

                const cardHTML = `
                    <div class="card bg-zinc-900 border border-zinc-700 rounded-3xl p-8 flex flex-col h-full">
                        <div class="flex justify-between items-start mb-6">
                            <div>
                                <h2 class="text-2xl font-semibold">🔋 ${name}</h2>
                                <p class="text-emerald-500 text-sm">${d.address}</p>
                            </div>
                            <div class="text-right">
                                <div class="text-5xl font-bold">${d.soc}<span class="text-2xl">%</span></div>
                            </div>
                        </div>

                        <!-- SOC Gauge -->
                        <div class="flex justify-center my-6">
                            <div class="gauge">
                                <svg width="170" height="170" viewBox="0 0 100 100" class="-rotate-90">
                                    <circle cx="50" cy="50" r="45" fill="none" stroke="#27272a" stroke-width="10"/>
                                    <circle cx="50" cy="50" r="45" fill="none" 
                                        stroke="currentColor"
                                        stroke-width="10"
                                        stroke-dasharray="283"
                                        stroke-dashoffset="${283 - Math.round(d.soc * 2.83)}"
                                        stroke-linecap="round"
                                        class="text-${socColor}-500"/>
                                </svg>
                                <div class="absolute inset-0 flex items-center justify-center text-6xl font-bold text-white -mt-2">
                                    ${d.soc}
                                </div>
                            </div>
                        </div>

                        <div class="grid grid-cols-2 gap-x-8 gap-y-6 text-sm flex-1">
                            <div><span class="text-gray-400">Voltage</span><br><span class="text-3xl font-semibold">${d.voltage} V</span></div>
                            <div><span class="text-gray-400">Current</span><br><span class="text-3xl font-semibold">${d.current} A</span></div>
                            <div><span class="text-gray-400">Power</span><br><span class="text-3xl font-semibold">${d.power} W</span></div>
                            <div><span class="text-gray-400">Temp</span><br><span class="text-3xl font-semibold">${d.temperature || '—'} °C</span></div>
                        </div>

                        <div class="mt-8">
                            <div class="text-gray-400 text-xs mb-2">CELL VOLTAGES</div>
                            <div class="flex gap-1 h-28">
                                ${d.cells.map(v => `
                                    <div class="flex-1 flex flex-col justify-end bg-zinc-800 rounded">
                                        <div class="bg-emerald-400 rounded-t" style="height: ${((v - 3.0) / 0.65 * 100).toFixed(1)}%"></div>
                                        <div class="text-[10px] text-center text-white mt-1">${v}</div>
                                    </div>
                                `).join('')}
                            </div>
                        </div>

                        <div class="mt-6 pt-6 border-t border-zinc-700 flex justify-between text-xs text-gray-400">
                            <div>Cycles: <span class="text-white">${d.cycles || '—'}</span></div>
                            <div>ΔV: <span class="text-white">${d.delta_mv || '—'} mV</span></div>
                        </div>
                    </div>
                `;

                container.insertAdjacentHTML('beforeend', cardHTML);
            });
        }

        // Initial load + auto-refresh every 8 seconds
        fetchData();
        setInterval(fetchData, 8000);
    </script>
</body>
</html>
"""

# =============================================================================
# DATA FETCHING FUNCTIONS
# =============================================================================

async def fetch_jbd(name, address):
    """
    Fetch data from a standard JBD/Jiabaida-style BMS using the aiobmsble library.
    Returns a clean dictionary of values or None on failure.
    """
    try:
        device = await BleakScanner.find_device_by_address(address, timeout=8.0)
        if not device:
            return None
        async with BMS(ble_device=device) as bms:
            data = await bms.async_update()
            return {
                "address": address,
                "voltage": round(data.get('voltage', 0), 2),
                "current": round(data.get('current', 0), 2),
                "power": round(data.get('power', 0), 1),
                "soc": data.get('battery_level', 0),
                "temperature": round(data.get('temperature', 0), 1),
                "delta_mv": round(data.get('delta_voltage', 0) * 1000, 1),
                "cycles": data.get('cycles', 0),
                "cells": [round(v, 3) for v in (data.get('cell_voltages') or [])]
            }
    except Exception:
        return None


async def fetch_ecoworthy(name, address):
    """
    Fetch data from the ECO-WORTHY BMS using raw BLE commands + notifications.
    This is the custom parser we reverse-engineered together.
    """
    try:
        device = await BleakScanner.find_device_by_address(address, timeout=10.0)
        if not device:
            return None

        a1 = None
        a2 = None

        async with BleakClient(device) as client:
            def handler(sender, data):
                nonlocal a1, a2
                h = data.hex()
                if h.startswith("e2e7798a56a3a1"):
                    a1 = h
                elif h.startswith("e2e7798a56a3a2"):
                    a2 = h

            await client.start_notify("0000fff1-0000-1000-8000-00805f9b34fb", handler)

            # Send the two standard commands the phone app uses
            await client.write_gatt_char("0000fff2-0000-1000-8000-00805f9b34fb", bytes.fromhex("dda50300fffd77"))
            await asyncio.sleep(1.2)
            await client.write_gatt_char("0000fff2-0000-1000-8000-00805f9b34fb", bytes.fromhex("dda50400fffc77"))
            await asyncio.sleep(1.5)

        if not a2 or not a1:
            return None

        # Parse cell voltages from A2 packet
        a2b = bytes.fromhex(a2)
        cells = [round(int.from_bytes(a2b[14 + i*2:16 + i*2], "big") / 1000, 3) for i in range(4)]
        voltage = round(sum(cells), 2)

        # Parse current and SOC from A1 packet
        a1b = bytes.fromhex(a1)
        current_raw = int.from_bytes(a1b[20:22], "big", signed=True)
        current = round(current_raw / 10, 2)
        power = round(voltage * current, 1)
        soc = a1b[15]

        return {
            "address": address,
            "voltage": voltage,
            "current": current,
            "power": power,
            "soc": soc,
            "cells": cells
        }
    except Exception:
        return None


def background_updater():
    """
    Runs in a separate thread.
    Continuously reads all batteries and updates the shared `latest_data` dictionary.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    while True:
        new_data = {}
        for name, (addr, proto) in BATTERIES.items():
            if proto == "jbd":
                result = loop.run_until_complete(fetch_jbd(name, addr))
            else:
                result = loop.run_until_complete(fetch_ecoworthy(name, addr))
            if result:
                new_data[name] = result

        # Safely update the shared data dictionary
        with update_lock:
            latest_data.clear()
            latest_data.update(new_data)

        time.sleep(10)   # Update every 10 seconds


# =============================================================================
# FLASK WEB ROUTES
# =============================================================================

@app.route("/")
def dashboard():
    """Serve the main dashboard HTML page."""
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/data")
def api_data():
    """Return the latest battery data as JSON for the JavaScript frontend."""
    with update_lock:
        return jsonify(latest_data)


# =============================================================================
# START THE APPLICATION
# =============================================================================

if __name__ == "__main__":
    # Start the background thread that reads the batteries
    threading.Thread(target=background_updater, daemon=True).start()

    print("🌐 Dashboard running at http://127.0.0.1:5000  (with ECO-WORTHY support)")
    print("   Keep this terminal open while using the dashboard.")
    
    # Start the Flask web server
    app.run(host="0.0.0.0", port=5000, debug=False)