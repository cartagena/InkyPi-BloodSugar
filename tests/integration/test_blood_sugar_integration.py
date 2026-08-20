"""Integration tests: BloodSugar against a real InkyPi host.

What earns a place here is anything that can only be verified against real
host code — the plugin loading through InkyPi's own registry, and the real
Jinja + headless-Chromium render pipeline (including the seven trend-arrow
SVGs, which are pulled in by ``{% include %}`` and so only fail at render
time). Everything expressible with fakes belongs in ``tests/unit/``, which
runs everywhere and runs fast.

**The Dexcom API is always mocked here, in every test, without exception.**
A CI run must never touch a real CGM account, and this plugin handles health
data — see tests/conftest.py and the repo README.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image


@pytest.fixture()
def plugin() -> Any:
    """Load BloodSugar the way InkyPi itself does, through the real registry."""
    from plugins.plugin_registry import get_plugin_instance, load_plugins

    plugin_config = {"id": "blood_sugar", "class": "BloodSugar"}
    load_plugins([plugin_config])
    return get_plugin_instance(plugin_config)


@pytest.fixture()
def credentialed_config(device_config: Any) -> Any:
    """device_config with fake Dexcom credentials, so generate_image gets past its guard."""
    with patch.object(
        type(device_config),
        "load_env_key",
        lambda self, key: {"DEXCOM_USERNAME": "u", "DEXCOM_PASSWORD": "p"}.get(key),
    ):
        yield device_config


def _reading(mg_dl: int, trend: int, minutes_ago: float) -> Any:
    from plugins.blood_sugar.dexcom_client import Reading

    return Reading(
        mg_dl=mg_dl,
        trend=trend,
        timestamp=datetime.now(UTC) - timedelta(minutes=minutes_ago),
    )


def test_plugin_loads_through_the_real_registry(plugin: Any) -> None:
    from plugins.base_plugin.base_plugin import BasePlugin

    assert isinstance(plugin, BasePlugin)
    assert plugin.get_plugin_id() == "blood_sugar"


def test_plugin_info_json_matches_the_registered_class(plugin: Any) -> None:
    """The installed folder name, id, and class must agree.

    `inkypi plugin install` sparse-checkouts the folder named after the plugin
    id, so a mismatch here breaks installation for every end user while
    everything still works locally.
    """
    from utils.app_utils import resolve_path

    with open(resolve_path("plugins/blood_sugar/plugin-info.json"), encoding="utf-8") as f:
        info = json.load(f)

    assert info["id"] == "blood_sugar"
    assert info["class"] == type(plugin).__name__


@pytest.mark.parametrize(
    "trend",
    # Every trend that maps to an icon file, plus one that maps to None.
    [1, 2, 3, 4, 5, 6, 7, 8],
)
def test_every_trend_icon_renders(
    plugin: Any, credentialed_config: Any, trend: int
) -> None:
    """Each trend arrow is a separate SVG pulled in by `{% include %}`.

    A missing or malformed icon file only fails when Jinja actually renders
    it, so a stubbed render_image would never catch it — this is exactly the
    kind of thing the integration suite exists for.
    """
    readings = [_reading(120, trend, 2), _reading(117, trend, 7)]

    with patch(
        "plugins.blood_sugar.dexcom_client.DexcomClient.fetch_latest",
        return_value=readings,
    ):
        image = plugin.generate_image({}, credentialed_config)

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    assert len(image.getcolors(maxcolors=1 << 20)) > 1, "rendered a blank canvas"


def test_api_failure_surfaces_as_a_user_facing_runtime_error(
    plugin: Any, credentialed_config: Any
) -> None:
    """InkyPi shows RuntimeError messages in the web UI; other exceptions leak a traceback."""
    from plugins.blood_sugar.dexcom_client import DexcomApiError

    with (
        patch(
            "plugins.blood_sugar.dexcom_client.DexcomClient.fetch_latest",
            side_effect=DexcomApiError("upstream is down"),
        ),
        pytest.raises(RuntimeError, match="check logs and credentials"),
    ):
        plugin.generate_image({}, credentialed_config)


def test_missing_credentials_surface_as_a_user_facing_runtime_error(
    plugin: Any, device_config: Any
) -> None:
    with pytest.raises(RuntimeError, match="DEXCOM_USERNAME"):
        plugin.generate_image({}, device_config)
