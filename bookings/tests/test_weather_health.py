from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from bookings.models import Booking, BookingEvent, WeatherIntegrationStatus
from bookings.weather_health import integration_health, record_automatic_result


class WeatherIntegrationHealthTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.employee = get_user_model().objects.create_user("health-employee", is_staff=True)

    def test_missing_row_and_empty_row_are_not_started(self):
        self.assertEqual(integration_health(now=self.now)["state"], "not_started")
        WeatherIntegrationStatus.objects.create()
        self.assertEqual(integration_health(now=self.now)["state"], "not_started")

    def test_failures_recovery_and_old_result(self):
        record_automatic_result(succeeded=True, attempted_at=self.now)
        for seconds in (1, 2, 3):
            row = record_automatic_result(succeeded=False, attempted_at=self.now + timedelta(seconds=seconds), error_code="timeout")
        self.assertEqual(row.consecutive_failures, 3)
        self.assertEqual(row.last_success_at, self.now)
        self.assertEqual(integration_health(now=self.now + timedelta(seconds=3))["state"], "error")
        record_automatic_result(succeeded=True, attempted_at=self.now + timedelta(seconds=4))
        record_automatic_result(succeeded=False, attempted_at=self.now + timedelta(seconds=1), error_code="timeout")
        row.refresh_from_db()
        self.assertEqual((row.status, row.consecutive_failures, row.error_code), ("success", 0, ""))
        self.assertEqual(integration_health(now=self.now + timedelta(seconds=4))["state"], "success")

    def test_first_failure_and_stale_boundary(self):
        record_automatic_result(succeeded=False, attempted_at=self.now, error_code="forecast_unavailable")
        self.assertEqual(integration_health(now=self.now)["state"], "error")
        self.assertIsNone(integration_health(now=self.now)["last_success_at"])
        record_automatic_result(succeeded=True, attempted_at=self.now + timedelta(seconds=1))
        self.assertEqual(integration_health(now=self.now + timedelta(hours=2, seconds=1))["state"], "success")
        self.assertEqual(integration_health(now=self.now + timedelta(hours=2, seconds=2))["state"], "stale")

    def test_database_singleton_and_safe_error(self):
        WeatherIntegrationStatus.objects.create()
        with self.assertRaises(IntegrityError), transaction.atomic():
            WeatherIntegrationStatus.objects.create(id=2)
        with self.assertRaises(ValueError):
            record_automatic_result(succeeded=False, attempted_at=self.now, error_code="private traceback")
        self.assertEqual(WeatherIntegrationStatus.objects.get().error_code, "")

    def test_endpoint_authorization_and_manual_recheck_does_not_update_heartbeat(self):
        self.assertEqual(self.client.get("/api/weather/health/").status_code, 401)
        outsider = get_user_model().objects.create_user("health-outsider")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get("/api/weather/health/").status_code, 403)
        self.client.force_login(self.employee)
        self.assertEqual(self.client.post("/api/weather/health/").status_code, 405)
        self.assertEqual(self.client.get("/api/weather/health/").json()["state"], "not_started")
        record_automatic_result(succeeded=True, attempted_at=self.now)
        before = list(WeatherIntegrationStatus.objects.values())
        with patch("bookings.services.fetch_forecast") as fetch:
            from bookings.weather_policy import Forecast
            fetch.return_value = Forecast.unavailable(self.now, "network_disabled")
            self.assertEqual(self.client.post("/api/weather/recheck/", data="{}", content_type="application/json").status_code, 200)
        self.assertEqual(list(WeatherIntegrationStatus.objects.values()), before)
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(BookingEvent.objects.count(), 0)
