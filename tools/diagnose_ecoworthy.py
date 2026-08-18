#!/usr/bin/env python3
"""
diagnose_ecoworthy.py - BLE Service & Characteristic Explorer
=============================================================

This diagnostic tool connects to the ECO-WORTHY battery (or any BLE device)
and prints a complete list of all GATT services and characteristics it exposes.

Why this is useful:
- Helps you understand the internal structure of a new/unknown BMS
- Shows exactly which services and characteristics are available
- Essential when reverse-engineering a new battery model
- Great for troubleshooting connection issues

It is completely safe — it only reads the device structure and never writes data.

Usage:
    python3 diagnose_ecoworthy.py                     # from config.json
    python3 diagnose_ecoworthy.py AA:BB:CC:DD:EE:FF   # explicit MAC
"""

import asyncio
import sys
from pathlib import Path

# Bleak is the modern Bluetooth Low Energy library for Python
from bleak import BleakClient, BleakScanner


async def main():
    """
    Main diagnostic function.

    1. Finds the battery by its MAC address
    2. Connects to it
    3. Prints every service and every characteristic with its properties
    """
    # Which battery to inspect: a MAC on the command line, else the first
    # ECO-WORTHY entry in config.json.
    if len(sys.argv) > 1:
        address = sys.argv[1]
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from bms_config import battery_entries, load_config
        entries = battery_entries(load_config())
        match = next((e for e in entries.values() if e["protocol"] == "ecoworthy"), None)
        if match is None:
            sys.exit("No ecoworthy battery in config.json — pass a MAC address instead.")
        address = match["address"]

    print(f"🔍 Connecting to ECO-WORTHY battery {address}...\n")

    # Find the device on Bluetooth (this is required to get a proper BLEDevice object)
    device = await BleakScanner.find_device_by_address(address, timeout=10.0)

    if not device:
        print("❌ Could not find the battery.")
        print("   Make sure the phone app is completely closed and Bluetooth is toggled off/on.")
        return

    # Connect to the battery
    async with BleakClient(device) as client:
        print("✅ Connected successfully!\n")
        print("=== SERVICES AND CHARACTERISTICS ===\n")

        # Loop through every GATT service the device advertises
        for service in client.services:
            print(f"Service: {service.uuid}")

            # For each service, list all its characteristics
            for char in service.characteristics:
                # Convert the list of properties (read, write, notify, etc.) into a readable string
                props = ", ".join(char.properties) if char.properties else "none"

                print(f"   └─ Characteristic: {char.uuid}  ({props})")

        print("\n=== End of services and characteristics ===")


# =============================================================================
# Program entry point
# =============================================================================
if __name__ == "__main__":
    # This is the standard way to run an asyncio program
    asyncio.run(main())