# Session Handoff — 2026-05-30

> **Read the dated updates at the bottom first.** Everything above them is the 2026-05-30
> handoff. Its "Current State" and "Pending" sections are long finished, and its deploy
> instructions name the wrong path on the server. The latest update has the server's current
> state.

## Goal

Stabilize the LiFePO4 BMS dashboard (dashboard.py) so batteries stop disappearing from the UI after
hours of run time, polish the code and frontend following code-review and fresh-eyes passes, and
bring all documentation in sync with the actual behavior.

---

## Done

### Phase 1 — "Dropped batteries" fix (committed: 7975416, d9af344 includes adapter watchdog)

- Root cause: `background_updater()` called `latest_data.clear()` every cycle, so a single BLE miss
  made a battery vanish permanently.
- Fix: retain last-known-good data (merge on success, never clear), track per-battery `misses`,
  expose `stale` / `age_seconds` / `last_seen` on `/api/data`, retry each battery up to
  `FETCH_ATTEMPTS` (2) times before counting a miss, guard the poll loop with
  `try/except BaseException` so it can't die silently.
- Frontend dims stale battery cards: `static/js/dashboard.js`, `static/css/dashboard.css`.

### Phase 2 — Adapter-recovery watchdog (committed: d9af344)

- Problem: even with Phase 1, only 2 of 4 batteries appeared after deployment. User confirmed a
  `systemctl restart bluetooth` (or full reboot) was needed to recover them. Diagnosis: the
  BlueZ/HCI adapter wedges at the kernel/BlueZ layer after extended scanning — below Python.
- Fix in `dashboard.py`:
  - When any battery reaches `RECOVER_AFTER_MISSES` (3) consecutive misses,
    `_recover_adapter()` power-cycles the BLE adapter in-process using the
    `bluetooth-auto-recovery` library (`recover_adapter(hci, mac)`).
  - Graduated escalation: first attempt uses `gone_silent=False` (gentle), escalates to
    `gone_silent=True` (USB reset) only if the gentle attempt fails or returns False.
  - Rate-limited by `RECOVER_COOLDOWN_SECONDS` (300 s); falls back to
    `systemctl restart bluetooth` if the library call fails.
  - `bluetooth-auto-recovery` added to `requirements.txt`.
  - README documents the `AmbientCapabilities` / `CAP_NET_ADMIN` systemd requirement.
- **User confirmed this is working in production on 192.168.1.62.**

### Phase 3 — Code/docs polish (committed: 4b29ada; documentation drift committed there too)

Implemented findings from code-reviewer, fresh-eyes, doc-auditor, and doc-writer agents:

**Code / frontend (dashboard.py, static/js/dashboard.js, templates/dashboard.html):**
- Graduated recovery escalation (`gone_silent=False` then `True` — the original always used `True`,
  forcing a USB reset unnecessarily every time).
- Multi-adapter warning logged if `hciconfig` shows more than one adapter.
- `miss_counts` dict cleaned up on successful read so it doesn't grow indefinitely.
- `RECOVER_COOLDOWN_SECONDS` timer now starts from when recovery fires, not from startup.
- Frontend: empty-state guidance text appears after ~60 s if no data arrives (previously silent).
- Frontend: connection-error indicator uses red/amber dot instead of no feedback.
- Frontend: stale indicator de-duplicated (was shown twice on a card).
- Frontend: `formatAge(0)` returns `'just now'` instead of `'0s ago'`.
- `templates/dashboard.html`: corrected stale comment ("Tailwind Play CDN" -> "vendored locally").

**Documentation (README.md, bms_config.py, tools/README.md):**
- `clean_scan.py` example output updated to match actual columnar format with emoji prefix and `|`
  separator.
- `--host` / `BMS_DASHBOARD_HOST` override documented (was completely missing).
- "How It Works" section updated to describe retry, retention, and the watchdog accurately.
- New "Advanced tuning" section documents the 4 constants (`STALE_AFTER_MISSES`, `FETCH_ATTEMPTS`,
  `RECOVER_AFTER_MISSES`, `RECOVER_COOLDOWN_SECONDS`) with a practical tip.
