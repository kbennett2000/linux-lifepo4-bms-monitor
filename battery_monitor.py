#!/usr/bin/env python3
"""
LiFePO4 Battery Monitor - Terminal Version
==========================================

This script monitors multiple LiFePO4 batteries over Bluetooth Low Energy (BLE)
on Ubuntu/Linux.

Supported batteries:
- Standard JBD / Jiabaida / Daly / Overkill BMS (most common)
- ECO-WORTHY BMS (custom protocol - reverse engineered in this project)

Features:
- Reads voltage, current, power, SOC, individual cell voltages
- Supports multiple batteries simultaneously
- Clean, readable output updated every ~25 seconds
- Designed to be easy to understand and extend

How it works:
1. For JBD batteries: Uses the excellent 'aiobmsble' library
2. For ECO-WORTHY: We manually connect and send the same commands the phone app uses,
   then parse the raw BLE notification packets.

Author: Kris Bennett (May 2026)
"""

import asyncio
import time
# Bleak is the modern Bluetooth Low Energy library for Python
from bleak import BleakScanner, BleakClient
# aiobmsble contains ready-made support for the common JBD BMS protocol
from aiobmsble.bms.jbd_bms import BMS

# =============================================================================
# CONFIGURATION - Edit this section to add/remove your batteries
# =============================================================================

BATTERIES = {
    # Format: "friendly_name": ("MAC_ADDRESS", "protocol_type")
    "200ah_01": ("A4:C1:37:55:C8:D3", "jbd"),
    "200ah_02": ("A4:C1:37:55:C2:29", "jbd"),
    "330ah":    ("A4:C1:37:25:C4:4D", "jbd"),
    "ecoworthy": ("E2:E7:79:8A:56:A3", "ecoworthy"),
}

# Global variables to store the latest raw packets from the ECO-WORTHY battery.
# These are updated by the notification handler and read by the parser.
ecoworthy_a1 = None   # Packet starting with "e2e7798a56a3a1..." (contains SOC + current)
ecoworthy_a2 = None   # Packet starting with "e2e7798a56a3a2..." (contains cell voltages)


def parse_ecoworthy():
    """
    Parses the latest raw notification packets from the ECO-WORTHY BMS.

    This function is called after we receive fresh data.
    It extracts:
        - Cell voltages (from A2 packet)
        - Voltage (sum of cells)
        - Current (from A1 packet)
        - Power (voltage × current)
        - SOC (from A1 packet)
    """
    global ecoworthy_a1, ecoworthy_a2

    # If we haven't received both packets yet, we can't parse anything
    if not ecoworthy_a2 or not ecoworthy_a1:
        return None

    # --- Parse A2 packet (cell voltages) ---
    a2 = bytes.fromhex(ecoworthy_a2)                    # Convert hex string to raw bytes
    cells = []                                          # List to hold the 4 cell voltages
    for i in range(4):                                  # There are 4 cells
        # Each cell voltage is 2 bytes, big-endian, in millivolts
        mv = int.from_bytes(a2[14 + i*2 : 16 + i*2], "big")
        cells.append(round(mv / 1000, 3))               # Convert mV → V with 3 decimal places

    voltage = round(sum(cells), 2)                      # Total pack voltage

    # --- Parse A1 packet (SOC + Current) ---
    a1 = bytes.fromhex(ecoworthy_a1)

    # Current is a signed 16-bit integer at byte offset 20, scaled by 0.1A
    # Negative value = battery is discharging
    current_raw = int.from_bytes(a1[20:22], "big", signed=True)
    current = round(current_raw / 10, 2)

    power = round(voltage * current, 1)                 # Calculate power in Watts

    # SOC (State of Charge) is stored as a single byte at position 15
    soc = a1[15]

    # Print the results in a clean format
    print(f"   Voltage      : {voltage:.2f} V")
    print(f"   Current      : {current:.2f} A")
    print(f"   Power        : {power:.1f} W")
    print(f"   SOC          : {soc}%")
    print(f"   Cells        : {cells}")


