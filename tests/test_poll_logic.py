#!/usr/bin/env python3
"""
Unit tests for the parts of the poll loop that can be reasoned about without a radio.

These cover the specific rules that were got wrong in production and that no amount of
hardware testing would have caught quickly:

* failure backoff, so one unreachable battery stops generating a retry storm
* the aged recovery quorum, so a permanently dead battery cannot keep the adapter
  power-cycling forever (this is what happened on 2026-08-21)
* the adapter health gate
* host-stack error classification, checked against the exact exception strings from
  this system's journal on 2026-08-20 and 2026-09-09
* the null-vs-zero contract in the reading mapper

Run with:  PYTHONPATH=venv/lib/python3.12/site-packages python3.12 -m unittest discover -s tests
(or plain `python3 -m unittest discover -s tests` where the deps are importable)
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bms_config  # noqa: E402


class BackoffTests(unittest.TestCase):
    """A failing battery must be retried less and less often, up to a ceiling."""

    def setUp(self):
        import dashboard  # noqa: PLC0415 - imported lazily; needs BLE deps present

        self.state = dashboard._PollState(["a"])

    def test_success_keeps_the_base_interval(self):
        factor = self.state.schedule_next("a", 30, failed=False, backoff_cap_minutes=10)
        self.assertEqual(factor, 1.0)
        self.assertAlmostEqual(self.state.due["a"] - time.time(), 30, delta=1)

    def test_failures_back_off_and_then_stop_growing(self):
        seen = [
            self.state.schedule_next("a", 30, failed=True, backoff_cap_minutes=10)
            for _ in range(10)
        ]
        self.assertEqual(seen[0], 2.0, "first failure should double the interval")
        self.assertTrue(
            all(b >= a for a, b in zip(seen, seen[1:])), "backoff must be monotonic"
        )
        # 10 minutes / 30 s interval = a factor of 20, and never more.
        self.assertLessEqual(max(seen), 20.0)
        self.assertEqual(seen[-1], 20.0, "backoff should reach, and hold at, the cap")

    def test_success_after_failures_resets_immediately(self):
        for _ in range(5):
            self.state.schedule_next("a", 30, failed=True, backoff_cap_minutes=10)
        factor = self.state.schedule_next("a", 30, failed=False, backoff_cap_minutes=10)
        self.assertEqual(factor, 1.0)
        self.assertEqual(self.state.failures["a"], 0)


class RecoveryQuorumTests(unittest.TestCase):
    """Adapter recovery must need a quorum of *recently alive* batteries.

    The regression this guards against is concrete: on 2026-08-21 a permanently dead
    ECO-WORTHY plus one flaky JBD satisfied the old two-battery quorum forever, so the
    adapter was power-cycled every five minutes for a day.
    """

    def setUp(self):
        import dashboard  # noqa: PLC0415

        self.dashboard = dashboard
        self.entries = {
            "a": self._entry("AA:AA:AA:AA:AA:AA"),
            "b": self._entry("BB:BB:BB:BB:BB:BB"),
            "dead": self._entry("CC:CC:CC:CC:CC:CC"),
        }
        self.polling = dict(bms_config._POLLING_DEFAULTS)
        self.state = dashboard._PollState(self.entries)
        self.health = dashboard.AdapterHealth(None)
        self.fired = []

    @staticmethod
    def _entry(address):
        return {
            "address": address, "protocol": "jbd", "label": address,
            "persistent": False, "rated_capacity_ah": None,
            "interval_seconds": None, "attempts": None,
            "adapter": None, "health_gated": False,
        }

    def _run(self):
        """Call _maybe_recover with ENTRIES/POLLING patched and recovery stubbed out.

        Never let a test reach the real recovery path: it power-cycles the machine's
        Bluetooth adapter, which on a dev box means taking down whatever else is using it.
        """
        d = self.dashboard
        saved = (d.ENTRIES, d.POLLING, d._recover_adapter, d.telemetry.enabled)
        d.ENTRIES, d.POLLING = self.entries, self.polling
        d._recover_adapter = lambda loop, adapter, addresses: (
            self.fired.append(adapter) or True
        )
        d.telemetry.enabled = False  # keep the tests from writing to state/
        try:
            d._maybe_recover(None, self.state, self.health, None)
        finally:
            (d.ENTRIES, d.POLLING, d._recover_adapter, d.telemetry.enabled) = saved

    def test_one_missing_battery_never_triggers_recovery(self):
        now = time.time()
        for name in self.entries:
            self.state.last_seen[name] = now
        self.state.misses["a"] = 10
        self._run()
        self.assertEqual(self.fired, [], "a single battery is that battery's problem")

    def test_all_recently_seen_missing_triggers_recovery(self):
        now = time.time()
        for name in self.entries:
            self.state.last_seen[name] = now
            self.state.misses[name] = 3
        self._run()
        self.assertEqual(len(self.fired), 1)

    def test_long_dead_battery_ages_out_of_the_quorum(self):
        now = time.time()
        stale = self.polling["recover_stale_minutes"] * 60 + 60
        # 'dead' has been gone for over an hour; 'a' is flaky; 'b' is fine.
        self.state.last_seen["dead"] = now - stale
        self.state.misses["dead"] = 5000
        self.state.last_seen["a"] = now
        self.state.misses["a"] = 5
        self.state.last_seen["b"] = now
        self.state.misses["b"] = 0
        self._run()
        self.assertEqual(
            self.fired, [],
            "a dead battery must not keep the adapter power-cycling forever",
        )

    def test_single_battery_adapter_never_recovers(self):
        self.entries = {"only": self._entry("DD:DD:DD:DD:DD:DD")}
        self.state = self.dashboard._PollState(self.entries)
        self.state.last_seen["only"] = time.time()
        self.state.misses["only"] = 99
        self._run()
        self.assertEqual(self.fired, [], "a dedicated adapter has no quorum to reach")

    def test_cooldown_blocks_a_second_attempt(self):
        now = time.time()
        for name in self.entries:
            self.state.last_seen[name] = now
            self.state.misses[name] = 3
        self._run()
        for name in self.entries:
            self.state.misses[name] = 3
        self._run()
        self.assertEqual(len(self.fired), 1, "cooldown must suppress the second attempt")


class HealthGateTests(unittest.TestCase):
    def setUp(self):
        import dashboard  # noqa: PLC0415

        self.health = dashboard.AdapterHealth(None)

    def test_a_fresh_adapter_is_quiet(self):
        self.assertIsNone(self.health.seconds_since_fault())
        self.assertTrue(self.health.is_quiet(15))

    def test_a_recent_fault_closes_the_gate(self):
        self.health.note("outage")
        self.assertFalse(self.health.is_quiet(15))
        self.assertLess(self.health.seconds_since_fault(), 5)

    def test_an_old_fault_reopens_the_gate(self):
        self.health.faults["outage"] = time.time() - 16 * 60
        self.assertTrue(self.health.is_quiet(15))


class StackErrorTests(unittest.TestCase):
    """Verbatim strings from this system's journal must be recognised.

    If these stop matching, a bluetoothd restart silently leaves bleak's D-Bus caches
    poisoned and every battery fails identically until the service is restarted by hand.
    """

    JOURNAL_LINES = [
        'BleakDBusError: [org.freedesktop.DBus.Error.NoReply] Message recipient '
        'disconnected from message bus without replying',
        'BleakDBusError: [org.freedesktop.DBus.Error.UnknownObject] Method "StartNotify" '
        'with signature "" on interface "org.bluez.GattCharacteristic1" doesn\'t exist',
        'BleakDBusError: [org.freedesktop.DBus.Error.ServiceUnknown] The name org.bluez '
        'was not provided by any .service files',
    ]

    def test_journal_lines_are_classified_as_stack_restarts(self):
        import bms_driver  # noqa: PLC0415

        for line in self.JOURNAL_LINES:
            with self.subTest(line=line[:60]):
                self.assertIsNotNone(bms_driver.classify_stack_error(Exception(line)))

    def test_ordinary_ble_failures_are_not_stack_restarts(self):
        import bms_driver  # noqa: PLC0415

        for line in [
            "TimeoutError: ",
            "BleakGATTProtocolError: (<BleakGATTProtocolErrorCode.UNLIKELY_ERROR: 14>, "
            "'GATT Protocol Error: Unlikely Error')",
            "BleakNotFoundError: A4:C1:37:55:C2:29 - Failed to connect after 4 attempt(s)",
        ]:
            with self.subTest(line=line[:60]):
                self.assertIsNone(bms_driver.classify_stack_error(Exception(line)))


class ReadingMappingTests(unittest.TestCase):
    """The null-vs-zero contract: absent must never render as a real reading."""

    def test_absent_fields_are_none_not_zero(self):
        import bms_driver  # noqa: PLC0415

        sparse = bms_driver.sample_to_reading(
            "AA:BB", {"battery_level": 55, "cell_voltages": [3.2, 3.3]}
        )
        for key in ("capacity_ah", "capacity_full_ah", "energy_wh", "soh",
                    "runtime_seconds", "cycles", "problem_code",
                    "chrg_mosfet", "dischrg_mosfet", "balancer"):
            with self.subTest(key=key):
                self.assertIsNone(sparse[key])
        self.assertEqual(sparse["temps"], [])
        self.assertIs(sparse["problem"], False, "problem is a real bool, never None")
        self.assertEqual(sparse["soc"], 55, "soc must stay numeric for the SOC ring")

    def test_temp_sensor_objects_are_rounded_like_floats(self):
        import bms_driver  # noqa: PLC0415

        # aiobmsble >= 0.28 returns TempSensor objects, which support float() but
        # not round(). Passing one straight to round() broke every read on 2026-10-27.
        class FakeTempSensor:
            def __init__(self, value):
                self.value = value

            def __float__(self):
                return float(self.value)

        reading = bms_driver.sample_to_reading("AA:BB", {
            "battery_level": 50, "temperature": 23.46,
            "temp_values": [FakeTempSensor(23.46), FakeTempSensor(22.04)],
        })
        self.assertEqual(reading["temps"], [23.5, 22.0])
        self.assertEqual(reading["temperature"], 23.5)

    def test_full_sample_matches_the_hardware_gate_reading(self):
        import bms_driver  # noqa: PLC0415

        # Values captured from the real ECO-WORTHY during the driver gate.
        full = bms_driver.sample_to_reading("E2:E7:79:8A:56:A3", {
            "battery_level": 100, "battery_health": 100, "voltage": 13.86,
            "current": 0.0, "power": 0.0, "cycle_charge": 52.0, "design_capacity": 52,
            "cycle_capacity": 720.72, "temperature": 34.5, "temp_values": [35.5, 33.5],
            "delta_voltage": 0.078, "cell_voltages": [3.514, 3.443, 3.472, 3.436],
            "problem": True, "problem_code": 256,
        })
        self.assertEqual(full["capacity_ah"], 52.0)
        self.assertEqual(full["energy_wh"], 721)
        self.assertIsInstance(full["energy_wh"], int)
        self.assertEqual(full["delta_mv"], 78.0)
        self.assertIsNone(full["cycles"], "ECO-WORTHY reports no cycle count")
        self.assertTrue(full["problem"])
        self.assertEqual(full["problem_code"], 256)

    def test_rated_capacity_prefers_config_then_bms_then_none(self):
        import bms_driver  # noqa: PLC0415

        self.assertEqual(
            bms_driver.rated_capacity({"rated_capacity_ah": 50.0},
                                      {"capacity_full_ah": 52}), 50.0)
        self.assertEqual(
            bms_driver.rated_capacity({"rated_capacity_ah": None},
                                      {"capacity_full_ah": 52}), 52.0)
        self.assertIsNone(bms_driver.rated_capacity({}, {}))
        self.assertIsNone(
            bms_driver.rated_capacity({}, {"capacity_full_ah": 0}),
            "a reported zero means unknown, not a zero-capacity battery",
        )


class ConfigTests(unittest.TestCase):
    def test_per_battery_overrides_fall_back_to_polling_defaults(self):
        polling = dict(bms_config._POLLING_DEFAULTS)
        plain = {"interval_seconds": None, "attempts": None}
        self.assertEqual(bms_config.battery_interval(plain, polling),
                         polling["interval_seconds"])
        self.assertEqual(bms_config.battery_attempts(plain, polling),
                         polling["attempts"])
        override = {"interval_seconds": 600, "attempts": 1}
        self.assertEqual(bms_config.battery_interval(override, polling), 600)
        self.assertEqual(bms_config.battery_attempts(override, polling), 1)

    def test_shipped_config_keeps_the_ecoworthy_off_the_shared_adapter_hot_path(self):
        """The shipped config must not re-enable the setting that preceded a failure."""
        cfg = bms_config.load_config()
        entries = bms_config.battery_entries(cfg)
        eco = entries.get("ecoworthy")
        if eco is None:
            self.skipTest("no ecoworthy battery configured")
        polling = bms_config.polling_config(cfg)
        self.assertFalse(eco["persistent"], "see docs/adr/0003 before changing this")
        self.assertTrue(eco["health_gated"])
        self.assertGreaterEqual(bms_config.battery_interval(eco, polling), 300)

    def test_adapters_group_batteries(self):
        entries = {
            "a": {"adapter": None}, "b": {"adapter": "hci1"}, "c": {"adapter": None},
        }
        self.assertEqual(bms_config.adapters(entries), [None, "hci1"])
        self.assertEqual(list(bms_config.batteries_on(entries, None)), ["a", "c"])
        self.assertEqual(list(bms_config.batteries_on(entries, "hci1")), ["b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
