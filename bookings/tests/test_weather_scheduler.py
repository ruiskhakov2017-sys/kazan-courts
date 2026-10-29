import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email.utils import format_datetime
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from bookings.models import Booking, Court, Customer, OutboxMessage, WeatherIntegrationStatus
from bookings.tests.weather_fixtures import weather_forecast, weather_payload
from bookings.weather_client import AutomaticForecastResult, _retry_after, fetch_automatic_forecast
from bookings.weather_scheduler import LOCK_KEY, run_weather_check
from bookings.weather_policy import MOSCOW
from bookings.weather_transitions import apply_weather_transition


def http_error(code, headers=None):
    return HTTPError("https://example.invalid", code, "synthetic", headers or {}, io.BytesIO())


@override_settings(WEATHER_NETWORK_ENABLED=True)
class AutomaticWeatherTests(TransactionTestCase):
    def setUp(self):
        self.now = timezone.now()
        employee = get_user_model().objects.create_user("scheduler", is_staff=True)
        customer = Customer.objects.create(name="Fictional", phone="DEMO")
        court = Court.objects.create(name="Outside", court_type="outdoor", latitude=55, longitude=49)
        start = (self.now + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
        self.booking = Booking.objects.create(court=court, customer=customer, created_by=employee,
                                              starts_at=start, ends_at=start + timedelta(hours=1), weather_status="unknown")

    def response(self, payload):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response

    def test_timeout_retry_then_success_and_pauses(self):
        payload = weather_payload(self.now.date())
        with patch("bookings.weather_client.urlopen", side_effect=[URLError("timeout"), URLError("timeout"), self.response(payload)]) as transport, patch("bookings.weather_client.time.sleep") as sleep:
            result = fetch_automatic_forecast()
        self.assertIsNotNone(result.forecast)
        self.assertEqual(transport.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 3])
        self.assertTrue(all(call.kwargs["timeout"] == 5 for call in transport.call_args_list))

    def test_429_long_retry_after_is_persisted_and_next_run_deferred(self):
        with patch("bookings.weather_client.urlopen", side_effect=http_error(429, {"Retry-After": "600"})) as transport, patch("bookings.weather_client.time.sleep") as sleep:
            result = run_weather_check()
            self.assertEqual(result["status"], "unavailable")
            self.assertTrue(result["retryable"])
            state = WeatherIntegrationStatus.objects.get()
            self.assertEqual(state.error_code, "rate_limited")
            self.assertGreater(state.next_allowed_at, self.now + timedelta(minutes=9))
            self.assertEqual(run_weather_check()["status"], "deferred")
        self.assertEqual(transport.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(OutboxMessage.objects.count(), 0)

    def test_invalid_and_permanent_errors_do_not_retry(self):
        for side_effect, code in ((http_error(403), "forecast_unavailable"), (http_error(501), "forecast_unavailable"), (self.response({}), "invalid_response")):
            with self.subTest(code=code), patch("bookings.weather_client.urlopen", side_effect=[side_effect]) as transport, patch("bookings.weather_client.time.sleep") as sleep:
                result = fetch_automatic_forecast()
                self.assertEqual(result.error_code, code)
                self.assertFalse(result.retryable)
                self.assertEqual(transport.call_count, 1)
                sleep.assert_not_called()

    def test_503_retries_and_exhausts(self):
        with patch("bookings.weather_client.urlopen", side_effect=[http_error(503), http_error(503), http_error(503)]) as transport, patch("bookings.weather_client.time.sleep") as sleep:
            result = fetch_automatic_forecast()
            self.assertEqual(result.error_code, "forecast_unavailable")
            self.assertTrue(result.retryable)
            self.assertEqual(transport.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 3])

    def test_retry_after_http_date_and_partial_response(self):
        retry_at = self.now + timedelta(minutes=15)
        with patch("bookings.weather_client.urlopen", side_effect=http_error(503, {"Retry-After": format_datetime(retry_at)})), patch("bookings.weather_client.time.sleep") as sleep:
            result = fetch_automatic_forecast()
            self.assertGreater(result.next_allowed_at, self.now + timedelta(minutes=14))
            sleep.assert_not_called()
        payload = weather_payload(self.now.date())
        payload["hourly"]["weather_code"][0] = None
        with patch("bookings.weather_client.urlopen", return_value=self.response(payload)) as transport:
            self.assertIsNotNone(fetch_automatic_forecast().forecast)
            self.assertEqual(transport.call_count, 1)

    def test_retry_after_is_bounded_and_handles_bad_or_huge_input(self):
        cap = self.now + timedelta(days=7)
        self.assertEqual(_retry_after("172800", self.now), self.now + timedelta(hours=48))
        self.assertEqual(_retry_after(format_datetime((self.now + timedelta(hours=48)).replace(microsecond=0)), self.now),
                         (self.now + timedelta(hours=48)).replace(microsecond=0))
        with self.assertLogs("bookings.weather_client", level="WARNING") as logs:
            self.assertEqual(_retry_after("999999999999999", self.now), cap)
            self.assertEqual(_retry_after("9" * 10000, self.now), cap)
            self.assertEqual(_retry_after("invalid", self.now), self.now + timedelta(hours=1))
            self.assertEqual(_retry_after(format_datetime(self.now + timedelta(days=99)), self.now), cap)
        self.assertEqual(len(logs.records), 4)
        self.assertNotIn("999999999999999", " ".join(logs.output))
        self.assertEqual(_retry_after("00000000000000001", self.now), self.now + timedelta(seconds=1))

    def test_48_hour_retry_after_stops_requests_and_reports_error(self):
        with patch("bookings.weather_client.urlopen", side_effect=http_error(429, {"Retry-After": "172800"})) as transport:
            self.assertEqual(run_weather_check()["status"], "unavailable")
            state = WeatherIntegrationStatus.objects.get()
            self.assertEqual(state.status, "error")
            self.assertGreaterEqual(state.next_allowed_at, self.now + timedelta(hours=48))
            self.assertEqual(run_weather_check()["status"], "deferred")
            self.assertEqual(transport.call_count, 1)

    def test_wmo_unknown_and_missing_hours_do_not_block_other_days_rain(self):
        payload = weather_payload(self.now.astimezone(MOSCOW).date(), days=2)
        hours = payload["hourly"]["time"]
        rain_hour = self.booking.starts_at.astimezone(MOSCOW).strftime("%Y-%m-%dT%H:00")
        payload["hourly"]["weather_code"][hours.index(rain_hour)] = 61
        payload["hourly"]["weather_code"][0] = 71
        payload["hourly"]["weather_code"][1] = 95
        payload["hourly"]["weather_code"][2] = None
        del payload["hourly"]["time"][3]
        del payload["hourly"]["weather_code"][3]
        with patch("bookings.weather_client.urlopen", return_value=self.response(payload)):
            self.assertEqual(run_weather_check()["events"], 1)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.weather_status, "blocked")
        self.assertEqual(OutboxMessage.objects.count(), 1)

    def test_unknown_and_rain_priority_and_outside_forecast(self):
        from bookings.weather_policy import blocks_for_day, blocks_for_interval, interval_status
        day = self.now.astimezone(MOSCOW).date()
        payload = weather_payload(day)
        payload["hourly"]["weather_code"][0:6] = [71, 95, None, 0, 61, 0]
        payload["hourly"]["weather_code"][6:12] = [71, 95, None, 0, 0, 0]
        with patch("bookings.weather_client.urlopen", return_value=self.response(payload)):
            forecast = fetch_automatic_forecast().forecast
        blocks = blocks_for_day(forecast, day)
        self.assertEqual([b.status for b in blocks], ["blocked", "unknown", "clear", "clear"])
        later = self.booking.starts_at + timedelta(days=8)
        self.assertEqual(interval_status(blocks_for_interval(forecast, later, later + timedelta(hours=1))), "unknown")
        Booking.objects.filter(pk=self.booking.pk).update(starts_at=later, ends_at=later + timedelta(hours=1), weather_status="blocked")
        with patch("bookings.weather_scheduler.fetch_automatic_forecast", return_value=AutomaticForecastResult(forecast)):
            self.assertEqual(run_weather_check(), {"status": "success", "checked": 0, "events": 0})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.weather_status, "blocked")
        self.assertEqual(WeatherIntegrationStatus.objects.get().status, "success")

    def test_booking_processing_failure_does_not_report_successful_cycle(self):
        output = io.StringIO()
        forecast = weather_forecast(self.now)
        with patch("bookings.weather_scheduler.fetch_automatic_forecast", return_value=AutomaticForecastResult(forecast)), patch("bookings.weather_scheduler.apply_weather_transition", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                call_command("check_weather", stdout=output)
        self.assertNotIn("status=success", output.getvalue())
        self.assertEqual(WeatherIntegrationStatus.objects.get().status, "success")

    def test_clear_blocked_clear_and_error_preserves_blocked(self):
        clear = weather_forecast(self.now)
        rain = weather_forecast(self.now, changes={self.booking.starts_at.astimezone(MOSCOW): 61})
        with patch("bookings.weather_scheduler.fetch_automatic_forecast", return_value=AutomaticForecastResult(clear)):
            self.assertEqual(run_weather_check()["events"], 0)
            self.booking.refresh_from_db()
            self.assertEqual(self.booking.weather_status, "clear")
        with patch("bookings.weather_scheduler.fetch_automatic_forecast", return_value=AutomaticForecastResult(rain)):
            self.assertEqual(run_weather_check()["events"], 1)
            self.assertEqual(run_weather_check()["events"], 0)
        with patch("bookings.weather_scheduler.fetch_automatic_forecast", return_value=AutomaticForecastResult(None, "forecast_unavailable")):
            self.assertEqual(run_weather_check()["status"], "unavailable")
            self.booking.refresh_from_db()
            self.assertEqual(self.booking.weather_status, "blocked")
        with patch("bookings.weather_scheduler.fetch_automatic_forecast", return_value=AutomaticForecastResult(clear)):
            self.assertEqual(run_weather_check()["events"], 1)
            self.assertEqual(WeatherIntegrationStatus.objects.get().status, "success")
        self.assertEqual(OutboxMessage.objects.count(), 2)

    def test_parallel_lock_and_release(self):
        # A separate PostgreSQL session holds the same lock.
        from django.db import connections
        other = connections["default"].copy()
        try:
            with other.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
                self.assertTrue(cursor.fetchone()[0])
            with patch("bookings.weather_scheduler.fetch_automatic_forecast") as fetch:
                self.assertEqual(run_weather_check()["status"], "already_running")
                fetch.assert_not_called()
        finally:
            other.close()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
            self.assertTrue(cursor.fetchone()[0])
            cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])

    def test_concurrent_cancel_after_snapshot_is_not_overwritten(self):
        rain = weather_forecast(self.now, changes={self.booking.starts_at.astimezone(MOSCOW): 61})
        def change_before_transition(**kwargs):
            def cancel():
                try:
                    Booking.objects.filter(pk=self.booking.pk).update(status="cancelled")
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(cancel).result(timeout=10)
            return apply_weather_transition(**kwargs)
        with patch("bookings.weather_scheduler.fetch_automatic_forecast", return_value=AutomaticForecastResult(rain)), patch("bookings.weather_scheduler.apply_weather_transition", side_effect=change_before_transition):
            self.assertEqual(run_weather_check()["events"], 0)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "cancelled")
        self.assertEqual(self.booking.weather_status, "unknown")
        self.assertFalse(OutboxMessage.objects.exists())

    def test_outbox_failure_rolls_back_booking_and_releases_lock(self):
        rain = weather_forecast(self.now, changes={self.booking.starts_at.astimezone(MOSCOW): 61})
        with patch("bookings.weather_scheduler.fetch_automatic_forecast", return_value=AutomaticForecastResult(rain)), patch("bookings.weather_transitions.OutboxMessage.objects.create", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                run_weather_check()
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.weather_status, "unknown")
        self.assertFalse(self.booking.events.exists())
        self.assertFalse(OutboxMessage.objects.exists())
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
            self.assertTrue(cursor.fetchone()[0])
            cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])
