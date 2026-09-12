# 0003. Minimise the ECO-WORTHY's link exposure on a shared adapter

* Status: accepted, **as a decision under uncertainty** — see "What would change this"
* Date: 2026-09-12
* Supersedes: the persistent-connection decision in
  [0002](0002-ecoworthy-persistent-connection.md). The rest of 0002 stands.

## Context

0002 held one BLE connection open for the ECO-WORTHY and made every teardown graceful, on
the theory that ~1,500 daily connect/disconnect cycles were wedging its BLE module. That
was deployed on 2026-08-18. The battery died again on 2026-08-20, about 48 hours later,
and has not advertised since. Meanwhile the owner ran the vendor phone app only, for a
month, with no failures at all.

### What the journal actually shows

Reconstructed from the server's journal (2026-05-27 → 2026-09-12), the BlueZ 5.85 source,
and the upstream projects.

| Time (UTC) | Event |
|---|---|
| 08-18 20:19 | Persistent build live. |
| 08-19 04:30 → 21:06 | `bluetoothd` segfaults four times — the first in 84 days of journal. The ECO-WORTHY survives all four. |
| 08-20 20:58:54 → 21:01 | **The only whole-bank scan outage in the entire window.** All three JBD packs go missing back-to-back. The adapter has stopped returning advertisements. |
| ~08-20 21:00 | The held link dies. |
| 08-20 21:00:30 | We log `UNLIKELY_ERROR`, then drop the session. |
| 08-20 21:05 | Our recovery power-cycles the adapter. The JBDs come back. The ECO-WORTHY does not, ever (1,736 consecutive misses until it was removed from config). |
| 08-20 21:28, 21:36 | `bluetoothd` segfaults twice more, both during connect attempts against a stale cached device object. |

Three details took real work to establish and each overturned an earlier belief:

**The `UNLIKELY_ERROR` was not the battery talking.** BlueZ's `process_error()` in
`src/shared/gatt-client.c` returns `BT_ATT_ERROR_UNLIKELY` (0x0E) whenever a pending ATT
operation is completed with a null PDU — which is exactly what `shared/att.c`'s
`timeout_cb` does after its 30-second request timeout, immediately before calling
`io_shutdown()` on the channel. So 0x0E here means "BlueZ gave up and tore the link down",
and the 30-second timeout puts the real link loss at ~21:00:00 — inside the scan outage.
Reading it as a protocol-level refusal from the module, which is what it looks like, points
the investigation in precisely the wrong direction.

**The power-cycle was ~5 minutes too late to be the cause.** It is still a hazard, and it
plausibly prevented recovery, but the adapter had already stopped hearing everything before
we touched it. The wedge is the primary event; our recovery arrived at the scene.

**The old code power-cycled the adapter far more than anyone knew.** The journal holds
**2,678** old-format recovery lines against **129** new-format ones. The old counts are
concentrated in five bursts sharing a single timestamp each — Python's block-buffered
`print()` to a pipe held them until the buffer filled or the process exited, so every
timestamp in the old epoch is a flush moment rather than an event time. This is also why
an earlier pass through the same journal concluded, wrongly, that recovery had barely ever
fired. Unflushed logging did not just lose detail; it produced a confident wrong answer.

### Why this battery and not the others

The owner confirms that when it is dead the **vendor app cannot find it either**, and that
a server reboot, unplugging the USB dongle, and connecting a charger have all been tried
and do nothing — only ECO-WORTHY's own documented procedure (discharge to low-voltage
cutoff, recharge) revives it. So the stuck state lives in the battery's BLE module, it is
not the host being blind, and it is not a sleep-on-idle mode.

The module appears not to resume advertising after a link ends without a clean
disconnect. Community reports for these packs describe the same "ghost connection", and
the discharge ritual is ECO-WORTHY's own support answer for it. The three JBD packs on the
same adapter survive every wedge, every crash and every power-cycle. The phone app never
triggers it because a phone's radio does not wedge and the app disconnects cleanly at the
end of each short session.

### Why the adapter wedges

The adapter is a Realtek RTL8851BU combo stick, and it is simultaneously the server's BLE
radio *and* its only network uplink — `eno1` has no carrier, so all traffic runs over the
WiFi half of the same chip, sharing one antenna. Linux runs this chip's WiFi/BT
coexistence in a degraded legacy profile because the modern one needs firmware that
`linux-firmware` has never shipped. Home Assistant lists every Realtek combo adapter as
unsupported for connection-heavy BLE, and `bluetooth-auto-recovery` exists specifically
because Realtek adapters stop responding after hours of scanning.

