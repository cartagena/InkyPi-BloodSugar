import unittest
from datetime import UTC, datetime, timedelta

# Imported under the plugin's real dotted path so this file runs unchanged
# both standalone (tests/conftest.py stubs the host BasePlugin) and inside a
# real InkyPi checkout. See tests/conftest.py.
from plugins.blood_sugar.blood_sugar import STALE_AFTER_MINUTES, BloodSugar


class _FakeDeviceConfig:
    def __init__(self, timezone=None):
        self._timezone = timezone

    def get_config(self, key, default=None):
        return self._timezone if key == "timezone" else default


class FormatDeltaTests(unittest.TestCase):
    def test_none_delta(self):
        self.assertEqual(BloodSugar._format_delta(None, "mg_dl"), "—")

    def test_positive_mg_dl(self):
        self.assertEqual(BloodSugar._format_delta(5, "mg_dl"), "+5")

    def test_negative_mg_dl(self):
        self.assertEqual(BloodSugar._format_delta(-3, "mg_dl"), "-3")

    def test_zero_mg_dl_gets_plus_sign(self):
        self.assertEqual(BloodSugar._format_delta(0, "mg_dl"), "+0")

    def test_positive_mmol(self):
        # 5 mg/dL -> 0.2777..., rounded (not floored) to 0.3
        self.assertEqual(BloodSugar._format_delta(5, "mmol_l"), "+0.3")

    def test_mmol_delta_does_not_use_floor_bias(self):
        # 1 mg/dL -> 0.0555..., round() gives 0.1; the floor-based mg_dl_to_mmol_l
        # helper used for absolute values would give 0.0 here -- this pins the
        # deliberate divergence documented in CLAUDE.md.
        self.assertEqual(BloodSugar._format_delta(1, "mmol_l"), "+0.1")


class ComputeValueColorTests(unittest.TestCase):
    def test_default_low(self):
        self.assertEqual(BloodSugar._compute_value_color(65, {}), "#D32F2F")

    def test_default_high(self):
        self.assertEqual(BloodSugar._compute_value_color(200, {}), "#FFC107")

    def test_default_in_range(self):
        self.assertIsNone(BloodSugar._compute_value_color(120, {}))

    def test_low_boundary_is_inclusive(self):
        self.assertEqual(BloodSugar._compute_value_color(70, {}), "#D32F2F")

    def test_high_boundary_is_exclusive(self):
        self.assertIsNone(BloodSugar._compute_value_color(180, {}))
        self.assertEqual(BloodSugar._compute_value_color(181, {}), "#FFC107")

    def test_custom_thresholds_and_colors(self):
        settings = {
            "lowThreshold": "80",
            "lowColor": "#123456",
            "highThreshold": "200",
            "highColor": "#abcdef",
        }
        self.assertEqual(BloodSugar._compute_value_color(50, settings), "#123456")
        self.assertEqual(BloodSugar._compute_value_color(250, settings), "#abcdef")
        self.assertIsNone(BloodSugar._compute_value_color(100, settings))

    def test_low_takes_priority_when_thresholds_overlap(self):
        settings = {"lowThreshold": "150", "highThreshold": "100"}
        self.assertEqual(BloodSugar._compute_value_color(120, settings), "#D32F2F")

    def test_non_numeric_threshold_falls_back_to_default(self):
        # Layout regions let settings be hand-edited as raw JSON, where the
        # settings form's number input isn't there to constrain the value.
        settings = {"lowThreshold": "not a number", "highThreshold": None}
        self.assertEqual(BloodSugar._compute_value_color(65, settings), "#D32F2F")
        self.assertEqual(BloodSugar._compute_value_color(200, settings), "#FFC107")
        self.assertIsNone(BloodSugar._compute_value_color(120, settings))


class ClientCacheTests(unittest.TestCase):
    def setUp(self):
        self.plugin = BloodSugar({"id": "blood_sugar", "class": "BloodSugar"})

    def test_same_credentials_reuse_one_client(self):
        first = self.plugin._get_client("share1.dexcom.com", "user", "pw")
        second = self.plugin._get_client("share1.dexcom.com", "user", "pw")
        self.assertIs(first, second)

    def test_changed_password_gets_a_fresh_client(self):
        # InkyPi keeps one plugin instance for the process lifetime, so a
        # client cached under the old password would keep failing to log in
        # until the service was restarted.
        first = self.plugin._get_client("share1.dexcom.com", "user", "old-pw")
        second = self.plugin._get_client("share1.dexcom.com", "user", "new-pw")
        self.assertIsNot(first, second)

    def test_different_servers_get_separate_clients(self):
        us = self.plugin._get_client("share1.dexcom.com", "user", "pw")
        ous = self.plugin._get_client("shareous1.dexcom.com", "user", "pw")
        self.assertIsNot(us, ous)


class ReadingAgeTests(unittest.TestCase):
    def test_none_timestamp_has_no_age(self):
        self.assertIsNone(BloodSugar._reading_age(None))

    def test_age_is_measured_from_now_in_utc(self):
        stamp = datetime.now(UTC) - timedelta(minutes=7)
        age = BloodSugar._reading_age(stamp)
        self.assertGreaterEqual(age, timedelta(minutes=7))
        self.assertLess(age, timedelta(minutes=8))


class FormatAgeTests(unittest.TestCase):
    def test_missing_timestamp(self):
        self.assertEqual(BloodSugar._format_age(None), "no timestamp")

    def test_sub_minute(self):
        self.assertEqual(BloodSugar._format_age(timedelta(seconds=20)), "just now")

    def test_minutes(self):
        self.assertEqual(BloodSugar._format_age(timedelta(minutes=27)), "27 min ago")

    def test_hours(self):
        self.assertEqual(BloodSugar._format_age(timedelta(minutes=125)), "2 hr ago")

    def test_days(self):
        self.assertEqual(BloodSugar._format_age(timedelta(hours=50)), "2d ago")

    def test_stale_threshold_is_three_missed_readings(self):
        # A CGM publishes every ~5 minutes; the threshold is meaningful only
        # relative to that cadence, so pin it rather than leaving it floating.
        self.assertEqual(STALE_AFTER_MINUTES, 15)


class FormatReadingTimeTests(unittest.TestCase):
    # 2024-01-15 18:30 UTC == 12:30 PM in America/Chicago (CST).
    STAMP = datetime(2024, 1, 15, 18, 30, tzinfo=UTC)

    def test_renders_in_the_configured_device_timezone(self):
        formatted = BloodSugar._format_reading_time(
            self.STAMP, _FakeDeviceConfig("America/Chicago")
        )
        self.assertEqual(formatted, "12:30 PM")

    def test_falls_back_to_utc_when_no_timezone_configured(self):
        formatted = BloodSugar._format_reading_time(self.STAMP, _FakeDeviceConfig(None))
        self.assertEqual(formatted, "6:30 PM")

    def test_unknown_timezone_falls_back_to_utc_instead_of_raising(self):
        formatted = BloodSugar._format_reading_time(
            self.STAMP, _FakeDeviceConfig("Mars/Olympus_Mons")
        )
        self.assertEqual(formatted, "6:30 PM")

    def test_missing_timestamp(self):
        self.assertEqual(
            BloodSugar._format_reading_time(None, _FakeDeviceConfig("UTC")), "Unknown"
        )


if __name__ == "__main__":
    unittest.main()
