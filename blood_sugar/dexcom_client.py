import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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
    # Timezone-aware, always UTC. Callers convert for display — the raw WT
    # epoch carries no zone of its own, and rendering it in whatever local
    # zone the Pi's clock happens to be set to (rather than the timezone
    # configured for the display) silently shifts every reading time.
    timestamp: datetime | None

    @property
    def trend_name(self) -> str:
        return TREND_NAMES.get(self.trend, "NONE")


def _normalize_trend(raw_trend: object) -> int:
    if raw_trend is None:
        return 0
    key = str(raw_trend).upper().replace(" ", "")
    return _TREND_CODES.get(key, 0)


def _parse_mg_dl(value: object) -> int:
    """Coerce an API-supplied glucose value to int.

    Raises DexcomApiError rather than letting a bare TypeError/ValueError out,
    so an unexpected response shape surfaces as "the Dexcom call failed" like
    every other API-level problem instead of as an opaque crash.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise DexcomApiError(f"Unexpected glucose value from Dexcom: {value!r}")
    try:
        return int(value)
    except ValueError as e:
        raise DexcomApiError(f"Unexpected glucose value from Dexcom: {value!r}") from e


def _parse_reading(raw: dict[str, object]) -> Reading:
    wt = raw.get("WT")
    match = _WT_EPOCH_RE.search(wt) if isinstance(wt, str) else None
    timestamp = (
        datetime.fromtimestamp(int(match.group(1)) / 1000, UTC) if match else None
    )
    return Reading(
        mg_dl=_parse_mg_dl(raw.get("Value")),
        trend=_normalize_trend(raw.get("Trend")),
        timestamp=timestamp,
    )


class DexcomClient:
    def __init__(self, server: str, username: str, password: str) -> None:
        self._base_url = f"https://{server}/ShareWebServices/Services"
        self._username = username
        self._password = password
        self._session_id: str | None = None

    def _post(
        self,
        path: str,
        json_body: dict[str, object] | None = None,
        params: dict[str, Any] | None = None,
    ) -> object:
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

    def _login(self) -> None:
        account_id = self._post(
            "/General/AuthenticatePublisherAccount",
            {
                "accountName": self._username,
                "password": self._password,
                "applicationId": APPLICATION_ID,
            },
        )
        session_id = self._post(
            "/General/LoginPublisherAccountById",
            {
                "accountId": account_id,
                "password": self._password,
                "applicationId": APPLICATION_ID,
            },
        )
        if not isinstance(session_id, str) or not session_id:
            raise DexcomApiError("Dexcom login did not return a session id.")
        self._session_id = session_id

    def fetch_latest(self, max_count: int = 2, minutes: int = 1440) -> list[Reading]:
        if self._session_id is None:
            self._login()

        params: dict[str, Any] = {
            "sessionID": self._session_id,
            "minutes": minutes,
            "maxCount": max(2, max_count),
        }
        try:
            raw_readings = self._post(
                "/Publisher/ReadPublisherLatestGlucoseValues", params=params
            )
        except DexcomApiError:
            # Sessions expire server-side with no advance signal, so one
            # re-login + retry is the normal path, not an exceptional one.
            self._session_id = None
            self._login()
            params["sessionID"] = self._session_id
            raw_readings = self._post(
                "/Publisher/ReadPublisherLatestGlucoseValues", params=params
            )

        if not isinstance(raw_readings, list):
            raise DexcomApiError(
                f"Expected a list of glucose readings, got {type(raw_readings).__name__}."
            )
        return [_parse_reading(r) for r in raw_readings if isinstance(r, dict)]
