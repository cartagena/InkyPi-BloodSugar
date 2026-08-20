import unittest
from unittest.mock import Mock, patch

# Imported under the plugin's real dotted path so this file runs unchanged
# both standalone (tests/conftest.py stubs the host) and inside a real InkyPi
# checkout. See tests/conftest.py.
from plugins.blood_sugar.dexcom_client import (
    DexcomApiError,
    DexcomClient,
    _normalize_trend,
    _parse_reading,
    mg_dl_to_mmol_l,
)


class NormalizeTrendTests(unittest.TestCase):
    def test_none_defaults_to_zero(self):
        self.assertEqual(_normalize_trend(None), 0)

    def test_numeric_string(self):
        self.assertEqual(_normalize_trend("4"), 4)

    def test_named_string_case_and_space_insensitive(self):
        self.assertEqual(_normalize_trend("FortyFiveDown"), 5)
        self.assertEqual(_normalize_trend("forty five down"), 5)

    def test_flat(self):
        self.assertEqual(_normalize_trend("Flat"), 4)

    def test_unknown_defaults_to_zero(self):
        self.assertEqual(_normalize_trend("SomethingUnexpected"), 0)


class ParseReadingTests(unittest.TestCase):
    def test_parses_value_and_trend(self):
        reading = _parse_reading({"Value": 105, "Trend": "Flat", "WT": "Date(1700000000000)"})
        self.assertEqual(reading.mg_dl, 105)
        self.assertEqual(reading.trend, 4)
        self.assertEqual(reading.trend_name, "FLAT")

    def test_parses_wt_epoch_ignoring_timezone_suffix(self):
        reading = _parse_reading({"Value": 100, "Trend": "Flat", "WT": "Date(1700000000000-0700)"})
        self.assertIsNotNone(reading.timestamp)
        self.assertEqual(int(reading.timestamp.timestamp() * 1000), 1700000000000)

    def test_missing_wt_yields_none_timestamp(self):
        reading = _parse_reading({"Value": 100, "Trend": "Flat"})
        self.assertIsNone(reading.timestamp)

    def test_missing_trend_defaults_to_none(self):
        reading = _parse_reading({"Value": 100, "WT": "Date(1700000000000)"})
        self.assertEqual(reading.trend_name, "NONE")


class MgDlToMmolLTests(unittest.TestCase):
    def test_floors_to_one_decimal(self):
        # 100 / 18 = 5.5555..., floored to 5.5 (not rounded to 5.6)
        self.assertEqual(mg_dl_to_mmol_l(100), 5.5)

    def test_exact_conversion(self):
        self.assertEqual(mg_dl_to_mmol_l(90), 5.0)

    def test_zero(self):
        self.assertEqual(mg_dl_to_mmol_l(0), 0.0)


def _fake_response(status_code=200, json_body=None, text=""):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = text
    return response


class DexcomClientTests(unittest.TestCase):
    def setUp(self):
        self.client = DexcomClient("share1.dexcom.com", "user", "pass")

    @patch("plugins.blood_sugar.dexcom_client.requests.post")
    def test_login_then_fetch_on_first_call(self, mock_post):
        mock_post.side_effect = [
            _fake_response(json_body="account-id"),
            _fake_response(json_body="session-id"),
            _fake_response(json_body=[{"Value": 100, "Trend": "Flat", "WT": "Date(1700000000000)"}]),
        ]

        readings = self.client.fetch_latest(max_count=2)

        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].mg_dl, 100)
        self.assertEqual(self.client._session_id, "session-id")
        self.assertEqual(mock_post.call_count, 3)

    @patch("plugins.blood_sugar.dexcom_client.requests.post")
    def test_reuses_session_on_subsequent_calls(self, mock_post):
        self.client._session_id = "existing-session"
        mock_post.side_effect = [
            _fake_response(json_body=[{"Value": 110, "Trend": "Flat", "WT": "Date(1700000000000)"}]),
        ]

        readings = self.client.fetch_latest(max_count=2)

        self.assertEqual(readings[0].mg_dl, 110)
        mock_post.assert_called_once()

    @patch("plugins.blood_sugar.dexcom_client.requests.post")
    def test_retries_once_after_relogin_on_session_expiry(self, mock_post):
        self.client._session_id = "stale-session"
        mock_post.side_effect = [
            _fake_response(status_code=500, text="session expired"),  # first fetch fails
            _fake_response(json_body="account-id"),  # relogin step 1
            _fake_response(json_body="new-session-id"),  # relogin step 2
            _fake_response(json_body=[{"Value": 120, "Trend": "Flat", "WT": "Date(1700000000000)"}]),  # retry succeeds
        ]

        readings = self.client.fetch_latest(max_count=2)

        self.assertEqual(readings[0].mg_dl, 120)
        self.assertEqual(self.client._session_id, "new-session-id")
        self.assertEqual(mock_post.call_count, 4)

    @patch("plugins.blood_sugar.dexcom_client.requests.post")
    def test_max_count_is_never_below_two(self, mock_post):
        self.client._session_id = "existing-session"
        mock_post.side_effect = [_fake_response(json_body=[])]

        self.client.fetch_latest(max_count=1)

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["params"]["maxCount"], 2)

    @patch("plugins.blood_sugar.dexcom_client.requests.post")
    def test_non_200_status_raises(self, mock_post):
        mock_post.return_value = _fake_response(status_code=401, text="unauthorized")

        with self.assertRaises(DexcomApiError):
            self.client._login()

    @patch("plugins.blood_sugar.dexcom_client.requests.post")
    def test_error_envelope_in_body_raises(self, mock_post):
        mock_post.return_value = _fake_response(json_body={"Code": "SessionNotValid", "Message": "bad session"})

        with self.assertRaises(DexcomApiError):
            self.client._post("/Publisher/ReadPublisherLatestGlucoseValues", params={})


if __name__ == "__main__":
    unittest.main()
