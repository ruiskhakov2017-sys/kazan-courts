from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from queue import Queue
from threading import Barrier, local
from time import monotonic, sleep
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.test import Client, TransactionTestCase

from bookings import services
from bookings.models import Booking, BookingEvent, Court, Customer


class PostgreSQLBookingActionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.employee = get_user_model().objects.create_user("action-race-employee", is_staff=True)
        self.court = Court.objects.create(name="Fictional action race court", court_type="indoor", latitude=55, longitude=49)
        self.customer = Customer.objects.create(name="Fictional action race customer", phone="DEMO-ACTION-RACE")
        self.now = datetime(2026, 7, 1, 12, tzinfo=ZoneInfo("Europe/Moscow"))
        self.start = self.now + timedelta(days=1)
        with patch("bookings.services.timezone.now", return_value=self.now):
            self.booking = self.new_booking(self.start)
        self.clients = [Client(), Client()]
        for client in self.clients:
            client.force_login(self.employee)

    def new_booking(self, start):
        return services.create_booking(actor=self.employee, customer_id=self.customer.pk, court_id=self.court.pk,
                                       starts_at=start, ends_at=start + timedelta(hours=1))

    def request(self, client, path, payload):
        try:
            response = client.post(path, payload, content_type="application/json")
            return response.status_code, response.json()
        finally:
            connections.close_all()

    def connection_trace(self):
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '10s'")
            cursor.execute("SELECT current_database(), current_user, pg_backend_pid()")
            return cursor.fetchone()

    def assert_traces(self, traces):
        self.assertEqual({row[0] for row in traces}, {"test_kazan_courts"})
        self.assertEqual({row[1] for row in traces}, {"kazan_test"})
        self.assertEqual(len({row[2] for row in traces}), 2)

    def assert_history_chain(self, booking):
        events = list(booking.events.all())
        for previous, current in zip(events, events[1:]):
            self.assertEqual(current.before, previous.after)
        booking.refresh_from_db()
        self.assertEqual(events[-1].after, services.booking_snapshot(booking))

    def race_on_same_booking(self, requests):
        barrier = Barrier(2)
        traces = Queue()
        attempts = local()
        real_lock = services._locked_booking
        def synchronized_lock(booking_id):
            if getattr(attempts, "seen", False):
                return real_lock(booking_id)
            attempts.seen = True
            traces.put(self.connection_trace())
            barrier.wait(timeout=10)
            return real_lock(booking_id)
        with patch("bookings.services.timezone.now", return_value=self.now):
            with patch("bookings.services._locked_booking", side_effect=synchronized_lock):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(self.request, client, path, payload)
                               for client, (path, payload) in zip(self.clients, requests)]
                    results = [future.result(timeout=20) for future in futures]
        rows = [traces.get_nowait(), traces.get_nowait()]
        self.assertTrue(traces.empty())
        self.assert_traces(rows)
        return results, rows

    def test_cancel_and_reschedule_same_booking_preserve_serial_history_repeatedly(self):
        for round_number in range(3):
            start = self.start + timedelta(days=round_number + 1)
            with patch("bookings.services.timezone.now", return_value=self.now):
                booking = self.new_booking(start)
            path = f"/api/bookings/{booking.pk}/"
            payload = {"starts_at": (start + timedelta(hours=2)).isoformat(),
                       "ends_at": (start + timedelta(hours=3)).isoformat()}
            results, traces = self.race_on_same_booking([(path + "reschedule/", payload), (path + "cancel/", {})])
            self.assertEqual(results[1][0], 200)
            self.assertIn(results[0][0], (200, 409))
            if results[0][0] == 409:
                self.assertEqual(results[0][1]["error"]["code"], "invalid_booking_state")
            booking.refresh_from_db()
            self.assertEqual(booking.status, "cancelled")
            self.assertEqual(booking.events.count(), 2 + (results[0][0] == 200))
            self.assert_history_chain(booking)
            print(f"Same-booking round {round_number + 1}: test_kazan_courts/kazan_test; "
                  f"PIDs={sorted(row[2] for row in traces)}; HTTP={[result[0] for result in results]}; serial history", flush=True)

    def test_duplicate_cancel_and_archive_record_each_change_once(self):
        path = f"/api/bookings/{self.booking.pk}/"
        for action in ("cancel", "archive"):
            results, traces = self.race_on_same_booking([(path + action + "/", {}), (path + action + "/", {})])
            self.assertEqual(sorted(status for status, body in results), [200, 409])
            self.assertEqual(self.booking.events.filter(event_type={"cancel": "cancelled", "archive": "archived"}[action]).count(), 1)
            self.assert_history_chain(self.booking)
            print(f"Duplicate {action}: PIDs={sorted(row[2] for row in traces)}; HTTP 200/409; one event", flush=True)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(BookingEvent.objects.count(), 3)

    def test_create_and_reschedule_compete_for_one_interval_repeatedly(self):
        for round_number in range(3):
            destination = self.start + timedelta(days=round_number + 2)
            self.booking.refresh_from_db()
            before = services.booking_snapshot(self.booking)
            event_count = BookingEvent.objects.count()
            barrier = Barrier(2)
            observations = Queue()
            attempts = local()
            real_check = services._has_active_conflict
            def synchronized_check(**values):
                if getattr(attempts, "seen", False):
                    return real_check(**values)
                attempts.seen = True
                free = not real_check(**values)
                observations.put((*self.connection_trace(), free))
                barrier.wait(timeout=10)
                return not free
            times = {"starts_at": destination.isoformat(), "ends_at": (destination + timedelta(hours=1)).isoformat()}
            requests = [(f"/api/bookings/{self.booking.pk}/reschedule/", times),
                        ("/api/bookings/", {**times, "customer_id": self.customer.pk, "court_id": self.court.pk})]
            with patch("bookings.services.timezone.now", return_value=self.now):
                with patch("bookings.services._has_active_conflict", side_effect=synchronized_check):
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        futures = [executor.submit(self.request, client, path, payload)
                                   for client, (path, payload) in zip(self.clients, requests)]
                        results = [future.result(timeout=20) for future in futures]
            traces = [observations.get_nowait(), observations.get_nowait()]
            self.assert_traces(traces)
            self.assertEqual([row[3] for row in traces], [True, True])
            self.assertEqual(sum(status in (200, 201) for status, body in results), 1)
            loser = next(body for status, body in results if status == 409)
            self.assertEqual(loser["error"]["code"], "time_conflict")
            self.assertEqual(Booking.objects.filter(court=self.court, starts_at=destination, status="active").count(), 1)
            self.assertEqual(BookingEvent.objects.count(), event_count + 1)
            if results[0][0] == 409:
                self.booking.refresh_from_db()
                self.assertEqual(services.booking_snapshot(self.booking), before)
            self.assert_history_chain(self.booking)
            print(f"Create/reschedule round {round_number + 1}: test_kazan_courts/kazan_test; "
                  f"PIDs={sorted(row[2] for row in traces)}; both prechecks free; HTTP={[result[0] for result in results]}; one winner/event", flush=True)

    def test_server_time_is_checked_after_waiting_for_real_row_lock(self):
        traces = Queue()
        real_lock = services._locked_booking
        def observed_lock(booking_id):
            traces.put(self.connection_trace())
            return real_lock(booking_id)
        with patch("bookings.services.timezone.now", return_value=self.now) as clock:
            with patch("bookings.services._locked_booking", side_effect=observed_lock):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    with transaction.atomic():
                        Booking.objects.select_for_update().get(pk=self.booking.pk)
                        future = executor.submit(self.request, self.clients[0], f"/api/bookings/{self.booking.pk}/cancel/", {})
                        database, user, pid = traces.get(timeout=5)
                        self.assertEqual((database, user), ("test_kazan_courts", "kazan_test"))
                        deadline = monotonic() + 3
                        waiting = False
                        while monotonic() < deadline:
                            with connection.cursor() as cursor:
                                cursor.execute("SELECT pg_stat_clear_snapshot()")
                                cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid])
                                waiting = cursor.fetchone() == ("Lock",)
                            if waiting:
                                break
                            sleep(0.01)
                        self.assertTrue(waiting, "Worker did not reach the actual PostgreSQL lock wait")
                        clock.return_value = self.start
                    status, body = future.result(timeout=10)
        self.assertEqual((status, body["error"]["code"]), (409, "booking_already_started"))
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "active")
        self.assertEqual(self.booking.events.count(), 1)
        print(f"Post-lock time check: test_kazan_courts/kazan_test; PID={pid}; real Lock wait; HTTP 409; unchanged booking/history", flush=True)