async def read_jbd_battery(name: str, address: str):
    """
    Reads data from a standard JBD/Jiabaida-style BMS using the aiobmsble library.

    This is the easy path for most batteries.
    """
    print(f"\n🔋 Reading {name} ({address}) ...")
    try:
        # Find the device on Bluetooth (gives us a proper BLEDevice object)
        device = await BleakScanner.find_device_by_address(address, timeout=8.0)
        if device is None:
            print("   ❌ Device not found")
            return

        # Connect and read data using the pre-built JBD parser
        async with BMS(ble_device=device) as bms:
            data = await bms.async_update()

            # Print all available values
            print(f"   Voltage      : {data.get('voltage', 0):.2f} V")
            print(f"   Current      : {data.get('current', 0):.2f} A")
            print(f"   SOC          : {data.get('battery_level', 0)}%")
            print(f"   Power        : {data.get('power', 0):.1f} W")
            print(f"   Temperature  : {data.get('temperature', 0):.1f} °C")
            print(f"   Cell ΔV      : {data.get('delta_voltage', 0)*1000:.1f} mV")
            print(f"   Cycles       : {data.get('cycles', 0)}")
            if cells := data.get('cell_voltages'):
                print(f"   Cells        : {[f'{v:.3f}' for v in cells]}")

    except Exception as e:
        print(f"   ❌ Error: {type(e).__name__} - {e}")


async def read_ecoworthy_battery(name: str, address: str):
    """
    Reads data from the ECO-WORTHY BMS using raw BLE commands and notifications.

    This is a custom implementation because this battery uses a different
    service and characteristic layout than standard JBD batteries.
    """
    global ecoworthy_a1, ecoworthy_a2
    print(f"\n🔋 Reading {name} (ECO-WORTHY) ...")

    try:
        device = await BleakScanner.find_device_by_address(address, timeout=10.0)
        if not device:
            print("   ❌ Device not found")
            return

        async with BleakClient(device) as client:
            # This inner function is called automatically every time the BMS sends data
            def notification_handler(sender, data):
                global ecoworthy_a1, ecoworthy_a2
                hex_str = data.hex()
                if hex_str.startswith("e2e7798a56a3a1"):
                    ecoworthy_a1 = hex_str          # Contains SOC and current
                elif hex_str.startswith("e2e7798a56a3a2"):
                    ecoworthy_a2 = hex_str          # Contains cell voltages

            # Tell the BMS we want to receive notifications on this characteristic
            await client.start_notify(
                "0000fff1-0000-1000-8000-00805f9b34fb",
                notification_handler
            )

            # Send the two standard JBD-style commands that the phone app uses
            await client.write_gatt_char(
                "0000fff2-0000-1000-8000-00805f9b34fb",
                bytes.fromhex("dda50300fffd77")   # Request basic info (SOC, voltage, etc.)
            )
            await asyncio.sleep(1.2)

            await client.write_gatt_char(
                "0000fff2-0000-1000-8000-00805f9b34fb",
                bytes.fromhex("dda50400fffc77")   # Request individual cell voltages
            )
            await asyncio.sleep(1.5)

            # Parse and display the received data
            parse_ecoworthy()

    except Exception as e:
        print(f"   ❌ Error: {type(e).__name__} - {e}")


async def main():
    """
    Main loop of the program.
    Continuously reads all batteries in sequence and prints the results.
    """
    while True:
        print(f"\n=== Battery Monitor @ {time.strftime('%H:%M:%S')} ===")

        # Read each battery one at a time (prevents Bluetooth "InProgress" errors)
        for name, (addr, protocol) in BATTERIES.items():
            if protocol == "jbd":
                await read_jbd_battery(name, addr)
            else:
                await read_ecoworthy_battery(name, addr)
            await asyncio.sleep(3)          # Small delay between batteries

        print(f"\n--- All batteries read — sleeping 25 seconds ---\n")
        await asyncio.sleep(25)


# =============================================================================
# Program entry point
# =============================================================================
if __name__ == "__main__":
    # This is the standard way to run an asyncio program
    asyncio.run(main())