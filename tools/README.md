# Diagnostic Tools

These scripts were used during development and are useful for troubleshooting or
adding support for new batteries. Run them from the project root with the venv
active (`source venv/bin/activate`).

| Script | What it does | When to use it |
|---|---|---|
| `clean_scan.py` | Safe BLE scan — lists nearby Bluetooth devices without connecting | Find a new battery's MAC address; confirm a battery is advertising |
| `diagnose_ecoworthy.py` | Connects and dumps all GATT services / characteristics | Adding support for an unknown BMS |
| `test_ecoworthy.py` | Sends raw commands and prints raw response packets | Reverse-engineering a new protocol |

> **Before running `diagnose_ecoworthy.py` or `test_ecoworthy.py`:** both scripts have an ECO-WORTHY MAC address hardcoded near the top (`address = "E2:E7:79:8A:56:A3"`). You **must edit that line** to match your battery's MAC address before running the script, or it will fail to connect.

### Quick examples

```bash
# Find the MAC address of a new battery
python3 tools/clean_scan.py

# Inspect a specific battery's BLE services
# (edit the hardcoded MAC near the top of the script first)
python3 tools/diagnose_ecoworthy.py

# Send raw commands and print response packets
# (edit the hardcoded MAC near the top of the script first)
python3 tools/test_ecoworthy.py
```

None of these tools modify the battery. They're read-only diagnostics.
