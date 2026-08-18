# Diagnostic Tools

These scripts were used during development and are useful for troubleshooting or
adding support for new batteries. Run them from the project root with the venv
active (`source venv/bin/activate`).

| Script | What it does | When to use it |
|---|---|---|
| `clean_scan.py` | Safe BLE scan — lists nearby Bluetooth devices without connecting | Find a new battery's MAC address; confirm a battery is advertising |
| `diagnose_ecoworthy.py` | Connects and dumps all GATT services / characteristics | Adding support for an unknown BMS |
| `test_ecoworthy.py` | Reads one battery with its `aiobmsble` driver and prints the decoded values | Checking a battery is understood before adding it to `config.json`; diagnosing one that reports nothing |

> **Picking a battery:** `diagnose_ecoworthy.py` and `test_ecoworthy.py` both default to the first ECO-WORTHY entry in `config.json`, and accept a MAC address as the first argument to override that. No editing required.

### Quick examples

```bash
# Find the MAC address of a new battery
python3 tools/clean_scan.py

# Inspect a specific battery's BLE services
python3 tools/diagnose_ecoworthy.py

# Read one battery and print the decoded values
python3 tools/test_ecoworthy.py

# ...with raw frames and CRC diagnostics
python3 tools/test_ecoworthy.py -v

# ...a specific battery, using a specific protocol
python3 tools/test_ecoworthy.py AA:BB:CC:DD:EE:FF --protocol jbd
```

`clean_scan.py` and `diagnose_ecoworthy.py` never write to a battery. `test_ecoworthy.py`
sends the same protocol handshake the dashboard does — nothing that changes a setting, but
it is not purely passive. None of them alter BMS configuration.