- New "API" section documents `/api/data` (all fields, types, always-present status fields) and
  `/api/config`.
- `tools/README.md`: hardcoded-MAC warning added for `diagnose_ecoworthy.py` and
  `test_ecoworthy.py`, and `test_ecoworthy.py` added to the quick-examples block.
- First-reading time estimate updated to "30–60 seconds" (was "~15 seconds").
- Recovery log grep updated to `grep -E 'recovery|background_updater'`.
- `bms_config.py`: `battery_tuples()` docstring corrected ("legacy code paths" ->
  "all configured batteries").

---

## Current State

**4 files are modified and NOT yet committed** (Phase 3 doc-audit pass):

```
README.md                (+87 / -13)
bms_config.py            (+1  / -1 )
templates/dashboard.html (+1  / -1 )
tools/README.md          (+7  / -0 )
```

The code changes from Phase 3 (dashboard.py, static/js/dashboard.js) are already in commit 4b29ada.
The remaining 4 files are documentation/template fixes that have not been staged.

---

## Pending / Next Session

1. **Commit the 4 unstaged files.** Suggested message:
   ```
   docs: fix documentation drift from code-review / doc-audit pass

   - Update clean_scan.py output format in README example
   - Document --host / BMS_DASHBOARD_HOST override
   - Add Advanced tuning section (4 constants)
   - Add API section (/api/data, /api/config)
   - Correct first-reading time estimate, recovery log grep, watchdog prose
   - Fix bms_config.py battery_tuples() docstring
   - Add hardcoded-MAC warning to tools/README.md
   - Correct Tailwind comment in dashboard.html
   ```

2. **Deploy to 192.168.1.62.**
   ```bash
   # on the server (path corrected 2026-10-27 — see the update at the bottom)
   cd ~/applications/linux-lifepo4-bms-monitor
   git pull
   sudo systemctl restart bms-dashboard
   ```
   No new pip installs needed — `bluetooth-auto-recovery` is already installed on the server from
   Phase 2. The graduated-recovery change is purely a code path tweak.

3. **Verify in production.** After a few hours, check:
   ```bash
   journalctl -u bms-dashboard -f | grep -E 'recovery|background_updater'
   ```
   A gentle-cycle log line should look like:
   `[recovery] recover_adapter(hci0, …, gone_silent=False) -> True`
   If you see `gone_silent=True` being reached, the gentle attempt is failing — investigate why.

---

## Open Questions

- The repo has **no `.gitignore`** and currently commits `venv/` and `.pyc` files. The user has not
  asked to change this, but it is unusual and creates noise in diffs. Worth raising if the user
  ever does a `git status`-based review. (For the user to decide.)
- No automated tests exist. A `test-writer` agent is available in `.claude/agents/test-writer.md`
  if the user wants to pursue this. (For the user to decide.)

---

## Watch Out For

**The dev venv is broken.** `venv/bin/python` resolves to system Python 3.14 but packages are
installed under `venv/lib/python3.12/site-packages`. Worse, `python3.12` is **no longer installed
on this machine** and `python3` on PATH resolves to the broken venv interpreter (so even
`python3 -m pip` fails). Use the absolute system interpreter with the venv packages on the path:
```bash
PYTHONPATH=venv/lib/python3.12/site-packages /usr/bin/python3 dashboard.py --demo
PYTHONPATH=venv/lib/python3.12/site-packages /usr/bin/python3 -m unittest discover -s tests
```
The packages are pure Python and import fine under 3.14, which is also what the server runs.
Do not assume `source venv/bin/activate && python3 ...` works.

**`bluetooth-auto-recovery` is NOT installed in the dev venv.** It is only on the server
(192.168.1.62). The live `recover_adapter()` code path cannot be exercised locally. It was
verified by reading the library source and using stubs.

**Do not run broad file deletions.** The repo tracks `venv/*.pyc` files. A previous session
accidentally deleted 773 tracked `.pyc` files with a cache cleanup and had to restore them via
`git checkout`. Always scope any cleanup commands to specific non-venv paths.

