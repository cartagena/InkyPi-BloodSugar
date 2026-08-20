import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL.Image import Image as ImageType

from plugins.base_plugin.base_plugin import BasePlugin

from .dexcom_client import SERVERS, DexcomApiError, DexcomClient, mg_dl_to_mmol_l

# The declarative settings-schema DSL only exists on some InkyPi forks;
# upstream InkyPi has no `plugins.base_plugin.settings_schema` module at all.
# Falling back to `schema = None` here (rather than letting the import raise)
# keeps this file importable on upstream, where settings.html remains the
# only settings UI — see build_settings_schema() below.
try:
    from plugins.base_plugin.settings_schema import field, option, row, schema, section
except ImportError:  # pragma: no cover - exercised only on upstream InkyPi
    schema = None

logger = logging.getLogger(__name__)

# A CGM publishes roughly every 5 minutes. Past this age the reading on screen
# is no longer "what your glucose is" — the sensor, the phone relaying it, or
# the Share upload has stopped, and a large number with a trend arrow beside it
# reads as current when it isn't. Three missed readings is the threshold for
# marking it stale: the reading stays on screen (it's still the last known
# value) but is visually demoted, loses its low/high alarm color, and gains an
# explicit age label.
STALE_AFTER_MINUTES = 15

# Maps a trend name to its pre-authored icon file under render/icons/. Each file draws
# the arrow already pointing the right way (rather than rotating one shared shape at
# render time), so every icon has identical, angle-independent padding within its own
# box and the gap to the glucose value stays constant across trend states.
TREND_ICON_FILE: dict[str, str | None] = {
    "DOUBLE_UP": "trend_double_up.svg",
    "SINGLE_UP": "trend_up.svg",
    "FORTYFIVE_UP": "trend_up_right.svg",
    "FLAT": "trend_right.svg",
    "FORTYFIVE_DOWN": "trend_down_right.svg",
    "SINGLE_DOWN": "trend_down.svg",
    "DOUBLE_DOWN": "trend_double_down.svg",
    "NOT_COMPUTABLE": None,
    "RATE_OUT_OF_RANGE": None,
    "NONE": None,
}


