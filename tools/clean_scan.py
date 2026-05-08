#!/usr/bin/env python3
"""
clean_scan.py - Safe Bluetooth Device Scanner
=============================================

A simple, safe diagnostic tool that scans for nearby Bluetooth Low Energy (BLE) devices
without attempting to connect to any of them.

This is the safest way to discover your batteries and other BLE devices. It is very
useful for:
- Verifying that a battery is advertising (visible)
- Finding the correct MAC address of a new battery
- Troubleshooting when a battery is not showing up in the main programs

It does NOT try to connect or send any commands — it only listens for advertisements.

Usage:
    python3 clean_scan.py
"""

import asyncio
# Bleak is the modern Bluetooth Low Energy library for Python
from bleak import BleakScanner


async def main():
    """
    Main function that performs a Bluetooth scan.
    
    This is a "safe mode" scan — it only listens for advertisement packets
    and never attempts to connect to any device.
    """
    print("🔍 Scanning for Bluetooth devices (safe mode, no connections)...\n")

    # Perform the scan for up to 15 seconds
    # BleakScanner.discover() returns a list of BLEDevice objects
    devices = await BleakScanner.discover(timeout=15.0)

    # Print a summary
    print(f"Found {len(devices)} device(s):\n")

    # Loop through each discovered device and print useful information
    for d in devices:
        # Use the device name if available, otherwise show "Unnamed"
        name = d.name or "Unnamed"

        # Print in a clean, aligned format:
        # Name (padded to 25 characters) | MAC Address
        print(f"📡 {name:<25} | {d.address} ")


# =============================================================================
# Program entry point
# =============================================================================
if __name__ == "__main__":
    # This is the standard way to run an asyncio-based program in Python
    asyncio.run(main())