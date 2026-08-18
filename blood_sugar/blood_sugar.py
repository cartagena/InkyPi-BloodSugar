import logging
from datetime import datetime

from plugins.base_plugin.base_plugin import BasePlugin

from .dexcom_client import DexcomApiError, DexcomClient, SERVERS, mg_dl_to_mmol_l

logger = logging.getLogger(__name__)

# Maps a trend name to its pre-authored icon file under render/icons/. Each file draws
# the arrow already pointing the right way (rather than rotating one shared shape at
# render time), so every icon has identical, angle-independent padding within its own
# box and the gap to the glucose value stays constant across trend states.
TREND_ICON_FILE = {
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
    def __init__(self, config, **dependencies):
        super().__init__(config, **dependencies)
        self._clients = {}

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "Dexcom Share",
            "expected_key": "DEXCOM_USERNAME and DEXCOM_PASSWORD",
        }
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        username = device_config.load_env_key("DEXCOM_USERNAME")
        password = device_config.load_env_key("DEXCOM_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "Dexcom username and password are required. Set DEXCOM_USERNAME and "
                "DEXCOM_PASSWORD on the API Keys page."
            )

        server = SERVERS.get(settings.get("dexcomServer", "us"), SERVERS["us"])
        units = settings.get("units", "mg_dl")

        client = self._get_client(server, username, password)
        try:
            readings = client.fetch_latest(max_count=2)
        except DexcomApiError as e:
            logger.error(f"Dexcom API request failed: {e}")
            raise RuntimeError("Failed to fetch Dexcom data, please check logs and credentials.")

        if not readings:
            raise RuntimeError("No glucose readings returned from Dexcom.")

        latest = readings[0]
        previous = readings[1] if len(readings) > 1 else None
        delta_mg = (latest.mg_dl - previous.mg_dl) if previous else None

        template_params = {
            "value": latest.mg_dl if units == "mg_dl" else f"{mg_dl_to_mmol_l(latest.mg_dl):.1f}",
            "units_label": "mg/dL" if units == "mg_dl" else "mmol/L",
            "trend_icon_file": TREND_ICON_FILE.get(latest.trend_name),
            "delta": self._format_delta(delta_mg, units),
            "value_color": self._compute_value_color(latest.mg_dl, settings),
            "last_reading_time": latest.timestamp.strftime("%I:%M %p").lstrip("0") if latest.timestamp else "Unknown",
            "plugin_settings": settings,
        }

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        return self.render_image(dimensions, "blood_sugar.html", "blood_sugar.css", template_params)

    def _get_client(self, server, username, password):
        key = (server, username)
        if key not in self._clients:
            self._clients[key] = DexcomClient(server, username, password)
        return self._clients[key]

    @staticmethod
    def _format_delta(delta_mg, units):
        if delta_mg is None:
            return "—"
        sign = "+" if delta_mg >= 0 else ""
        if units == "mg_dl":
            return f"{sign}{delta_mg}"
        return f"{sign}{round(delta_mg / 18.0, 1)}"

    @staticmethod
    def _compute_value_color(mg_dl, settings):
        low_threshold = float(settings.get("lowThreshold") or 70)
        high_threshold = float(settings.get("highThreshold") or 180)
        if mg_dl <= low_threshold:
            return settings.get("lowColor") or "#D32F2F"
        if mg_dl > high_threshold:
            return settings.get("highColor") or "#FFC107"
        return None
