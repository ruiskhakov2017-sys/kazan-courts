from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import Client, TransactionTestCase
from psycopg.errors import DeadlockDetected
from django.db import OperationalError

from bookings import services
from bookings.models import Booking, BookingEvent, Court, Customer
from bookings.weather_policy import Forecast, MOSCOW
from .weather_fixtures import weather_forecast


class WeatherOperationTests(TransactionTestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 1, 9, tzinfo=MOSCOW)
        self.clock = patch("bookings.services.timezone.now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.employee = get_user_model().objects.create_user("weather-employee", is_staff=True)
        self.outsider = get_user_model().objects.create_user("weather-outsider")
        self.customer = Customer.objects.create(name="Fictional weather customer", phone="DEMO-WEATHER")
        self.courts = [Court.objects.create(name=f"Fictional outdoor {number}", court_type="outdoor", latitude=55, longitude=49)
                       for number in range(4)]
        self.court = self.courts[0]
        self.indoor = Court.objects.create(name="Fictional indoor", court_type="indoor", latitude=55, longitude=49)
        self.start = (self.now + timedelta(days=1)).replace(hour=14)
        self.forecast = weather_forecast(self.now)
        self.provider = patch("bookings.services.fetch_forecast", side_effect=self.provide_forecast).start()
        self.api_provider = patch("bookings.api.fetch_forecast", side_effect=self.provide_forecast).start()
        self.addCleanup(patch.stopall)
        self.client.force_login(self.employee)

    def provide_forecast(self):
        self.assertFalse(connection.in_atomic_block, "Weather fetch ran inside a database transaction")
        return self.forecast

    def create(self, **changes):
        return services.create_booking(**{"actor": self.employee, "customer_id": self.customer.pk,
                                         "court_id": self.court.pk, "starts_at": self.start,
                                         "ends_at": self.start + timedelta(hours=1), **changes})

    def state(self):
        return [list(model.objects.order_by("pk").values()) for model in (Court, Customer, Booking, BookingEvent)]

    def post(self, path, payload=None, client=None, **headers):
        return (client or self.client).post(path, {} if payload is None else payload, content_type="application/json", **headers)

    def test_shared_forecast_blocks_all_four_outdoor_courts_but_not_indoor(self):
        self.forecast = weather_forecast(self.now, changes={self.start.replace(hour=13): 53})
        for court in self.courts:
            with self.assertRaises(services.BookingOperationError) as error:
                self.create(court_id=court.pk)
            self.assertEqual((error.exception.code, error.exception.status), ("weather_conflict", 409))
        self.assertEqual(Booking.objects.count(), 0)
        self.provider.reset_mock()
        indoor = self.create(court_id=self.indoor.pk)
        self.provider.assert_not_called()
        self.assertEqual(indoor.weather_status, "not_applicable")
        self.assertIsNone(indoor.weather_checked_at)
        self.assertEqual(BookingEvent.objects.count(), 1)

    def test_unknown_weather_is_saved_with_visible_warning_and_history(self):
        self.forecast = Forecast.unavailable(self.now, "forecast_unavailable")
        payload = {"court_id": self.court.pk, "customer_id": self.customer.pk,
                   "starts_at": self.start.isoformat(), "ends_at": (self.start + timedelta(hours=1)).isoformat()}
        response = self.post("/api/bookings/", payload)
        self.assertEqual(response.status_code, 201)
        row = response.json()["booking"]
        self.assertEqual(row["weather_status"], "unknown")
        self.assertTrue(row["needs_weather_recheck"])
        self.assertTrue(response.json()["warnings"])
        booking = Booking.objects.get()
        self.assertEqual(booking.weather_checked_at, self.now)
        self.assertEqual(booking.events.get().after, services.booking_snapshot(booking))
        schedule = self.client.get("/api/schedule/", {"court_id": self.court.pk, "date": self.start.date().isoformat()})
        self.assertEqual(schedule.json()["bookings"][0]["weather_status"], "unknown")

    def test_dates_beyond_forecast_horizon_are_allowed_with_recheck_flag(self):
        later = self.now + timedelta(days=25)
        booking = self.create(starts_at=later, ends_at=later + timedelta(hours=1))
        self.assertEqual(booking.weather_status, "unknown")
        self.assertTrue(services.booking_snapshot(booking)["needs_weather_recheck"])

    def test_drying_and_maintenance_reject_new_and_rescheduled_bookings(self):
        booking = self.create()
        for status in ("drying", "maintenance"):
            Court.objects.filter(pk=self.court.pk).update(surface_status=status)
            before = self.state()
            for operation in (lambda: self.create(starts_at=self.start + timedelta(hours=2), ends_at=self.start + timedelta(hours=3)),
                              lambda: services.reschedule_booking(actor=self.employee, booking_id=booking.pk,
                                                                   starts_at=self.start + timedelta(hours=2), ends_at=self.start + timedelta(hours=3))):
                with self.assertRaises(services.BookingOperationError) as error:
                    operation()
                self.assertEqual(error.exception.code, "surface_unavailable")
                self.assertEqual(self.state(), before)

    def test_rain_failure_preserves_original_booking_and_history(self):
        booking = self.create()
        before = self.state()
        target = self.start.replace(hour=8)
        self.forecast = weather_forecast(self.now, changes={target: 82})
        with self.assertRaises(services.BookingOperationError) as error:
            services.reschedule_booking(actor=self.employee, booking_id=booking.pk, starts_at=target, ends_at=target + timedelta(hours=1))
        self.assertEqual(error.exception.code, "weather_conflict")
        self.assertEqual(self.state(), before)

    def test_manual_recheck_marks_future_active_outdoor_bookings_without_cancelling(self):
        active = [self.create(court_id=court.pk) for court in self.courts]
        untouched = [self.create(court_id=self.indoor.pk)]
        for changes in ({"starts_at": self.now - timedelta(days=1), "ends_at": self.now - timedelta(days=1) + timedelta(hours=1)},
                        {"status": "cancelled"}, {"status": "cancelled", "archived_at": self.now}):
            untouched.append(Booking.objects.create(court=self.court, customer=self.customer, created_by=self.employee,
                                                    **{"starts_at": self.start, "ends_at": self.start + timedelta(hours=1), **changes}))
        untouched_before = list(Booking.objects.filter(pk__in=[row.pk for row in untouched]).order_by("pk").values())
        self.forecast = weather_forecast(self.now, changes={self.start: 61})
        self.provider.reset_mock()
        response = self.post("/api/weather/recheck/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checked_count"], 4)
        self.assertEqual(len(response.json()["affected_bookings"]), 4)
        self.assertEqual(self.provider.call_count, 1)
        for booking in active:
            booking.refresh_from_db()
            self.assertEqual(booking.status, "active")
            self.assertEqual(booking.starts_at, self.start)
            self.assertEqual(booking.weather_status, "blocked")
            events = list(booking.events.all())
            self.assertEqual([event.event_type for event in events], ["created", "weather_checked"])
            self.assertEqual(events[1].before, events[0].after)
            self.assertEqual(events[1].after, services.booking_snapshot(booking))
            self.assertEqual(events[1].actor_id, self.employee.pk)
        row = response.json()["affected_bookings"][0]
        self.assertEqual(row["customer"]["phone"], "DEMO-WEATHER")
        self.assertEqual(row["blocks"][0]["status"], "blocked")
        self.assertEqual(list(Booking.objects.filter(pk__in=[row.pk for row in untouched]).order_by("pk").values()), untouched_before)
        self.assertEqual(Court.objects.get(pk=self.court.pk).surface_status, "available")

    def test_recheck_unknown_then_clear_preserves_previous_events(self):
        booking = self.create()
        original_event = booking.events.get()
        self.forecast = Forecast.unavailable(self.now, "missing")
        result = services.recheck_weather(actor=self.employee)
        self.assertEqual(len(result["affected_bookings"]), 1)
        booking.refresh_from_db()
        self.assertEqual(booking.weather_status, "unknown")
        self.forecast = weather_forecast(self.now)
        self.assertEqual(services.recheck_weather(actor=self.employee)["affected_bookings"], [])
        booking.refresh_from_db()
        self.assertEqual(booking.weather_status, "clear")
        self.assertFalse(services.booking_snapshot(booking)["needs_weather_recheck"])
        original_event.refresh_from_db()
        self.assertEqual(original_event.event_type, "created")
        self.assertEqual(booking.events.count(), 3)

    def test_history_failure_rolls_back_the_entire_recheck_batch(self):
        self.create()
        self.create(court_id=self.courts[1].pk)
        self.forecast = weather_forecast(self.now, changes={self.start: 65})
        before = self.state()
        real_event = BookingEvent.objects.create
        calls = []
        def fail_second(**values):
            calls.append(values)
            if len(calls) == 2:
                raise IntegrityError("Synthetic history failure")
            return real_event(**values)
        with patch("bookings.services.BookingEvent.objects.create", side_effect=fail_second):
            with self.assertLogs("bookings.api", level="ERROR"):
                response = self.post("/api/weather/recheck/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.state(), before)

    def test_surface_close_and_confirmation_record_readiness(self):
        close = f"/api/courts/{self.court.pk}/surface/close/"
        ready = f"/api/courts/{self.court.pk}/surface/ready/"
        self.assertEqual(self.post(close).status_code, 200)
        self.assertEqual(self.post(close).status_code, 409)
        self.assertEqual(self.post(ready).status_code, 200)
        self.court.refresh_from_db()
        self.assertEqual(self.court.surface_status, "available")
        self.assertEqual(self.court.last_inspected_at, self.now)
        self.assertEqual(self.post(ready).status_code, 409)
        self.assertEqual(Booking.objects.count(), 0)

    def test_confirming_surface_keeps_current_and_future_weather_bans_independent(self):
        booking = self.create()
        services.close_surface(actor=self.employee, court_id=self.court.pk)
        self.forecast = weather_forecast(self.now, changes={self.now.replace(hour=7): 55, self.start: 61})
        days = (self.now.date().isoformat(), self.start.date().isoformat())
        blocks_before = {day: self.client.get("/api/weather/", {"court_id": self.court.pk, "date": day}).json()["blocks"]
                         for day in days}
        before = self.state()
        response = self.post(f"/api/courts/{self.court.pk}/surface/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["warnings"])
        self.court.refresh_from_db()
        self.assertEqual(self.court.surface_status, "available")
        self.assertEqual(self.court.last_inspected_at, self.now)
        self.assertEqual(self.state()[1:], before[1:])
        for day in days:
            blocks = self.client.get("/api/weather/", {"court_id": self.court.pk, "date": day}).json()["blocks"]
            self.assertEqual(blocks, blocks_before[day])
        self.assertEqual(blocks_before[days[0]][1]["status"], "blocked")
        self.assertEqual(blocks_before[days[1]][2]["status"], "blocked")
        after_confirmation = self.state()
        for target in (self.now + timedelta(hours=1), self.start + timedelta(hours=2)):
            times = {"starts_at": target.isoformat(), "ends_at": (target + timedelta(hours=1)).isoformat()}
            for path, payload in (("/api/bookings/", {"court_id": self.court.pk, "customer_id": self.customer.pk, **times}),
                                  (f"/api/bookings/{booking.pk}/reschedule/", times)):
                with self.subTest(path=path, target=target):
                    response = self.post(path, payload)
                    self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "weather_conflict"))
                    self.assertEqual(self.state(), after_confirmation)
        clear_time = self.now.replace(hour=14)
        self.create(starts_at=clear_time, ends_at=clear_time + timedelta(hours=1))
        self.assertEqual((Booking.objects.count(), BookingEvent.objects.count()), (2, 2))

    def test_unknown_allows_inspected_surface_with_warning_and_cannot_override_maintenance(self):
        services.close_surface(actor=self.employee, court_id=self.court.pk)
        self.forecast = Forecast.unavailable(self.now, "missing")
        response = self.post(f"/api/courts/{self.court.pk}/surface/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["warnings"])
        Court.objects.filter(pk=self.court.pk).update(surface_status="maintenance")
        before = self.state()
        self.assertEqual(self.post(f"/api/courts/{self.court.pk}/surface/ready/").status_code, 409)
        self.assertEqual(self.state(), before)

    def test_confirmation_selects_current_kazan_day_even_near_utc_midnight(self):
        services.close_surface(actor=self.employee, court_id=self.court.pk)
        now = datetime(2026, 7, 1, 21, 30, tzinfo=UTC)
        self.forecast = weather_forecast(self.now, changes={now.astimezone(MOSCOW).replace(hour=1, minute=0): 51})
        with patch("bookings.services.timezone.now", return_value=now):
            result = services.confirm_surface(actor=self.employee, court_id=self.court.pk)
        self.assertTrue(result["warnings"])
        self.court.refresh_from_db()
        self.assertEqual(self.court.surface_status, "available")
        self.assertEqual(self.court.last_inspected_at, now)

    def test_weather_view_has_four_periods_is_read_only_and_skips_indoor_transport(self):
        before = self.state()
        query = {"court_id": self.court.pk, "date": "2026-07-01"}
        response = self.client.get("/api/weather/", query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["blocks"]), 4)
        self.assertIn("no-store", response["Cache-Control"])
        self.api_provider.reset_mock()
        response = self.client.get("/api/weather/", {**query, "court_id": self.indoor.pk})
        self.assertEqual(response.json()["status"], "not_applicable")
        self.api_provider.assert_not_called()
        self.assertEqual(self.state(), before)
        self.assertEqual(self.client.get("/api/weather/", {}).status_code, 400)
        self.assertEqual(self.client.get("/api/weather/", {**query, "court_id": 999999}).status_code, 404)

    def test_actions_require_employee_csrf_methods_and_empty_json(self):
        paths = ["/api/weather/recheck/", f"/api/courts/{self.court.pk}/surface/close/", f"/api/courts/{self.court.pk}/surface/ready/"]
        before = self.state()
        for path in paths:
            for method in ("get", "put", "patch", "delete"):
                self.assertEqual(getattr(self.client, method)(path).status_code, 405)
            for payload in ({"weather_status": "clear"}, {"surface_status": "available"}, [], "null", "{"):
                self.assertEqual(self.post(path, payload).status_code, 400)
        for actor, status in ((None, 401), (self.outsider, 403)):
            self.client.logout()
            if actor:
                self.client.force_login(actor)
            for path in paths:
                self.assertEqual(self.post(path).status_code, status)
            self.assertEqual(self.client.get("/api/weather/", {"court_id": self.court.pk, "date": "2026-07-01"}).status_code, status)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.employee)
        client.get("/")
        for path in paths:
            self.assertEqual(self.post(path, client=client).status_code, 403)
        self.assertEqual(self.state(), before)
        token = client.cookies["csrftoken"].value
        self.assertEqual(self.post(paths[1], client=client, HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertEqual(self.post(paths[2], client=client, HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertEqual(self.post(paths[0], client=client, HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_surface_actions_reject_indoor_missing_and_oversized_ids(self):
        for pk, status in ((self.indoor.pk, 400), (999999, 404), (9223372036854775808, 404)):
            for action in ("close", "ready"):
                self.assertEqual(self.post(f"/api/courts/{pk}/surface/{action}/").status_code, status)

    def test_deadlock_retry_reuses_one_forecast_and_keeps_one_booking_event(self):
        real_event = BookingEvent.objects.create
        calls = []
        def fail_first(**values):
            event = real_event(**values)
            calls.append(event.pk)
            if len(calls) == 1:
                error = OperationalError("Synthetic deadlock")
                error.__cause__ = DeadlockDetected("Synthetic deadlock")
                raise error
            return event
        self.provider.reset_mock()
        with patch("bookings.services.BookingEvent.objects.create", side_effect=fail_first):
            self.create()
        self.assertEqual(self.provider.call_count, 1)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingEvent.objects.count(), 1)

    def test_database_rejects_invalid_weather_status(self):
        booking = self.create()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.filter(pk=booking.pk).update(weather_status="made_up")
        booking.refresh_from_db()
        self.assertEqual(booking.weather_status, "clear")
