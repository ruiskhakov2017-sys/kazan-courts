from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
import json

from django.test import SimpleTestCase, override_settings

from bookings.weather_client import MAX_RESPONSE_BYTES, fetch_forecast, parse_forecast
from bookings.weather_policy import MOSCOW, Forecast, RAIN_CODES, blocks_for_day, blocks_for_interval, interval_status
from .weather_fixtures import weather_forecast, weather_payload


class WeatherPolicyTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 1, 9, tzinfo=MOSCOW)

    def test_each_approved_rain_code_blocks_its_entire_period(self):
        expected = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}
        self.assertEqual(RAIN_CODES, expected)
        for code in expected:
            with self.subTest(code=code):
                forecast = weather_forecast(self.now, changes={self.now.replace(hour=14): code})
                blocks = blocks_for_day(forecast, self.now.date())
                self.assertEqual([block.status for block in blocks], ["clear", "clear", "blocked", "clear"])
                self.assertEqual((blocks[2].starts_at.hour, blocks[2].ends_at.hour, blocks[2].rain_codes), (12, 18, (code,)))

    def test_cloud_and_fog_codes_do_not_create_rain_bans(self):
        for code in (0, 1, 2, 3, 45, 48):
            changes = {self.now.replace(hour=hour): code for hour in range(24)}
            self.assertEqual([block.status for block in blocks_for_day(weather_forecast(self.now, changes=changes), self.now.date())], ["clear"] * 4)

    def test_missing_null_unsupported_and_invalid_codes_are_unknown(self):
        for code in (None, True, "61", 61.5, 71, 73, 75, 77, 85, 86, 95, 96, 97, 99, 999):
            with self.subTest(code=code):
                forecast = weather_forecast(self.now, changes={self.now.replace(hour=14): code})
                self.assertEqual(blocks_for_day(forecast, self.now.date())[2].status, "unknown")
        empty = Forecast.unavailable(self.now, "missing")
        self.assertEqual([block.status for block in blocks_for_day(empty, self.now.date())], ["unknown"] * 4)

    def test_known_rain_takes_precedence_over_missing_hours(self):
        payload = weather_payload(self.now.date(), changes={self.now.replace(hour=14): 53})
        payload["hourly"]["weather_code"][12:14] = [None, None]
        blocks = blocks_for_day(parse_forecast(payload, self.now), self.now.date())
        self.assertEqual(blocks[2].status, "blocked")
        self.assertEqual(interval_status([blocks[0], blocks[2], blocks[3]]), "blocked")

    def test_partial_overlap_and_exclusive_end_boundaries(self):
        forecast = weather_forecast(self.now, changes={self.now.replace(hour=13): 61})
        at = lambda hour, minute=0: self.now.replace(hour=hour, minute=minute)
        self.assertEqual(interval_status(blocks_for_interval(forecast, at(11), at(12))), "clear")
        self.assertEqual(interval_status(blocks_for_interval(forecast, at(11), at(12, 10))), "blocked")
        self.assertEqual(interval_status(blocks_for_interval(forecast, at(17, 50), at(19))), "blocked")
        self.assertEqual(interval_status(blocks_for_interval(forecast, at(18), at(19))), "clear")

    def test_midnight_and_utc_inputs_use_kazan_dates(self):
        tomorrow = (self.now + timedelta(days=1)).replace(hour=0)
        forecast = weather_forecast(self.now, changes={tomorrow: 80})
        start = self.now.replace(hour=23).astimezone(UTC)
        end = (tomorrow + timedelta(hours=1)).astimezone(UTC)
        blocks = blocks_for_interval(forecast, start, end)
        self.assertEqual([(block.starts_at.hour, block.status) for block in blocks], [(18, "clear"), (0, "blocked")])
        self.assertEqual(blocks[0].ends_at, tomorrow)

    def test_dates_outside_returned_horizon_are_unknown(self):
        forecast = weather_forecast(self.now, days=16)
        later = self.now + timedelta(days=25)
        self.assertEqual(interval_status(blocks_for_interval(forecast, later, later + timedelta(hours=1))), "unknown")


class WeatherClientTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 1, 9, tzinfo=MOSCOW)

    @override_settings(WEATHER_NETWORK_ENABLED=False)
    def test_disabled_client_never_calls_transport(self):
        with patch("bookings.weather_client.urlopen") as transport:
            result = fetch_forecast()
        transport.assert_not_called()
        self.assertEqual(result.unavailable_reason, "network_disabled")
        self.assertFalse(result.hours)

    @override_settings(WEATHER_NETWORK_ENABLED=True)
    def test_request_contract_and_parsed_local_hours(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(weather_payload(self.now.date(), changes={self.now: 61.0})).encode()
        with patch("bookings.weather_client.urlopen", return_value=response) as transport:
            result = fetch_forecast()
        url = transport.call_args.args[0]
        self.assertEqual((urlparse(url).scheme, urlparse(url).netloc, urlparse(url).path), ("https", "api.open-meteo.com", "/v1/forecast"))
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query, {"latitude": ["55.793000"], "longitude": ["49.123000"],
                                 "hourly": ["weather_code"], "timezone": ["Europe/Moscow"], "forecast_days": ["16"]})
        self.assertEqual(transport.call_args.kwargs, {"timeout": 5})
        response.__enter__.return_value.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
        self.assertEqual(len(result.hours), 48)
        self.assertEqual(result.hours[self.now.replace(hour=0)], 0)
        self.assertEqual(result.hours[self.now], 61)
        self.assertEqual(blocks_for_day(result, self.now.date())[1].status, "blocked")

    @override_settings(WEATHER_NETWORK_ENABLED=True)
    def test_transport_failures_return_unknown_without_retry(self):
        for error in (URLError("Synthetic unavailable"), TimeoutError("Synthetic timeout"), OSError("Synthetic IO error")):
            with self.subTest(error=type(error).__name__), patch("bookings.weather_client.urlopen", side_effect=error) as transport:
                result = fetch_forecast()
            self.assertEqual(transport.call_count, 1)
            self.assertFalse(result.hours)
            self.assertEqual(result.unavailable_reason, "forecast_unavailable")

    @override_settings(WEATHER_NETWORK_ENABLED=True)
    def test_bad_json_schema_and_oversized_response_return_unknown(self):
        cases = (b'\xff', b'{', b'{"value": NaN}', b'null', b'{}', b'x' * (MAX_RESPONSE_BYTES + 1),
                 json.dumps({"timezone": "UTC", "hourly": {"time": [], "weather_code": []}}).encode())
        for body in cases:
            with self.subTest(size=len(body)):
                response = MagicMock()
                response.__enter__.return_value.read.return_value = body
                with patch("bookings.weather_client.urlopen", return_value=response):
                    self.assertFalse(fetch_forecast().hours)

    def test_bad_array_lengths_duplicates_and_timestamps_are_rejected(self):
        payload = weather_payload(self.now.date())
        cases = [None, [], {**payload, "hourly": []},
                 {**payload, "hourly": {"time": ["2026-07-01T00:00"], "weather_code": []}},
                 {**payload, "hourly": {"time": ["2026-07-01T00:00"] * 2, "weather_code": [0, 0]}}]
        cases += [{**payload, "hourly": {"time": [value], "weather_code": [0]}} for value in
                  ("2026-02-30T00:00", "2026-07-01T25:00", "2026-07-01T00:10", "2026-07-01T00:00Z", 1)]
        for value in cases:
            with self.subTest(payload=value), self.assertRaises(ValueError):
                parse_forecast(value, self.now)
