from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import TestCase

from bookings.models import Booking, BookingEvent, Court, Customer

MOSCOW = ZoneInfo("Europe/Moscow")


class EmployeeReadAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = get_user_model().objects.create_user(username="employee", is_staff=True)
        cls.court = Court.objects.create(
            name="A synthetic court", court_type="indoor", latitude="55.790000", longitude="49.120000"
        )
        cls.other_court = Court.objects.create(
            name="B synthetic court", court_type="outdoor", latitude="55.791000", longitude="49.121000",
            surface_status="drying",
        )
        cls.customer = Customer.objects.create(name="<script>fictional</script>", phone="DEMO-API")

    def setUp(self):
        self.client.force_login(self.employee)
        self.query = {"court_id": self.court.pk, "date": "2026-07-01"}

    def booking(self, start, **changes):
        values = {
            "court": self.court, "customer": self.customer, "created_by": self.employee,
            "starts_at": start, "ends_at": start + timedelta(hours=1),
        }
        return Booking.objects.create(**{**values, **changes})

    def test_anonymous_cannot_read_any_endpoint(self):
        self.client.logout()
        for path in ("/api/courts/", "/api/customers/", "/api/schedule/"):
            with self.subTest(path=path):
                response = self.client.get(path, self.query)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["error"]["code"], "authentication_required")
                self.assertIn("no-store", response["Cache-Control"])

    def test_non_employee_cannot_read_any_endpoint(self):
        outsider = get_user_model().objects.create_user(username="outsider")
        self.client.force_login(outsider)
        for path in ("/api/courts/", "/api/customers/", "/api/schedule/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, self.query).status_code, 403)

    def test_employee_access_is_rechecked_after_staff_flag_changes(self):
        get_user_model().objects.filter(pk=self.employee.pk).update(is_staff=False)
        self.assertEqual(self.client.get("/api/customers/").status_code, 403)

    def test_courts_are_read_from_database_with_explicit_fields(self):
        response = self.client.get("/api/courts/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        rows = response.json()["courts"]
        self.assertEqual([row["id"] for row in rows], [self.court.pk, self.other_court.pk])
        self.assertEqual(rows[0]["latitude"], "55.790000")
        self.assertEqual(rows[1]["surface_status"], "drying")
        self.assertEqual(set(rows[0]), {
            "id", "name", "court_type", "latitude", "longitude", "surface_status", "last_inspected_at",
        })
        self.assertIn("no-store", response["Cache-Control"])

    def test_customers_return_only_contact_fields(self):
        response = self.client.get("/api/customers/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["customers"], [{
            "id": self.customer.pk, "name": self.customer.name, "phone": "DEMO-API", "email": "",
        }])

    def test_empty_schedule_is_successful(self):
        response = self.client.get("/api/schedule/", self.query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "court_id": self.court.pk, "date": "2026-07-01", "time_zone": "Europe/Moscow", "bookings": [],
        })

    def test_schedule_includes_intervals_crossing_midnight(self):
        previous = self.booking(datetime(2026, 6, 30, 23, 30, tzinfo=MOSCOW))
        late = self.booking(datetime(2026, 7, 1, 23, 30, tzinfo=MOSCOW))
        rows = self.client.get("/api/schedule/", self.query).json()["bookings"]
        self.assertEqual([row["id"] for row in rows], [previous.pk, late.pk])
        self.assertEqual(datetime.fromisoformat(rows[0]["starts_at"]), previous.starts_at)
        self.assertEqual(set(rows[0]), {"id", "court_id", "customer_id", "starts_at", "ends_at", "status"})

    def test_day_boundaries_are_half_open_in_moscow_not_utc(self):
        self.booking(datetime(2026, 6, 30, 23, tzinfo=MOSCOW))
        first = self.booking(datetime(2026, 7, 1, 0, tzinfo=MOSCOW))
        self.booking(datetime(2026, 7, 2, 0, tzinfo=MOSCOW))
        rows = self.client.get("/api/schedule/", self.query).json()["bookings"]
        self.assertEqual([row["id"] for row in rows], [first.pk])

    def test_schedule_excludes_cancelled_archived_and_other_courts(self):
        start = datetime(2026, 7, 1, 12, tzinfo=MOSCOW)
        active = self.booking(start)
        self.booking(start, status="cancelled")
        self.booking(start, status="cancelled", archived_at=start)
        self.booking(start, court=self.other_court)
        rows = self.client.get("/api/schedule/", self.query).json()["bookings"]
        self.assertEqual([row["id"] for row in rows], [active.pk])

    def test_invalid_parameters_have_controlled_json_errors(self):
        cases = (
            {}, {"date": "2026-07-01"}, {"court_id": self.court.pk},
            {"court_id": 0, "date": "2026-07-01"}, {"court_id": "abc", "date": "2026-07-01"},
            {"court_id": 9223372036854775808, "date": "2026-07-01"},
            *({"court_id": self.court.pk, "date": value} for value in (
                "2026-02-30", "01.07.2026", "2026-7-1", "20260701", "2026-W27-3", "9999-12-31",
            )),
        )
        for query in cases:
            with self.subTest(query=query):
                response = self.client.get("/api/schedule/", query)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_query")

    def test_unknown_court_is_not_confused_with_empty_schedule(self):
        response = self.client.get("/api/schedule/", {**self.query, "court_id": 999999})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "court_not_found")

    def test_endpoints_reject_writes(self):
        for path in ("/api/courts/", "/api/customers/", "/api/schedule/"):
            for method in ("post", "put", "patch", "delete"):
                with self.subTest(path=path, method=method):
                    response = getattr(self.client, method)(path, self.query)
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response["Allow"], "GET")

    def test_read_requests_do_not_change_domain_data(self):
        booking = self.booking(datetime(2026, 7, 1, 12, tzinfo=MOSCOW))
        BookingEvent.objects.create(booking=booking, actor=self.employee, event_type="created", after={"status": "active"})
        def snapshot():
            return [list(model.objects.order_by("pk").values()) for model in (Court, Customer, Booking, BookingEvent)]
        before = snapshot()
        for path in ("/api/courts/", "/api/customers/", "/api/schedule/"):
            self.assertEqual(self.client.get(path, self.query).status_code, 200)
        self.assertEqual(snapshot(), before)

    def test_database_failure_does_not_expose_internal_details(self):
        with patch("bookings.api.Court.objects.order_by", side_effect=OperationalError("private database detail")):
            with self.assertLogs("bookings.api", level="ERROR"):
                response = self.client.get("/api/courts/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "database_unavailable")
        self.assertNotIn("private database detail", response.content.decode())
