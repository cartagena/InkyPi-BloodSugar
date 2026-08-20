import math
import re
from dataclasses import dataclass
from datetime import datetime

import requests

APPLICATION_ID = "d89443d2-327c-4a6f-89e5-496bbb0317db"
AGENT = "Dexcom Share/3.0.2.11 CFNetwork/711.2.23 Darwin/14.0.0"

SERVERS = {
    "us": "share1.dexcom.com",
    "ous": "shareous1.dexcom.com",
}

TREND_NAMES = {
    0: "NONE",
    1: "DOUBLE_UP",
    2: "SINGLE_UP",
    3: "FORTYFIVE_UP",
    4: "FLAT",
    5: "FORTYFIVE_DOWN",
    6: "SINGLE_DOWN",
    7: "DOUBLE_DOWN",
    8: "NOT_COMPUTABLE",
    9: "RATE_OUT_OF_RANGE",
}

_TREND_CODES = {
    "0": 0, "NONE": 0,
    "1": 1, "DOUBLEUP": 1,
    "2": 2, "SINGLEUP": 2,
    "3": 3, "FORTYFIVEUP": 3,
    "4": 4, "FLAT": 4,
    "5": 5, "FORTYFIVEDOWN": 5,
    "6": 6, "SINGLEDOWN": 6,
    "7": 7, "DOUBLEDOWN": 7,
    "8": 8, "NOTCOMPUTABLE": 8,
    "9": 9, "RATEOUTOFRANGE": 9,
}

_WT_EPOCH_RE = re.compile(r"\((\d+)")


def mg_dl_to_mmol_l(mg_dl: int) -> float:
    """Converts an absolute mg/dL reading to mmol/L, floored to 1 decimal."""
    return math.floor(10 * (mg_dl / 18.0)) / 10


class DexcomApiError(Exception):
    pass


@dataclass
class Reading:
    mg_dl: int
    trend: int
    timestamp: datetime | None

    @property
    def trend_name(self) -> str:
        return TREND_NAMES.get(self.trend, "NONE")


def _normalize_trend(raw_trend) -> int:
    if raw_trend is None:
        return 0
    key = str(raw_trend).upper().replace(" ", "")
    return _TREND_CODES.get(key, 0)


def _parse_reading(raw: dict) -> Reading:
    match = _WT_EPOCH_RE.search(raw.get("WT", ""))
    timestamp = datetime.fromtimestamp(int(match.group(1)) / 1000) if match else None
    return Reading(mg_dl=raw["Value"], trend=_normalize_trend(raw.get("Trend")), timestamp=timestamp)


class DexcomClient:
    def __init__(self, server: str, username: str, password: str):
        self._base_url = f"https://{server}/ShareWebServices/Services"
        self._username = username
        self._password = password
        self._session_id = None

    def _post(self, path: str, json_body=None, params=None):
        try:
            response = requests.post(
                f"{self._base_url}{path}",
                json=json_body,
                params=params,
                headers={
                    "User-Agent": AGENT,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=10,
            )
        except requests.exceptions.RequestException as e:
            raise DexcomApiError(f"Request to {path} failed: {e}") from e

        if response.status_code != 200:
            raise DexcomApiError(f"{path} returned HTTP {response.status_code}: {response.text[:200]}")

        body = response.json()
        if isinstance(body, dict) and "Code" in body:
            raise DexcomApiError(f"{path} error {body.get('Code')}: {body.get('Message')}")
        return body

    def _login(self):
        account_id = self._post(
            "/General/AuthenticatePublisherAccount",
            {
                "accountName": self._username,
                "password": self._password,
                "applicationId": APPLICATION_ID,
            },
        )
        self._session_id = self._post(
            "/General/LoginPublisherAccountById",
            {
                "accountId": account_id,
                "password": self._password,
                "applicationId": APPLICATION_ID,
            },
        )

    def fetch_latest(self, max_count: int = 2, minutes: int = 1440) -> list[Reading]:
        if self._session_id is None:
            self._login()

        params = {"sessionID": self._session_id, "minutes": minutes, "maxCount": max(2, max_count)}
        try:
            raw_readings = self._post("/Publisher/ReadPublisherLatestGlucoseValues", params=params)
        except DexcomApiError:
            self._session_id = None
            self._login()
            params["sessionID"] = self._session_id
            raw_readings = self._post("/Publisher/ReadPublisherLatestGlucoseValues", params=params)

        return [_parse_reading(r) for r in raw_readings]
