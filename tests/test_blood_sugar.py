import os
import sys
import types
import unittest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, REPO_ROOT)

# blood_sugar.py does `from plugins.base_plugin.base_plugin import BasePlugin`, which
# only exists inside a real InkyPi checkout. Stub it out so these tests can run
# standalone against just this repo, per CLAUDE.md's "no InkyPi needed" philosophy
# for anything that doesn't actually need Jinja/Chromium rendering.
if "plugins.base_plugin.base_plugin" not in sys.modules:
    plugins_pkg = types.ModuleType("plugins")
    base_plugin_pkg = types.ModuleType("plugins.base_plugin")
    base_plugin_module = types.ModuleType("plugins.base_plugin.base_plugin")

    class _FakeBasePlugin:
        def __init__(self, config, **dependencies):
            self.config = config

    base_plugin_module.BasePlugin = _FakeBasePlugin
    plugins_pkg.base_plugin = base_plugin_pkg
    base_plugin_pkg.base_plugin = base_plugin_module

    sys.modules["plugins"] = plugins_pkg
    sys.modules["plugins.base_plugin"] = base_plugin_pkg
    sys.modules["plugins.base_plugin.base_plugin"] = base_plugin_module

from blood_sugar.blood_sugar import BloodSugar  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