class BloodSugar(BasePlugin):
    def __init__(self, config: Mapping[str, object], **dependencies: object) -> None:
        super().__init__(config, **dependencies)
        self._clients: dict[tuple[str, str, str], DexcomClient] = {}

    def build_settings_schema(self) -> dict[str, object] | None:
        # settings.html stays as the fallback for forks/upstream without the
        # schema DSL (see the import guard above); this is the fork-native
        # equivalent of that same form.
        if schema is None:
            return None
        return schema(
            section(
                "Dexcom",
                row(
                    field(
                        "dexcomServer",
                        "select",
                        label="Dexcom Server Region",
                        default="us",
                        options=[
                            option("us", "United States"),
                            option("ous", "Outside United States"),
                        ],
                    ),
                    field(
                        "units",
                        "select",
                        label="Units",
                        default="mg_dl",
                        options=[
                            option("mg_dl", "mg/dL"),
                            option("mmol_l", "mmol/L"),
                        ],
                    ),
                ),
            ),
            section(
                "Thresholds",
                row(
                    field(
                        "lowThreshold",
                        "number",
                        label="Low Threshold (mg/dL)",
                        default="70",
                    ),
                    field(
                        "lowColor",
                        "color",
                        label="Low Color",
                        default="#D32F2F",
                    ),
                ),
                row(
                    field(
                        "highThreshold",
                        "number",
                        label="High Threshold (mg/dL)",
                        default="180",
                    ),
                    field(
                        "highColor",
                        "color",
                        label="High Color",
                        default="#FFC107",
                    ),
                ),
            ),
        )

    def generate_settings_template(self) -> dict[str, object]:
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "Dexcom Share",
            "expected_key": "DEXCOM_USERNAME and DEXCOM_PASSWORD",
            # "services" is only understood by some InkyPi forks that resolve
            # per-key presence for the "Requires API Key" badge; upstream InkyPi
            # ignores it and keeps using "service"/"expected_key" above as-is.
            # It's an OR check, not AND (both are actually required), since
            # that's the only per-key presence primitive those forks provide.
            "services": [
                {"name": "Username", "env_var": "DEXCOM_USERNAME"},
                {"name": "Password", "env_var": "DEXCOM_PASSWORD"},
            ],
        }
        template_params['style_settings'] = True
        return template_params

    def generate_image(
        self, settings: Mapping[str, object], device_config: Any
    ) -> ImageType:
        username = device_config.load_env_key("DEXCOM_USERNAME")
        password = device_config.load_env_key("DEXCOM_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "Dexcom username and password are required. Set DEXCOM_USERNAME and "
                "DEXCOM_PASSWORD on the API Keys page."
            )

        server = SERVERS.get(self._as_str(settings.get("dexcomServer"), "us"), SERVERS["us"])
        units = self._as_str(settings.get("units"), "mg_dl")

        client = self._get_client(server, username, password)
        try:
            readings = client.fetch_latest(max_count=2)
        except DexcomApiError as e:
            logger.error(f"Dexcom API request failed: {e}")
            raise RuntimeError(
                "Failed to fetch Dexcom data, please check logs and credentials."
            ) from e

        if not readings:
            raise RuntimeError("No glucose readings returned from Dexcom.")

        latest = readings[0]
        previous = readings[1] if len(readings) > 1 else None
        delta_mg = (latest.mg_dl - previous.mg_dl) if previous else None

        age = self._reading_age(latest.timestamp)
        is_stale = age is None or age > timedelta(minutes=STALE_AFTER_MINUTES)

        template_params = {
            "value": latest.mg_dl if units == "mg_dl" else f"{mg_dl_to_mmol_l(latest.mg_dl):.1f}",
            "units_label": "mg/dL" if units == "mg_dl" else "mmol/L",
            "trend_icon_file": TREND_ICON_FILE.get(latest.trend_name),
            "delta": self._format_delta(delta_mg, units),
            # A stale reading keeps its number but loses the low/high alarm
            # color: a red "55" from an hour ago reads as a live emergency.
            "value_color": None if is_stale else self._compute_value_color(latest.mg_dl, settings),
            "last_reading_time": self._format_reading_time(latest.timestamp, device_config),
            "is_stale": is_stale,
            "age_label": self._format_age(age),
            "plugin_settings": settings,
        }

        # Deliberately not BasePlugin.get_oriented_dimensions(): that helper is
        # fork-only, and this file is kept importable on upstream InkyPi (see
        # the settings_schema import guard above).
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        return self.render_image(dimensions, "blood_sugar.html", "blood_sugar.css", template_params)

    def _get_client(self, server: str, username: str, password: str) -> DexcomClient:
        # Keyed on the password too, not just (server, username): InkyPi keeps
        # one plugin instance alive for the life of the process, so a client
        # cached under an old password would keep failing to log in until the
        # service was restarted after a credential change.
        key = (server, username, password)
        if key not in self._clients:
            self._clients[key] = DexcomClient(server, username, password)
        return self._clients[key]

    @staticmethod
    def _reading_age(timestamp: datetime | None) -> timedelta | None:
        """Age of a reading, or None if it has no usable timestamp."""
        if timestamp is None:
            return None
        return datetime.now(UTC) - timestamp

    @staticmethod
    def _format_age(age: timedelta | None) -> str:
        if age is None:
            return "no timestamp"
        minutes = int(age.total_seconds() // 60)
        if minutes < 1:
            return "just now"
        if minutes < 60:
            return f"{minutes} min ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours} hr ago"
        return f"{hours // 24}d ago"

    @staticmethod
    def _format_reading_time(timestamp: datetime | None, device_config: Any) -> str:
        """Render a UTC reading timestamp in the display's configured timezone.

        `Reading.timestamp` is always UTC. Formatting it with the Pi's own
        local time would shift every reading whenever the system clock's zone
        and the device's configured timezone disagree — which is the normal
        case on a headless Pi left at UTC.
        """
        if timestamp is None:
            return "Unknown"
        tz_name = device_config.get_config("timezone")
        tz: tzinfo = UTC
        if isinstance(tz_name, str) and tz_name:
            try:
                tz = ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError):
                logger.warning("Unknown timezone %r; showing reading time in UTC.", tz_name)
        return timestamp.astimezone(tz).strftime("%I:%M %p").lstrip("0")

    @staticmethod
    def _format_delta(delta_mg: int | None, units: object) -> str:
        if delta_mg is None:
            return "—"
        sign = "+" if delta_mg >= 0 else ""
        if units == "mg_dl":
            return f"{sign}{delta_mg}"
        return f"{sign}{round(delta_mg / 18.0, 1)}"

    @staticmethod
    def _compute_value_color(mg_dl: int, settings: Mapping[str, object]) -> str | None:
        # Thresholds are always mg/dL regardless of the display unit, and are
        # compared against the raw reading with no conversion. Falls back to
        # the defaults rather than raising if a threshold isn't a number —
        # region settings in the Layout plugin can be hand-edited as JSON,
        # where the settings form's number input isn't there to constrain it.
        low_threshold = BloodSugar._as_float(settings.get("lowThreshold"), 70)
        high_threshold = BloodSugar._as_float(settings.get("highThreshold"), 180)
        if mg_dl <= low_threshold:
            return BloodSugar._as_str(settings.get("lowColor"), "#D32F2F")
        if mg_dl > high_threshold:
            return BloodSugar._as_str(settings.get("highColor"), "#FFC107")
        return None

    @staticmethod
    def _as_str(value: object, fallback: str) -> str:
        return value if isinstance(value, str) and value else fallback

    @staticmethod
    def _as_float(value: object, fallback: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return fallback
        try:
            return float(value)
        except ValueError:
            return fallback
