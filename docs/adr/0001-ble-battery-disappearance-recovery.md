# 0001. Retain last-known-good data and auto-recover the BLE adapter when batteries drop off the dashboard

Date: 2026-05-30
Status: Accepted

## Context

`dashboard.py` polls multiple LiFePO4 batteries over Bluetooth LE (via `bleak` + `aiobmslib`) in a
single background thread and serves readings as JSON to a web UI. The service runs unattended on a
headless Pi-class server.

After hours of continuous operation, batteries would disappear from the dashboard and never return
within the same session. The only known remediation was a full reboot or
`systemctl restart bluetooth`. Two distinct root causes were identified:

**Root cause 1 — application state wipe.** The poll loop called `latest_data.clear()` at the start
of every cycle, then re-populated only the batteries that responded. A single transient BLE miss
(normal on a busy 2.4 GHz band) permanently removed a battery from the UI until the process was
restarted.

**Root cause 2 — wedged BLE adapter.** Even after fixing the state wipe, batteries that had been
absent for several cycles never reappeared within the session. The BlueZ/HCI adapter wedges at the
kernel/stack layer after extended scanning — a documented BLE-on-Linux failure mode that is
invisible to Python. No application-level retry can recover from this; only a hardware/stack reset
does.

## Decision

The fix was implemented in two layers:

**Layer 1 — retain last-known-good data.**
Instead of clearing state each cycle, the poll loop now merges fresh readings into a persistent
`latest_data` dict. Batteries that miss a cycle keep their previous values. A `misses` counter is
tracked per battery; the `/api/data` response includes `stale`, `age_seconds`, and `last_seen`
fields so the web UI can dim (rather than remove) a stale card. Each battery gets up to
`FETCH_ATTEMPTS` (2) connection attempts before a miss is counted. The poll loop is wrapped in
`try/except BaseException` so it cannot die silently.

**Layer 2 — in-process adapter watchdog.**
When any battery reaches `RECOVER_AFTER_MISSES` (3) consecutive misses, `_recover_adapter()` is
called. It uses the `bluetooth-auto-recovery` library's `recover_adapter(hci, mac)` to power-cycle
the BLE adapter in-process — the programmatic equivalent of `systemctl restart bluetooth`. Recovery
is rate-limited to once per `RECOVER_COOLDOWN_SECONDS` (300 s). Escalation is graduated: the first
attempt uses `gone_silent=False` (soft power-cycle); only if that fails does it escalate to
`gone_silent=True` (USB re-enumeration). If the library is unavailable or the call raises, the
watchdog falls back to shelling out to `systemctl restart bluetooth`.

## Alternatives considered

- **Restart the whole service/process on failure** — a service restart does not fix a wedged
  adapter; only the adapter/stack reset does. It also drops the HTTP server and discards data for
  all healthy batteries simultaneously. Rejected.

- **Require the operator to reboot or run `systemctl restart bluetooth` manually** — this is the
  status quo that caused the problem. Unattended headless operation is a design goal of the project.
  Rejected.

- **Maintain a persistent BLE connection per battery** — would avoid repeated scanning, reducing
  adapter stress. However it is a significant rewrite of the polling model, BLE adapters cap
  simultaneous connections (typically 7), and the connect-per-poll pattern is what `aiobmsble`
  expects. Not pursued.

- **Always use `gone_silent=True` (USB reset) on every recovery attempt** — simpler code but
  forces a disruptive USB re-enumeration even for soft wedges that a gentle power-cycle would fix.
  Rejected in favour of graduated escalation.

## Consequences

- **New dependency.** `bluetooth-auto-recovery` is added to `requirements.txt`. If the library is
  not installed, the watchdog degrades gracefully to the `systemctl` shell fallback.

- **Privilege requirement.** `recover_adapter()` opens the kernel Bluetooth management socket,
  which requires `CAP_NET_ADMIN`. The systemd service unit must include
  `AmbientCapabilities=CAP_NET_ADMIN` (or run as root, or grant the fallback via `sudoers`). This
  is documented in the README.

- **Recovery is best-effort and rate-limited.** A battery that is genuinely out of range or
  powered off will not continuously thrash the adapter; the 300-second cooldown and the miss
  threshold together prevent that.

- **Single-adapter assumption.** The watchdog targets the lowest-index HCI adapter (`hci0`).
  Hosts with multiple adapters get a warning log entry but the behaviour is otherwise unchanged.
  The project's target hardware (Pi-class servers) virtually always has a single adapter.

- **Stale UI state is visible.** Layer 1 means cards never disappear from the dashboard during a
  transient outage; they dim instead. This is the desired behaviour for an unattended display, but
  operators should understand that a dimmed card reflects the last-known-good reading, not a live
  reading.

## Revisit if

- The `bluetooth-auto-recovery` library's API changes and `recover_adapter()` is removed or its
  signature changes — the wrapper will need updating.
- The service is deployed on a host with more than one BLE adapter and the single-adapter
  assumption causes incorrect recovery targeting.
- The graduated escalation (`gone_silent=False` then `True`) proves ineffective in production
  (i.e., gentle cycles consistently fail and USB resets are always needed) — at that point,
  defaulting to `gone_silent=True` on the first attempt may be simpler.
- A future version of BlueZ fixes the adapter-wedging behaviour under extended scanning, making the
  watchdog unnecessary.
