from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, OperationalError
from django.test import Client, TestCase
from psycopg.errors import DeadlockDetected, LockNotAvailable, SerializationFailure

from bookings import services
from bookings.models import Booking, BookingEvent, Court, Customer
from bookings.services import (
    BookingOperationError, archive_booking, booking_snapshot, cancel_booking,
    create_booking, reschedule_booking,
)

MOSCOW = ZoneInfo("Europe/Moscow")


class BookingActionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.creator = get_user_model().objects.create_user("action-creator", is_staff=True)
        cls.employee = get_user_model().objects.create_user("action-employee", is_staff=True)
        cls.outsider = get_user_model().objects.create_user("action-outsider")
        cls.court = Court.objects.create(name="Fictional action court", court_type="indoor", latitude=55, longitude=49)
        cls.other_court = Court.objects.create(name="Other fictional action court", court_type="outdoor", latitude=55, longitude=49)
        cls.customer = Customer.objects.create(name="Fictional action customer", phone="DEMO-ACTION")
        cls.now = datetime(2026, 7, 1, 12, tzinfo=MOSCOW)

    def setUp(self):
        self.clock = patch("bookings.services.timezone.now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.client.force_login(self.employee)
        self.start = self.now + timedelta(days=1)
        self.booking = self.new_booking(self.start)
        self.times = {"starts_at": self.start + timedelta(hours=2), "ends_at": self.start + timedelta(hours=3)}
        self.payload = {name: value.isoformat() for name, value in self.times.items()}

    def new_booking(self, start, **changes):
        values = {"actor": self.creator, "court_id": self.court.pk, "customer_id": self.customer.pk,
                  "starts_at": start, "ends_at": start + timedelta(hours=1)}
        return create_booking(**{**values, **changes})

    def action(self, operation, **values):
        return operation(actor=self.employee, booking_id=self.booking.pk, **values)

    def post(self, action, payload=None, client=None, **options):
        return (client or self.client).post(f"/api/bookings/{self.booking.pk}/{action}/",
                                           {} if payload is None else payload, content_type="application/json", **options)

    def domain_state(self):
        return [list(model.objects.order_by("pk").values()) for model in (Court, Customer, Booking, BookingEvent)]

    def database_error(self, cause):
        error = OperationalError("Synthetic database failure")
        error.__cause__ = cause
        return error

    def test_deadlock_retries_whole_operation_after_rolling_back_booking_and_event(self):
        cases = (
            (create_booking, {"actor": self.employee, "customer_id": self.customer.pk, "court_id": self.court.pk,
                              "starts_at": self.start + timedelta(days=2), "ends_at": self.start + timedelta(days=2, hours=1)}),
            (reschedule_booking, {"actor": self.employee, "booking_id": self.booking.pk, **self.times}),
            (cancel_booking, {"actor": self.employee, "booking_id": self.booking.pk}),
            (archive_booking, {"actor": self.employee, "booking_id": self.booking.pk}),
        )
        for operation, values in cases:
            with self.subTest(operation=operation.__name__):
                before = self.domain_state()
                original = None if operation is create_booking else booking_snapshot(Booking.objects.get(pk=self.booking.pk))
                states = []
                real_employee = services._employee
                real_event = BookingEvent.objects.create
                def observe_employee(actor):
                    states.append(self.domain_state())
                    return real_employee(actor)
                def fail_first_event(**event_values):
                    event = real_event(**event_values)
                    if len(states) == 1:
                        raise self.database_error(DeadlockDetected("Synthetic deadlock"))
                    return event
                with patch("bookings.services._employee", side_effect=observe_employee):
                    with patch("bookings.services.BookingEvent.objects.create", side_effect=fail_first_event) as write:
                        result = operation(**values)
                self.assertEqual(write.call_count, 2)
                self.assertEqual(states, [before, before])
                self.assertEqual(Booking.objects.count(), len(before[2]) + (operation is create_booking))
                self.assertEqual(BookingEvent.objects.count(), len(before[3]) + 1)
                event = result.events.order_by("pk").last()
                self.assertEqual(event.before, original)
                self.assertEqual(event.after, booking_snapshot(result))
                self.assertEqual(event.occurred_at, result.updated_at)
                self.assertEqual(event.actor_id, self.employee.pk)

    def test_deadlock_retry_rechecks_time_instead_of_cancelling_started_booking(self):
        before = self.domain_state()
        real_event = BookingEvent.objects.create
        with patch("bookings.services.timezone.now", return_value=self.now) as clock:
            def fail_event(**values):
                real_event(**values)
                clock.return_value = self.start
                raise self.database_error(DeadlockDetected("Synthetic deadlock"))
            with patch("bookings.services.BookingEvent.objects.create", side_effect=fail_event) as write:
                response = self.post("cancel")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "booking_already_started"))
        self.assertEqual(write.call_count, 1)
        self.assertEqual(self.domain_state(), before)

    def test_second_deadlock_returns_503_without_more_retries_or_partial_writes(self):
        create_payload = {"customer_id": self.customer.pk, "court_id": self.court.pk,
                          "starts_at": (self.start + timedelta(days=2)).isoformat(),
                          "ends_at": (self.start + timedelta(days=2, hours=1)).isoformat()}
        cases = [("/api/bookings/", create_payload)]
        cases += [(f"/api/bookings/{self.booking.pk}/{action}/", payload)
                  for action, payload in (("reschedule", self.payload), ("cancel", {}), ("archive", {}))]
        for path, payload in cases:
            with self.subTest(path=path):
                if path.endswith("/archive/"):
                    self.action(cancel_booking)
                before = self.domain_state()
                real_event = BookingEvent.objects.create
                def fail_event(**values):
                    real_event(**values)
                    raise self.database_error(DeadlockDetected("Synthetic deadlock"))
                with patch("bookings.services.BookingEvent.objects.create", side_effect=fail_event) as write:
                    with self.assertLogs("bookings.api", level="ERROR"):
                        response = self.client.post(path, payload, content_type="application/json")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], "database_unavailable")
                self.assertNotIn("Synthetic database failure", response.content.decode())
                self.assertEqual(write.call_count, 2)
                self.assertEqual(self.domain_state(), before)

    def test_other_operational_errors_are_not_retried(self):
        before = self.domain_state()
        real_event = BookingEvent.objects.create
        for cause in (LockNotAvailable("Synthetic lock failure"), SerializationFailure("Synthetic serialization failure"), None):
            with self.subTest(cause=type(cause).__name__):
                def fail_event(**values):
                    real_event(**values)
                    raise self.database_error(cause)
                with patch("bookings.services.BookingEvent.objects.create", side_effect=fail_event) as write:
                    with self.assertLogs("bookings.api", level="ERROR"):
                        response = self.post("cancel")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(write.call_count, 1)
                self.assertEqual(self.domain_state(), before)

    def test_full_history_chain_preserves_identity_original_author_and_old_events(self):
        first = self.booking.events.get()
        original = booking_snapshot(self.booking)
        self.action(reschedule_booking, **self.times, reason="  Fictional move  ")
        self.action(cancel_booking, reason="Fictional cancellation")
        self.action(archive_booking, reason="Fictional archive")
        self.booking.refresh_from_db()
        events = list(self.booking.events.all())
        self.assertEqual([event.event_type for event in events], ["created", "rescheduled", "cancelled", "archived"])
        self.assertEqual(events[0].after, original)
        first.refresh_from_db()
        self.assertIsNone(first.before)
        self.assertEqual(first.after, original)
        for previous, current in zip(events, events[1:]):
            self.assertEqual(current.before, previous.after)
            self.assertEqual(current.actor_id, self.employee.pk)
        self.assertEqual(events[-1].after, booking_snapshot(self.booking))
        self.assertEqual([event.reason for event in events[1:]], ["Fictional move", "Fictional cancellation", "Fictional archive"])
        self.assertEqual(self.booking.created_by_id, self.creator.pk)
        self.assertEqual(self.booking.created_at, first.occurred_at)
        self.assertEqual((self.booking.court_id, self.booking.customer_id), (self.court.pk, self.customer.pk))
        self.assertEqual(self.booking.cancellation_reason, "Fictional cancellation")
        self.assertEqual(self.booking.archived_at, self.now)
        self.assertEqual(Booking.objects.count(), 1)

    def test_reschedule_can_overlap_its_own_previous_interval(self):
        self.action(reschedule_booking, starts_at=self.start + timedelta(minutes=30), ends_at=self.start + timedelta(minutes=90))
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.starts_at, self.start + timedelta(minutes=30))
        self.assertEqual(self.booking.events.count(), 2)

    def test_conflicting_reschedule_and_database_rejection_preserve_all_data(self):
        self.new_booking(self.times["starts_at"])
        before = self.domain_state()
        with self.assertRaises(BookingOperationError) as error:
            self.action(reschedule_booking, **self.times)
        self.assertEqual(error.exception.code, "time_conflict")
        self.assertEqual(self.domain_state(), before)
        with patch("bookings.services._has_active_conflict", return_value=False):
            with self.assertRaises(BookingOperationError) as error:
                self.action(reschedule_booking, **self.times)
        self.assertEqual(error.exception.code, "time_conflict")
        self.assertEqual(error.exception.__cause__.__cause__.sqlstate, "23P01")
        self.assertEqual(self.domain_state(), before)

    def test_new_time_rules_are_rechecked_without_rounding_or_writes(self):
        before = self.domain_state()
        for changes in (
            {"starts_at": self.now}, {"starts_at": self.start.replace(tzinfo=None)},
            {"ends_at": self.times["starts_at"] + timedelta(minutes=50)},
            {"starts_at": self.times["starts_at"] + timedelta(minutes=1)},
            {"ends_at": self.times["ends_at"] + timedelta(seconds=1)},
            {"ends_at": datetime(2026, 8, 2, 12, tzinfo=MOSCOW)},
        ):
            with self.subTest(changes=changes), self.assertRaises(BookingOperationError) as error:
                self.action(reschedule_booking, **{**self.times, **changes})
            self.assertEqual(error.exception.code, "invalid_booking")
            self.assertEqual(self.domain_state(), before)

    def test_reschedule_reuses_month_and_outdoor_season_boundaries(self):
        with patch("bookings.services.timezone.now", return_value=datetime(2026, 10, 15, 12, tzinfo=MOSCOW)):
            target = self.new_booking(datetime(2026, 10, 16, 12, tzinfo=MOSCOW), court_id=self.other_court.pk)
            november = datetime(2026, 11, 1, tzinfo=MOSCOW)
            before = self.domain_state()
            with self.assertRaises(BookingOperationError):
                reschedule_booking(actor=self.employee, booking_id=target.pk,
                                   starts_at=november - timedelta(minutes=50), ends_at=november + timedelta(minutes=10))
            self.assertEqual(self.domain_state(), before)
            reschedule_booking(actor=self.employee, booking_id=target.pk,
                               starts_at=november - timedelta(hours=1), ends_at=november)
            target.refresh_from_db()
            self.assertEqual(target.ends_at, november)

    def test_current_phone_is_checked_on_reschedule(self):
        before = self.domain_state()
        self.customer.phone = "   "
        with patch("bookings.services.Customer.objects.get", return_value=self.customer):
            with self.assertRaises(BookingOperationError) as error:
                self.action(reschedule_booking, **self.times)
        self.assertIn("customer_id", error.exception.fields)
        self.assertEqual(self.domain_state(), before)

    def test_cancel_frees_time_and_does_not_change_original_interval(self):
        original = booking_snapshot(self.booking)
        self.action(cancel_booking)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "cancelled")
        self.assertEqual((self.booking.starts_at, self.booking.ends_at), (self.start, self.start + timedelta(hours=1)))
        self.assertIsNone(self.booking.archived_at)
        self.assertEqual(self.booking.cancellation_reason, "")
        event = self.booking.events.get(event_type="cancelled")
        self.assertEqual(event.before, original)
        replacement = self.new_booking(self.start)
        self.assertNotEqual(replacement.pk, self.booking.pk)
        self.assertEqual(Booking.objects.count(), 2)

    def test_cancel_and_reschedule_reject_started_or_past_booking(self):
        before = self.domain_state()
        for now in (self.start, self.start + timedelta(minutes=10)):
            with patch("bookings.services.timezone.now", return_value=now):
                for operation, values in ((cancel_booking, {}), (reschedule_booking, self.times)):
                    with self.subTest(now=now, operation=operation.__name__), self.assertRaises(BookingOperationError) as error:
                        self.action(operation, **values)
                    self.assertEqual(error.exception.code, "booking_already_started")
                    self.assertEqual(error.exception.status, 409)
                    self.assertEqual(self.domain_state(), before)

    def test_only_cancelled_bookings_can_be_archived_including_past_bookings(self):
        before = self.domain_state()
        with self.assertRaises(BookingOperationError) as error:
            self.action(archive_booking)
        self.assertEqual(error.exception.code, "invalid_booking_state")
        self.assertEqual(self.domain_state(), before)
        self.action(cancel_booking, reason="Preserved reason")
        with patch("bookings.services.timezone.now", return_value=self.start + timedelta(days=1)):
            self.action(archive_booking)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "cancelled")
        self.assertEqual(self.booking.cancellation_reason, "Preserved reason")
        self.assertEqual(self.booking.archived_at, self.start + timedelta(days=1))
        self.assertEqual(self.booking.events.count(), 3)

    def test_repeat_actions_and_same_time_do_not_duplicate_events(self):
        before = self.domain_state()
        with self.assertRaises(BookingOperationError) as error:
            self.action(reschedule_booking, starts_at=self.start, ends_at=self.start + timedelta(hours=1))
        self.assertEqual((error.exception.code, error.exception.status), ("no_change", 400))
        self.assertEqual(self.domain_state(), before)
        self.action(cancel_booking)
        for archived in (False, True):
            if archived:
                self.action(archive_booking)
            before = self.domain_state()
            actions = [(cancel_booking, {}), (reschedule_booking, self.times)]
            if archived:
                actions.append((archive_booking, {}))
            for operation, values in actions:
                with self.subTest(archived=archived, operation=operation.__name__), self.assertRaises(BookingOperationError) as error:
                    self.action(operation, **values)
                self.assertEqual(error.exception.status, 409)
                self.assertEqual(self.domain_state(), before)

    def test_missing_booking_and_revoked_employee_are_rejected(self):
        before = self.domain_state()
        operations = ((reschedule_booking, self.times), (cancel_booking, {}), (archive_booking, {}))
        for operation, values in operations:
            for booking_id in (0, 999999, 9223372036854775808):
                with self.subTest(operation=operation.__name__, booking_id=booking_id), self.assertRaises(BookingOperationError) as error:
                    operation(actor=self.employee, booking_id=booking_id, **values)
                self.assertEqual(error.exception.status, 404)
            for actor, status in ((AnonymousUser(), 401), (self.outsider, 403)):
                with self.assertRaises(BookingOperationError) as error:
                    operation(actor=actor, booking_id=self.booking.pk, **values)
                self.assertEqual(error.exception.status, status)
        for changes in ({"is_staff": False}, {"is_staff": True, "is_active": False}):
            get_user_model().objects.filter(pk=self.employee.pk).update(**changes)
            for operation, values in operations:
                with self.assertRaises(BookingOperationError) as error:
                    self.action(operation, **values)
                self.assertEqual(error.exception.status, 403)
        self.assertEqual(self.domain_state(), before)

    def test_history_failure_rolls_back_each_change_and_returns_no_false_success(self):
        for action, payload in (("reschedule", self.payload), ("cancel", {}), ("archive", {})):
            if action == "archive":
                self.action(cancel_booking)
            before = self.domain_state()
            with patch("bookings.services.BookingEvent.objects.create", side_effect=IntegrityError("private event failure")):
                with self.assertLogs("bookings.api", level="ERROR"):
                    response = self.post(action, payload)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "database_unavailable")
            self.assertNotIn("private event failure", response.content.decode())
            self.assertEqual(self.domain_state(), before)

    def test_api_list_archive_history_and_schedule_are_consistent(self):
        query = {"court_id": self.court.pk, "date": "2026-07-02"}
        def ids(path):
            return [row["id"] for row in self.client.get(path, query).json()["bookings"]]
        self.assertEqual(ids("/api/bookings/"), [self.booking.pk])
        response = self.post("reschedule", {**self.payload, "reason": "Fictional move"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response.json()["booking"]["created_by_id"], self.creator.pk)
        self.assertEqual(self.post("cancel").status_code, 200)
        self.assertEqual(ids("/api/schedule/"), [])
        self.assertEqual(ids("/api/bookings/"), [self.booking.pk])
        self.assertEqual(self.post("archive").status_code, 200)
        self.assertEqual(ids("/api/bookings/"), [])
        response = self.client.get(f"/api/bookings/{self.booking.pk}/history/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        events = response.json()["events"]
        self.assertEqual([event["event_type"] for event in events], ["created", "rescheduled", "cancelled", "archived"])
        self.assertEqual(events[1]["reason"], "Fictional move")
        for previous, current in zip(events, events[1:]):
            self.assertEqual(current["before"], previous["after"])
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingEvent.objects.count(), 4)

    def test_list_filters_court_day_and_archive_without_read_side_effects(self):
        other = self.new_booking(self.start, court_id=self.other_court.pk)
        self.new_booking(self.start + timedelta(days=1))
        before = self.domain_state()
        query = {"court_id": self.court.pk, "date": "2026-07-02"}
        response = self.client.get("/api/bookings/", query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()["bookings"]], [self.booking.pk])
        self.assertEqual(response.json()["time_zone"], "Europe/Moscow")
        self.assertEqual(self.client.get("/api/bookings/", {**query, "date": "2026-07-04"}).json()["bookings"], [])
        for values in ({}, {"court_id": 0, "date": "bad"}, {**query, "date": "9999-12-31"}):
            self.assertEqual(self.client.get("/api/bookings/", values).status_code, 400)
        self.assertEqual(self.client.get("/api/bookings/", {**query, "court_id": 999999}).status_code, 404)
        self.assertEqual(self.client.get(f"/api/bookings/{other.pk}/history/").status_code, 200)
        self.assertEqual(self.client.get("/api/bookings/999999/history/").status_code, 404)
        self.assertEqual(self.client.get("/api/bookings/9223372036854775808/history/").status_code, 404)
        self.assertEqual(self.domain_state(), before)

    def test_api_requires_employee_and_supports_no_delete_method(self):
        paths = [f"/api/bookings/{self.booking.pk}/{action}/" for action in ("reschedule", "cancel", "archive")]
        before = self.domain_state()
        for path in paths:
            for method in ("get", "put", "patch", "delete", "head", "options"):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response["Allow"], "POST")
        history = f"/api/bookings/{self.booking.pk}/history/"
        self.assertEqual(self.client.delete(history).status_code, 405)
        self.assertEqual(self.client.delete("/api/bookings/").status_code, 405)
        for actor, status in ((None, 401), (self.outsider, 403)):
            self.client.logout()
            if actor is not None:
                self.client.force_login(actor)
            for path in paths:
                self.assertEqual(self.client.post(path, {}, content_type="application/json").status_code, status)
            self.assertEqual(self.client.get(history).status_code, status)
            self.assertEqual(self.client.get("/api/bookings/").status_code, status)
        self.assertEqual(self.domain_state(), before)

    def test_csrf_is_required_for_all_actions(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.employee)
        client.get("/")
        for action, payload in (("reschedule", self.payload), ("cancel", {}), ("archive", {})):
            before = self.domain_state()
            self.assertEqual(self.post(action, payload, client=client).status_code, 403)
            self.assertEqual(self.domain_state(), before)
            response = self.post(action, payload, client=client, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
            self.assertEqual(response.status_code, 200)

    def test_invalid_action_payloads_cannot_change_server_owned_fields(self):
        before = self.domain_state()
        for action, valid in (("reschedule", self.payload), ("cancel", {}), ("archive", {})):
            for payload in ([], {**valid, "court_id": self.other_court.pk}, {**valid, "customer_id": 99},
                            {**valid, "created_by_id": self.employee.pk}, {**valid, "status": "active"},
                            {**valid, "archived_at": self.now.isoformat()}, {**valid, "reason": None},
                            {**valid, "reason": []}, {**valid, "reason": 12}):
                with self.subTest(action=action, payload=payload):
                    response = self.post(action, payload)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json()["error"]["code"], "invalid_payload")
            for raw in ("null", "{", '{"reason": NaN}', b'\xff'):
                self.assertEqual(self.post(action, raw).status_code, 400)
            self.assertEqual(self.client.post(f"/api/bookings/{self.booking.pk}/{action}/", {}).status_code, 400)
        for payload in ({}, {**self.payload, "starts_at": "2026-07-02T14:00:00"},
                        {**self.payload, "ends_at": "2026-07-02T15:00:00+02:60"}):
            self.assertEqual(self.post("reschedule", payload).status_code, 400)
        self.assertEqual(self.domain_state(), before)

    def test_api_errors_have_correct_codes_and_preserve_data(self):
        before = self.domain_state()
        response = self.post("reschedule", {"starts_at": self.start.isoformat(), "ends_at": (self.start + timedelta(hours=1)).isoformat()})
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "no_change"))
        response = self.post("archive")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "invalid_booking_state"))
        self.assertEqual(self.client.post("/api/bookings/999999/cancel/", {}, content_type="application/json").status_code, 404)
        with patch("bookings.services._locked_booking", side_effect=OperationalError("private db failure")):
            with self.assertLogs("bookings.api", level="ERROR"):
                response = self.post("cancel")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private db failure", response.content.decode())
        self.assertEqual(self.domain_state(), before)
