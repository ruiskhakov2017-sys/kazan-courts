import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from bookings.models import Booking, BookingEvent, Court, Customer, OutboxMessage, SimulatedNotification
from bookings.weather_transitions import apply_weather_transition


class QueueFixture:
    def setUp(self):
        self.now = timezone.now().replace(minute=0, second=0, microsecond=0)
        self.employee = get_user_model().objects.create_user("queue-employee", is_staff=True)
        self.customer = Customer.objects.create(name="<img src=x onerror=alert(1)>", phone="DEMO")
        self.client.force_login(self.employee)

    def booking(self, start, *, status="active", weather="blocked", court_type="outdoor", surface="available"):
        court = Court.objects.create(name="Synthetic " + str(uuid.uuid4()), court_type=court_type,
                                     latitude=55, longitude=49, surface_status=surface)
        return Booking.objects.create(court=court, customer=self.customer, created_by=self.employee,
                                      starts_at=start, ends_at=start + timedelta(hours=1),
                                      status=status, weather_status=weather)

    def contact(self, booking, token, client=None):
        return (client or self.client).post(f"/api/bookings/{booking.pk}/contact/no-answer/",
                    data=json.dumps({"request_id": str(token)}), content_type="application/json")


class QueueAPITests(QueueFixture, TestCase):
    def test_single_time_boundary_stable_order_and_independent_surface(self):
        urgent = self.booking(self.now + timedelta(hours=72))
        equal = self.booking(self.now + timedelta(hours=72), surface="drying")
        before = self.booking(self.now + timedelta(hours=71, minutes=50))
        later = self.booking(self.now + timedelta(hours=72, minutes=10))
        self.booking(self.now, weather="blocked")
        self.booking(self.now + timedelta(hours=2), weather="clear")
        self.booking(self.now + timedelta(hours=2), status="cancelled")
        self.booking(self.now + timedelta(hours=2), court_type="indoor")
        with patch("bookings.api.timezone", autospec=True) as clock:
            clock.now.return_value = self.now
            response = self.client.get("/api/weather/queue/")
        self.assertEqual(response.status_code, 200)
        clock.now.assert_called_once()
        data = response.json()
        self.assertEqual(data["as_of"], self.now.isoformat())
        self.assertEqual([x["booking"]["id"] for x in data["action"]], [before.pk, urgent.pk, equal.pk])
        self.assertEqual([x["booking"]["id"] for x in data["observation"]], [later.pk])
        with patch("bookings.api.timezone.now", return_value=self.now + timedelta(minutes=10)):
            moved = self.client.get("/api/weather/queue/").json()
        self.assertIn(later.pk, [x["booking"]["id"] for x in moved["action"]])
        self.assertEqual(moved["observation"], [])

    def test_access_and_csrf(self):
        booking = self.booking(self.now + timedelta(hours=2))
        anonymous = Client(enforce_csrf_checks=True)
        self.assertEqual(anonymous.get("/api/weather/queue/").status_code, 401)
        self.assertEqual(anonymous.get(f"/api/bookings/{booking.pk}/history/").status_code, 401)
        outsider = get_user_model().objects.create_user("not-staff")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get("/api/weather/queue/").status_code, 403)
        self.assertEqual(self.client.get(f"/api/bookings/{booking.pk}/history/").status_code, 403)
        self.assertEqual(self.contact(booking, uuid.uuid4()).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.employee)
        self.assertEqual(self.contact(booking, uuid.uuid4(), csrf).status_code, 403)
        self.assertFalse(booking.events.exists())
        csrf.get("/")
        token = csrf.cookies["csrftoken"].value
        response = csrf.post(f"/api/bookings/{booking.pk}/contact/no-answer/",
            data=json.dumps({"request_id": str(uuid.uuid4())}), content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_repeat_same_id_is_idempotent_new_id_is_new_contact(self):
        booking = self.booking(timezone.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=2))
        token = uuid.uuid4()
        first = self.contact(booking, token)
        again = self.contact(booking, token)
        second = self.contact(booking, uuid.uuid4())
        self.assertEqual([first.status_code, again.status_code, second.status_code], [200, 200, 200])
        self.assertEqual(first.json()["event_id"], again.json()["event_id"])
        self.assertNotEqual(first.json()["event_id"], second.json()["event_id"])
        self.assertEqual(booking.events.filter(event_type="contact_no_answer", actor=self.employee).count(), 2)
        self.assertFalse(OutboxMessage.objects.exists())
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.weather_status), ("active", "blocked"))
        self.assertIn(booking.pk, [x["booking"]["id"] for x in self.client.get("/api/weather/queue/").json()["action"]])
        history = self.client.get(f"/api/bookings/{booking.pk}/history/").json()
        self.assertNotIn("contact_request_id", str(history))

    def test_stale_state_and_duplicate_after_state_change(self):
        booking = self.booking(self.now + timedelta(hours=2))
        token = uuid.uuid4()
        first = self.contact(booking, token)
        self.assertEqual(first.status_code, 200)
        Booking.objects.filter(pk=booking.pk).update(weather_status="clear")
        self.assertEqual(self.contact(booking, token).json()["event_id"], first.json()["event_id"])
        self.assertEqual(self.contact(booking, uuid.uuid4()).status_code, 409)
        self.assertEqual(booking.events.count(), 1)
        self.assertNotIn(booking.pk, [x["booking"]["id"] for x in self.client.get("/api/weather/queue/").json()["action"]])

    def test_cancelled_or_already_started_booking_rejects_new_attempt(self):
        cancelled = self.booking(self.now + timedelta(hours=2), status="cancelled")
        started = self.booking(self.now - timedelta(hours=1))
        self.assertEqual(self.contact(cancelled, uuid.uuid4()).status_code, 409)
        self.assertEqual(self.contact(started, uuid.uuid4()).status_code, 409)
        self.assertFalse(BookingEvent.objects.exists())

    def test_other_employee_cannot_reuse_request_id(self):
        booking = self.booking(self.now + timedelta(hours=2))
        token = uuid.uuid4()
        self.assertEqual(self.contact(booking, token).status_code, 200)
        other = get_user_model().objects.create_user("other-employee", is_staff=True)
        self.client.force_login(other)
        self.assertEqual(self.contact(booking, token).status_code, 409)
        self.assertEqual(booking.events.count(), 1)

    def test_improved_weather_removes_queue_but_keeps_contact_history(self):
        booking = self.booking(self.now + timedelta(hours=2))
        self.assertEqual(self.contact(booking, uuid.uuid4()).status_code, 200)
        clear = apply_weather_transition(booking_id=booking.pk, expected_starts_at=booking.starts_at,
            expected_ends_at=booking.ends_at, status="clear", checked_at=timezone.now())
        queue = self.client.get("/api/weather/queue/").json()
        self.assertNotIn(booking.pk, [x["booking"]["id"] for x in queue["action"] + queue["observation"]])
        history = self.client.get(f"/api/bookings/{booking.pk}/history/").json()["events"]
        self.assertEqual(sum(event["event_type"] == "contact_no_answer" for event in history), 1)
        self.assertEqual(clear.event_type, "booking.weather_cleared")
        self.assertEqual(OutboxMessage.objects.count(), 1)

    def test_failed_history_write_leaves_booking_and_outbox_unchanged(self):
        booking = self.booking(self.now + timedelta(hours=2))
        with patch("bookings.weather_transitions.BookingEvent.objects.create", side_effect=IntegrityError("synthetic")):
            response = self.contact(booking, uuid.uuid4())
        self.assertEqual(response.status_code, 503)
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.weather_status), ("active", "blocked"))
        self.assertFalse(booking.events.exists())
        self.assertFalse(OutboxMessage.objects.exists())

    def test_notification_history_distinguishes_result_states(self):
        booking = self.booking(self.now + timedelta(hours=2), weather="clear")
        def transition(status):
            return apply_weather_transition(booking_id=booking.pk, expected_starts_at=booking.starts_at,
                expected_ends_at=booking.ends_at, status=status, checked_at=timezone.now())
        first, second, third = transition("blocked"), transition("clear"), transition("blocked")
        SimulatedNotification.objects.create(event=first, result="simulated", text="synthetic")
        SimulatedNotification.objects.create(event=second, result="suppressed")
        events = self.client.get(f"/api/bookings/{booking.pk}/history/").json()["events"]
        notices = {event["id"]: event["notification"] for event in events}
        self.assertEqual(notices[first.booking_event_id], {"state": "simulated", "text": "synthetic"})
        self.assertEqual(notices[second.booking_event_id]["state"], "suppressed")
        self.assertEqual(notices[third.booking_event_id]["state"], "pending")
        self.assertTrue(all(event["actor_id"] is None for event in events))
        self.assertEqual(booking.events.count(), 3)