**The `systemctl restart bluetooth` fallback is real.** During testing, this fallback was once
triggered on the dev laptop (192.168.1.30 "G434"), briefly restarting its local Bluetooth. Keep
any recovery testing stubbed out on the dev machine.

**Server vs. dev machine.** The production server is 192.168.1.62. This dev machine is
192.168.1.30 ("G434"). They are separate hosts. Changes must be deployed via `git pull` on the
server — pushing to the repo on the dev machine does not automatically update the server.

**Commit 4b29ada vs. the 4 unstaged files.** The Phase 3 code changes (dashboard.py,
dashboard.js) are in 4b29ada. The 4 currently unstaged files are docs-only follow-up from the
doc-auditor pass. They are safe to commit at any time without touching code.


---

## Update 2026-09-12

The ECO-WORTHY failed again after the August fix. See
[ADR 0003](../docs/adr/0003-ecoworthy-link-exposure.md) for the diagnosis; the short version
is that the trigger is the host's Bluetooth stack (a wedging Realtek combo adapter plus a
known BlueZ 5.85 crash), not the battery's poll pattern, and holding the link open made the
exposure worse rather than better.

**Two traps for anyone reading this journal.**

*Timestamps before 2026-09-12 are unreliable.* `print()` was block-buffered, so lines reached
the journal in bursts when the buffer filled or the process died. 2,678 adapter recoveries
arrived as five bursts sharing one timestamp each. An earlier analysis of the same journal
concluded recovery had barely fired, because a `tail -40` in the query silently truncated it.
Prefer `tools/bt_health.py` over ad-hoc greps; it counts whole windows.

*Nothing is deployed.* (Superseded — see the 2026-10-27 update.) The server still runs
`683aa39` with the ECO-WORTHY out of its config, and its `config.json` has uncommitted local
edits. Deploy order and verification are in the plan file and in the README's "Keeping the
Bluetooth stack healthy" section.

---

## Update 2026-10-27

**ADR 0003's code is deployed** to 192.168.1.62 (`40cdf36`), and `aiobmsble` there is upgraded
0.23 → 0.31 to meet the `>=0.28.0` floor. Tests pass on the server. Before this, the server
was running `3f8f6d1` (not `683aa39`) — the held-connection code from ADR 0002 that failed.
Check `systemctl show bms-dashboard -p ActiveEnterTimestamp`: if it is earlier than
2026-10-27 14:44 UTC, the service has not been restarted since and still runs the old code
in memory.

**The ECO-WORTHY fix is still unproven.** The battery has been out of the server's config
since August, so the new code has never polled it. It is healthy and advertising, revived on
the vendor app alone. Kris is looking for a second (non-Realtek) Bluetooth adapter. The plan
is to put the ECO-WORTHY on it with its own `adapter` setting and `persistent: true`, and
run `tools/btmon-ring.sh` so a failure leaves a capture. ADR 0003 sets the bar at 60 clean
days on a dedicated adapter.

BlueZ on the server is `5.85-4ubuntu0.4`, which contains the GATT teardown crash fix
(`0.2`, LP #2164630).

**Server facts that earlier sections get wrong:**

- The checkout is `~/applications/linux-lifepo4-bms-monitor`, not `~/linux-lifepo4-bms-monitor`.
- `sudo` on the server needs a password, so a session can deploy but Kris restarts the service.
- The checkout carries local, uncommitted edits to `config.json` and to tracked `venv/` files.
  `config.json` changes upstream, so a plain `git pull` conflicts. What worked: copy
  `config.json` aside, `git checkout -- config.json`, `git pull --ff-only`, copy it back.
  Dated backups sit next to it (`config.json.2026.10.27` is the latest).
- SSH as `kb` with keys from the dev machine works (`ssh 192.168.1.62`). The login prints a
  long ASCII banner, so strip it before parsing output.

**Batteries in the server's config:** `100ah_Smart` (added 2026-10-27, `A4:C1:37:42:9E:41`),
`200ah_01`, `200ah_02`, `330ah`. All JBD. The new 100Ah was invisible until it was moved
next to the server. Even there its signal is only about -67 dBm, so it will probably drop out
if it moves back. Its BMS reports 84 Ah full against a 100 Ah rating.
