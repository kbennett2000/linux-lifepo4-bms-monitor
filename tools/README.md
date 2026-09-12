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

---

## `bt_health.py` — Bluetooth stack health report

Most "the battery disappeared" problems are the host's Bluetooth stack, not the battery.
This packages the journal queries that distinguish the two.

```bash
python3 tools/bt_health.py                 # last 7 days
python3 tools/bt_health.py --days 30
python3 tools/bt_health.py --since "2026-08-19" --until "2026-08-22"
```

Read-only — it runs `journalctl` and nothing else. What good looks like:

| Section | Healthy |
|---|---|
| bluetoothd segfaults | **zero**. Any crash is a host-stack bug; check the `bluez` package version. |
| adapter recoveries | rare, ≤1/week. Each one power-cycles the radio and can strand a battery that had a link open. |
| whole-bank scan outages | **zero**. This is the adapter going deaf, and it is what killed the ECO-WORTHY. |
| kernel wedge lines | near zero. Dozens a day means the adapter is struggling. |
| per-battery failures | roughly even. One battery far worse is that battery or where it sits; all of them together is the adapter. |

## `btmon-ring.sh` / `btmon-ring.service` — rolling HCI capture

Every diagnosis in this project has been slowed by the same missing artifact: nobody ever
captured what happened on the air when a battery went silent. This keeps a rolling week of
HCI traffic so the trace already exists when it is needed.

```bash
sudo cp tools/btmon-ring.sh /usr/local/bin/btmon-ring.sh
sudo cp tools/btmon-ring.service /etc/systemd/system/
sudo systemctl enable --now btmon-ring
```

Roughly a few MB per hour, pruned after 7 days. It only listens; it never connects.

### What to look for after a battery goes silent

Find the capture covering the moment it went quiet (files are named by UTC start time)
and open it with `btmon -r /var/log/btmon/hci-<stamp>.btsnoop`. Four questions, in order:

1. **The `HCI Disconnection Complete` reason code on that battery's handle.** This is the
   single most important byte in the file, because it says whether the battery was *told*
   the link was ending:
   - `0x16` — local host terminated the connection, and the link-layer terminate was
     acknowledged. The battery knew. If it still never re-advertised, the theory that it
     only fails on unclean losses is wrong, and [ADR 0003](../docs/adr/0003-ecoworthy-link-exposure.md)
     needs revisiting.
   - `0x08` (supervision timeout) or `0x22` (LL response timeout) — the peer was already
     unreachable and no terminate got through. That is the lethal case.
2. **Which scan path the adapter uses** — `LE Set Extended Scan Enable (0x2042)` versus
   legacy `LE Set Scan Enable (0x200c)` — and whether either is being answered with
   `Command Disallowed`. Some Realtek controllers claim extended-scan support they do not
   have, and the kernel's handling of that has been changing.
3. **Whether ACL data or `Number of Completed Packets` events keep arriving on a handle
   after the host thinks the link is gone.** That is controller/host state desync, and it
   matches the `ACL packet for unknown connection handle` lines in the kernel log.
4. **The last packets before the notification stream stopped.** For the ECO-WORTHY, the
   A1/A2 notification frames stopping *before* anything was written is the first event of
   the incident and the least understood part of it.
