#!/usr/bin/env python3
"""
test_ecoworthy.py - Raw BLE Command Test Tool for ECO-WORTHY BMS
================================================================

This is a low-level diagnostic / development tool that:
- Connects directly to the ECO-WORTHY battery
- Enables notifications on the correct characteristic
- Sends the exact same commands the phone app uses
- Prints the raw hex packets received from the battery

Why this tool exists:
- It helps us understand and debug the custom ECO-WORTHY protocol
- Useful when developing or extending support for this (or similar) batteries
- Great for learning how BLE communication with these BMS works

This script is intentionally "noisy" — it prints raw data so you can see exactly what the battery is sending.

Usage:
    python3 test_ecoworthy.py
"""

import asyncio

# Bleak is the modern Bluetooth Low Energy library for Python
from bleak import BleakScanner, BleakClient


# =============================================================================
# CONFIGURATION
# =============================================================================

ADDRESS = "E2:E7:79:8A:56:A3"

# These are the specific UUIDs used by the ECO-WORTHY BMS
SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
WRITE_UUID   = "0000fff2-0000-1000-8000-00805f9b34fb"   # Where we send commands
NOTIFY_UUID  = "0000fff1-0000-1000-8000-00805f9b34fb"   # Where the battery sends responses


async def main():
    """
    Main test function.

    This connects to the battery, subscribes to notifications, and sends
    the two standard commands that the phone app uses to request data.
    """
    print(f"Connecting to ECO-WORTHY {ADDRESS}...")

    # Find the device on Bluetooth (required to get a proper BLEDevice object)
    device = await BleakScanner.find_device_by_address(ADDRESS, timeout=10.0)
    if not device:
        print("❌ Device not found")
        print("   Tip: Make sure the phone app is completely closed and Bluetooth has been toggled off/on.")
        return

    # Connect to the battery
    async with BleakClient(device) as client:
        print("✅ Connected!\n")

        # Enable notifications so the battery can send us data automatically
        await client.start_notify(
            NOTIFY_UUID,
            lambda sender, data: print(f"RX: {data.hex()}")
        )
        print("Notifications enabled — waiting for responses from the BMS...\n")

        # Send the first command: Request basic information (voltage, SOC, current, etc.)
        cmd = bytes.fromhex("dda50300fffd77")
        await client.write_gatt_char(WRITE_UUID, cmd, response=False)
        print("Sent basic data request (dda50300fffd77)...")

        await asyncio.sleep(2)   # Give the battery time to respond

        # Send the second command: Request individual cell voltages
        cmd = bytes.fromhex("dda50400fffc77")
        await client.write_gatt_char(WRITE_UUID, cmd, response=False)
        print("Sent cell voltage request (dda50400fffc77)...")

        await asyncio.sleep(2)   # Give the battery time to respond

        print("\nTest complete. Raw packets shown above.")


# =============================================================================
# Program entry point
# =============================================================================
if __name__ == "__main__":
    # This is the standard way to run an asyncio program
    asyncio.run(main())