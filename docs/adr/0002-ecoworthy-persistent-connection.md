# 0002. Hold one BLE connection for the ECO-WORTHY, and stop stranding it

Date: 2026-08-18
Status: Accepted
Supersedes part of [0001](0001-ble-battery-disappearance-recovery.md)

## Context

The ECO-WORTHY battery worked for a while under `dashboard.py`, then its BLE module stopped
advertising entirely — invisible to scans, not merely refusing connections. The only recovery
was discharging the pack to under-voltage cutoff and recharging it, i.e. a hard reset of the
BMS MCU. The official phone app monitored the same battery indefinitely. The three JBD packs
on the same adapter were unaffected.

The ECO-WORTHY path was the only BLE code in the project that did not go through `aiobmsble`.
It was hand-rolled raw `bleak`, duplicated in four places, and it differed from the phone app
in three ways:

**It spoke the wrong protocol.** It wrote `dd a5 03 00 ff fd 77` and `dd a5 04 00 ff fc 77`
to characteristic FFF2 — verbatim JBD/Xiaoxiang frames. This BMS is not a JBD device: its
notifications arrive as its own MAC followed by a frame-type byte (`e2e7798a56a3a1…`), and
the real protocol is MODBUS-framed with a CRC16. The writes were never answered. Data arrived
only because the module autonomously broadcasts A1/A2 frames once you subscribe to FFF1, so
the feature appeared to work while pushing two malformed frames into a CRC-checked parser on
every poll.

**It reconnected constantly.** Poll cycle plus a 10 s sleep, up to two attempts per battery,
forever — on the order of 1,500–3,000 connect/subscribe/disconnect cycles a day against a
module the phone app connects to exactly once.

**It had no reliable teardown.** `stop_notify` appeared nowhere in the project, teardown relied
entirely on `async with BleakClient`, and `except Exception: return None` swallowed a failed
disconnect silently.

The mechanism this points at: a BLE peripheral that believes it is still connected stops
advertising. `bleak`'s own BlueZ backend documents that *"The BlueZ backend does not disconnect
devices when the application closes or crashes"* (bluez/bluez#89). Between that, the adapter
power-cycles from 0001's watchdog, and `systemctl restart bluetooth`, there were many ways for
the module to be left holding a link nothing would ever terminate.

Worse, 0001's watchdog fired on `max(miss_counts.values())` — so *one* battery missing three
cycles power-cycled the adapter for the whole bank. The ECO-WORTHY, being the flakiest device,
was almost certainly driving every recovery event, each of which could strand it further.

## Decision

**Use the `aiobmsble` ECO-WORTHY driver.** It was already an installed dependency and simply
never imported. It sends the real CRC16-protected MODBUS init commands, validates the CRC on
every received frame, handles both protocol revisions (the `/100` and `/10` current scalings),
derives the MAC frame prefix from the device address rather than hardcoding it, reads
`cell_count` dynamically instead of assuming four, and reports temperature.

**Route every battery through one shared `bms_driver.read_battery()`.** Protocol dispatch is a
name lookup into `aiobmsble.bms.*`, so any of its ~40 drivers can be selected from
`config.json`, and an unrecognised protocol now fails at startup instead of silently falling
through to a default branch.

**Hold one connection for batteries marked `"persistent": true`.** This is what the phone app
does, and it takes teardowns from ~1,500/day to ~1/restart. It also removes the per-cycle
active scan for that battery — the extended-scanning load 0001 blames for wedging the adapter.
Scoped per battery and defaulting to `false`, so the JBD packs keep the connect-per-poll model
and this change has one variable.

**Add a release switch.** Because these modules accept one client at a time, a held link locks
the phone app out. `POST /api/ble/release` (and a button on the card) drops the link for a
clamped number of minutes; the poll loop skips that battery and reconnects when it expires.

**Make every teardown graceful.** Explicit `stop_notify` before `disconnect()` (`BaseBMS.disconnect()`
does not do this despite its docstring), teardown from a `finally`, a SIGTERM/SIGINT handler so
`systemctl stop/restart` closes links instead of dropping them, and failures logged rather than
swallowed. On startup and before any adapter power-cycle, `close_stale_connections_by_address()`
sweeps away links BlueZ still holds from a previous process.

**Gate adapter recovery on a quorum.** A wedged adapter loses every battery at once, so recovery
now requires a majority of batteries at the miss threshold, not just one.

## Consequences

- **0001's "maintain a persistent BLE connection per battery — not pursued" is partly reversed.**
  Its stated reason ("the connect-per-poll pattern is what `aiobmsble` expects") was wrong:
  `BaseBMS` defaults to `keep_alive=True` and explicitly supports held connections. The effort
  argument was real, which is why this is scoped to the one battery that was failing.
- **A single dead battery no longer triggers adapter recovery.** Correct — no adapter reset fixes
  a dead battery — but a genuinely wedged adapter with only one battery configured now needs that
  battery to be the quorum, which it is.
- **The API is no longer read-only.** Two POST endpoints exist. Both are unauthenticated like the
  rest of the API; the blast radius is dropping a BLE link for a few minutes.
- **Temperature and ΔV now populate for ECO-WORTHY**, and `cycles` is explicitly `null` rather
  than a fabricated `0`.
- **This may not be a complete fix.** The wedge itself is a peripheral firmware defect — no legal
  sequence of BLE operations from a central should be able to put a module into a state only an
  MCU reset clears. What this does is remove every host-side trigger and make the Linux client
  behave like the one client known to work with this battery.

## Revisit if

- The module still wedges with a persistent connection and graceful teardown. At that point the
  next step is instrumentation, not more code: a long-running `btmon` capture plus an advertising
  logger to pin the moment it stops advertising, correlated against `[recovery]` lines and
  systemd restarts. Capturing the phone app's own traffic (Android HCI snoop log) would give a
  known-good reference to diff against.
- `aiobmsble`'s ECO-WORTHY driver rejects this unit's frames on CRC. That makes it an upstream
  issue rather than a local one.
- Holding one link degrades the JBD packs' reads through scan/connect contention on a single
  adapter. A second USB dongle dedicated to the ECO-WORTHY would decouple them entirely, and
  would also mean recovery power-cycling `hci0` could never touch that link.