class ConcurrentContactTests(QueueFixture, TransactionTestCase):
    def test_parallel_duplicate_requests_create_one_contact(self):
        booking = self.booking(self.now + timedelta(hours=2))
        token = uuid.uuid4()
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                client = Client()
                client.force_login(self.employee)
                barrier.wait(timeout=10)
                response = self.contact(booking, token, client)
                return response.status_code, response.json()["event_id"]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual([r[0] for r in results], [200, 200])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(BookingEvent.objects.filter(booking=booking, event_type="contact_no_answer").count(), 1)
        self.assertFalse(OutboxMessage.objects.exists())

    def test_weather_change_while_waiting_for_lock_rejects_contact(self):
        booking = self.booking(self.now + timedelta(hours=2))
        entered, pids = Event(), []

        def worker():
            close_old_connections()
            try:
                client = Client()
                client.force_login(self.employee)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids.append(cursor.fetchone()[0])
                entered.set()
                return self.contact(booking, uuid.uuid4(), client).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                locked = Booking.objects.select_for_update().get(pk=booking.pk)
                future = pool.submit(worker)
                self.assertTrue(entered.wait(10))
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_stat_clear_snapshot()")
                        cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", pids)
                        state = cursor.fetchone()
                    if state and state[0] == "Lock":
                        break
                    time.sleep(0.05)
                else:
                    self.fail("Contact request did not wait for the booking lock")
                locked.weather_status = "clear"
                locked.save(update_fields=["weather_status"])
            self.assertEqual(future.result(timeout=10), 409)
        self.assertFalse(BookingEvent.objects.filter(booking=booking).exists())
        self.assertFalse(OutboxMessage.objects.exists())
