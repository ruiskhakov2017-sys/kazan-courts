from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, OperationalError
from django.test import Client, TestCase

from bookings.models import Booking, BookingEvent, Court, Customer
from bookings.services import BookingCreationError, booking_snapshot, create_booking

MOSCOW = ZoneInfo("Europe/Moscow")


class BookingCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = get_user_model().objects.create_user("creation-employee", is_staff=True)
        cls.outsider = get_user_model().objects.create_user("creation-outsider")
        cls.court = Court.objects.create(name="Fictional creation court", court_type="indoor", latitude=55, longitude=49)
        cls.other = Court.objects.create(name="Other fictional court", court_type="outdoor", latitude=55, longitude=49)
        cls.customer = Customer.objects.create(name="Fictional creation customer", phone="DEMO-CREATE")
        cls.now = datetime(2026, 7, 1, 12, tzinfo=MOSCOW)
        cls.start = cls.now + timedelta(days=1)

    def setUp(self):
        self.clock = patch("bookings.services.timezone.now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.client.force_login(self.employee)
        self.values = {"actor": self.employee, "customer_id": self.customer.pk, "court_id": self.court.pk,
                       "starts_at": self.start, "ends_at": self.start + timedelta(hours=1)}
        self.payload = {key: value.isoformat() if isinstance(value, datetime) else value
                        for key, value in self.values.items() if key != "actor"}

    def create(self, **changes):
        return create_booking(**{**self.values, **changes})

    def post(self, payload=None, client=None):
        return (client or self.client).post("/api/bookings/", self.payload if payload is None else payload,
                                           content_type="application/json")

    def assert_no_creation(self):
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(BookingEvent.objects.count(), 0)

    def test_service_creates_booking_and_exact_history_without_changing_contacts_or_courts(self):
        before = [list(model.objects.order_by("pk").values()) for model in (Court, Customer)]
        booking = self.create()
        booking.refresh_from_db()
        event = BookingEvent.objects.get(booking=booking)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(event.actor_id, self.employee.pk)
        self.assertEqual(event.event_type, "created")
        self.assertIsNone(event.before)
        self.assertEqual(event.after, booking_snapshot(booking))
        self.assertEqual(event.after["starts_at"], self.start.astimezone(UTC).isoformat())
        self.assertEqual(event.occurred_at, booking.created_at)
        self.assertEqual(event.reason, "")
        self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in (Court, Customer)])

    def test_service_requires_current_employee_permissions(self):
        for actor, status in ((AnonymousUser(), 401), (self.outsider, 403)):
            with self.subTest(actor=type(actor).__name__), self.assertRaises(BookingCreationError) as error:
                self.create(actor=actor)
            self.assertEqual(error.exception.status, status)
        for changes in ({"is_staff": False}, {"is_staff": True, "is_active": False}):
            get_user_model().objects.filter(pk=self.employee.pk).update(**changes)
            with self.assertRaises(BookingCreationError) as error:
                self.create()
            self.assertEqual(error.exception.status, 403)
        self.assert_no_creation()

    def test_service_rejects_missing_references_and_missing_phone(self):
        for changes, code in (({"customer_id": 999999}, "customer_not_found"),
                              ({"court_id": 999999}, "court_not_found")):
            with self.subTest(code=code), self.assertRaises(BookingCreationError) as error:
                self.create(**changes)
            self.assertEqual(error.exception.code, code)
            self.assertEqual(error.exception.status, 404)
        # PostgreSQL already forbids a stored blank phone; exercise the service guard with a transient object.
        self.customer.phone = "   "
        with patch("bookings.services.Customer.objects.get", return_value=self.customer):
            with self.assertRaises(BookingCreationError) as error:
                self.create()
        self.assertIn("customer_id", error.exception.fields)
        self.assert_no_creation()

    def test_invalid_rules_leave_no_booking_or_history(self):
        for changes in (
            {"starts_at": self.now}, {"starts_at": self.start.replace(tzinfo=None)},
            {"ends_at": self.start + timedelta(minutes=50)},
            {"ends_at": self.start + timedelta(hours=1, seconds=1)},
            {"ends_at": datetime(2026, 8, 2, 12, tzinfo=MOSCOW)},
            {"court_id": self.other.pk, "starts_at": datetime(2026, 11, 1, tzinfo=MOSCOW)},
        ):
            with self.subTest(changes=changes), self.assertRaises(BookingCreationError) as error:
                self.create(**changes)
            self.assertEqual(error.exception.status, 400)
            self.assert_no_creation()

    def test_conflicts_are_rejected_and_adjacent_or_other_court_intervals_are_allowed(self):
        first = self.create()
        for start, end in ((self.start, self.start + timedelta(hours=1)),
                           (self.start - timedelta(minutes=30), self.start + timedelta(minutes=30)),
                           (self.start + timedelta(minutes=30), self.start + timedelta(minutes=90)),
                           (self.start - timedelta(hours=1), self.start + timedelta(hours=2))):
            with self.subTest(start=start), self.assertRaises(BookingCreationError) as error:
                self.create(starts_at=start, ends_at=end)
            self.assertEqual(error.exception.code, "time_conflict")
        self.assertEqual(first.events.count(), 1)
        self.create(starts_at=self.start + timedelta(hours=1), ends_at=self.start + timedelta(hours=2))
        self.create(court_id=self.other.pk)
        self.assertEqual(Booking.objects.count(), 3)
        self.assertEqual(BookingEvent.objects.count(), 3)

    def test_cancelled_booking_does_not_occupy_time(self):
        cancelled = self.create()
        Booking.objects.filter(pk=cancelled.pk).update(status="cancelled", archived_at=self.now)
        self.create()
        self.assertEqual(Booking.objects.filter(status="active").count(), 1)
        self.assertEqual(BookingEvent.objects.count(), 2)

    def test_postgresql_conflict_is_mapped_even_after_free_precheck(self):
        self.create()
        with patch("bookings.services._has_active_conflict", return_value=False):
            with self.assertRaises(BookingCreationError) as error:
                self.create()
        self.assertEqual(error.exception.code, "time_conflict")
        self.assertEqual(error.exception.__cause__.__cause__.sqlstate, "23P01")
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingEvent.objects.count(), 1)

    def test_history_integrity_error_rolls_back_booking_and_is_not_mislabeled_as_conflict(self):
        with patch("bookings.services.BookingEvent.objects.create", side_effect=IntegrityError("private history failure")):
            with self.assertRaises(IntegrityError):
                self.create()
        self.assert_no_creation()

    def test_api_success_is_saved_and_visible_in_existing_schedule(self):
        response = self.post()
        self.assertEqual(response.status_code, 201)
        booking = Booking.objects.get(pk=response.json()["booking"]["id"])
        self.assertEqual(response.json()["booking"], {"id": booking.pk, **booking_snapshot(booking)})
        self.assertEqual(booking.created_by_id, self.employee.pk)
        self.assertIn("no-store", response["Cache-Control"])
        rows = self.client.get("/api/schedule/", {"court_id": self.court.pk, "date": "2026-07-02"}).json()["bookings"]
        self.assertEqual([row["id"] for row in rows], [booking.pk])
        self.assertEqual(booking.events.count(), 1)

    def test_api_invalid_payloads_are_controlled_without_writes(self):
        for payload in ([], {}, {**self.payload, "created_by_id": self.outsider.pk},
                        {**self.payload, "status": "cancelled"}, {**self.payload, "court_id": True},
                        {**self.payload, "starts_at": "2026-07-02T12:00:00"}):
            with self.subTest(payload=payload):
                response = self.client.post("/api/bookings/", payload, content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_payload")
        for raw in ("null", "{", '{"court_id": NaN}', b'\xff'):
            with self.subTest(raw=raw):
                self.assertEqual(self.client.post("/api/bookings/", raw, content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post("/api/bookings/", self.payload).status_code, 400)
        self.assert_no_creation()

    def test_api_reports_reference_rule_and_conflict_errors(self):
        for payload, status, code in (
            ({**self.payload, "customer_id": 999999}, 404, "customer_not_found"),
            ({**self.payload, "court_id": 999999}, 404, "court_not_found"),
            ({**self.payload, "starts_at": self.now.isoformat()}, 400, "invalid_booking"),
        ):
            with self.subTest(code=code):
                response = self.post(payload)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["error"]["code"], code)
        self.assert_no_creation()
        self.assertEqual(self.post().status_code, 201)
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "time_conflict")
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingEvent.objects.count(), 1)

    def test_api_requires_employee_and_post_method(self):
        for method in ("get", "put", "patch", "delete", "head", "options"):
            response = getattr(self.client, method)("/api/bookings/")
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Allow"], "POST")
        self.client.logout()
        self.assertEqual(self.post().status_code, 401)
        self.client.force_login(self.outsider)
        self.assertEqual(self.post().status_code, 403)
        self.assert_no_creation()

    def test_api_csrf_failure_creates_nothing_and_valid_token_allows_creation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.employee)
        client.get("/")
        self.assertEqual(self.post(client=client).status_code, 403)
        self.assert_no_creation()
        response = client.post("/api/bookings/", self.payload, content_type="application/json",
                               HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(BookingEvent.objects.count(), 1)

    def test_api_database_failure_returns_generic_error(self):
        with patch("bookings.api.create_booking", side_effect=OperationalError("private database failure")):
            with self.assertLogs("bookings.api", level="ERROR"):
                response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private database failure", response.content.decode())
        self.assert_no_creation()

    def test_api_history_failure_never_reports_success_or_keeps_booking(self):
        with patch("bookings.services.BookingEvent.objects.create", side_effect=IntegrityError("private history failure")):
            with self.assertLogs("bookings.api", level="ERROR"):
                response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private history failure", response.content.decode())
        self.assert_no_creation()