Separately, the `bluetoothd` crashes are a known BlueZ 5.85 use-after-free in GATT client
teardown that upstream describes as firing "when a device disconnects" (Ubuntu LP #2164630,
upstream `d01616f0`). The fix is in `bluez 5.85-4ubuntu0.2`. The crashes began 8 hours
after 0002 shipped, and 0002's explicit `stop_notify` immediately followed by `Disconnect`
on a link BlueZ was already tearing down is a plausible trigger for exactly that reentrancy.

## Decision

**Treat link exposure, not teardown count, as the quantity to minimise — on this adapter.**
The lethal event is an adapter-level link loss. Its rate is set by the adapter, not by how
often we disconnect. A held link is open during every wedge; a bounded ten-minute read is
open during well under 1% of them. So on the shared adapter the ECO-WORTHY is
`persistent: false`, `interval_seconds: 600`, `attempts: 1`.

This is deliberately the opposite of upstream's default, and upstream is not wrong — a held
link *is* better on a radio that works. The knob is per-battery precisely so this reverses
when the battery moves to a dedicated adapter, which is the real fix.

**Never connect to a battery we have not just heard advertise.** One scan per adapter per
cycle, and a device is only accepted with a real advertisement (`rssi != -127`). On BlueZ,
`find_device_by_address` returns cached device objects on any D-Bus property change,
including the burst a restarting `bluetoothd` emits — which is how we came to attempt
connections to a battery that had been silent for half an hour, twice immediately preceding
a daemon crash.

**Gate the fragile battery on adapter health, at the adapter level only.** Skip its reads
for 15 minutes after a whole-bank outage, an adapter recovery, or a `bluetoothd` restart.
Gating on individual battery failures was considered and rejected: the JBD packs produce
hundreds of read failures a day on this adapter, so such a gate would essentially never
open, and a battery that is never polled cannot be said to be fixed.

**Recover the adapter only when the adapter looks wedged.** Require *all* recently-seen
batteries on that adapter to be missing, with at least two of them, and age out any battery
unseen for an hour. The old rule fired on a single missing battery; the 0002 rule required
two but counted dead batteries forever, so on 08-21 a dead ECO-WORTHY plus one flaky JBD
power-cycled the adapter 21 times in a day. Cooldown goes from 5 to 30 minutes, links are
brought down and *verified* down first, and the result is checked by scanning — the USB
re-enumeration escalation had never once actually run, because the code returned on the
library's own optimistic report.

**Rebuild the event loop when the host stack restarts.** bleak registers no
`NameOwnerChanged` watcher, so after a `bluetoothd` restart its D-Bus object and GATT
service caches are stale for the life of the loop and every call fails identically. A new
loop is the only thing that clears it.

**Log with `flush=True`, always.** See above for what buffered logging cost.

**Stop calling `stop_notify` before `Disconnect`.** Subscriptions do not persist across
connections for an unbonded peer, so it achieved nothing, and it is the likely trigger for
the BlueZ teardown crash.

## Consequences

- The ECO-WORTHY updates every 10 minutes instead of every 30 seconds. For a battery
  whose SOC moves slowly this is a fair trade for not bricking it; the dashboard shows the
  reading age.
- Poll interval moves from 10 s to 30 s bank-wide, matching the reference deployment of
  these same drivers, and per-cycle scanning drops from one scan per battery to one per
  adapter. This should reduce adapter load substantially on its own.
- A single battery can no longer trigger adapter recovery, and on a dedicated
  single-battery adapter recovery can never fire at all. Both are intended.
- `state/telemetry.jsonl` now records every reading and event, so the next failure leaves
  the battery's last known state behind instead of losing it with the process.
- **This may still not be enough.** It reduces exposure; it does not eliminate it. A wedge
  that starts during the few seconds a read holds the link would still be lethal.

## What would change this

Written down because the evidence here is one incident per code regime, and that is not
enough to be sure of:

- **A `btmon` capture showing a clean terminate that the module ignored.** Then the
  "unclean loss" premise is wrong and the exposure model is beside the point. The rotating
  capture in `tools/btmon-ring.sh` exists to answer this; the specific thing to read is the
  HCI Disconnection Complete reason code on the module's handle — `0x16` means the local
  host terminated and the link-layer terminate was acknowledged, `0x08` or `0x22` mean the
  peer was already unreachable.
- **Any independent radio seeing it advertise during an outage.** Then the host was blind
  and the module was never stuck. The unfiltered scan after 10 minutes missing is the cheap
  in-project version of this check.
- **60 clean days on a dedicated adapter.** At that point `persistent: true` should be
  restored for it, matching upstream, and this ADR's central trade-off retired.

The deliberate experiment that would settle the mechanism in an afternoon — hold a link,
pull the dongle, see whether it recovers — was offered and declined, because a wrong guess
costs a full discharge cycle. That is a reasonable call; it just means the capture is the
route instead.
