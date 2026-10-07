from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from queue import Queue
from threading import Barrier, Event
from time import monotonic, sleep
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction
from django.test import Client, TransactionTestCase

from bookings import services
from bookings.models import Booking, BookingEvent, Court, Customer
from bookings.weather_policy import MOSCOW


class CourtWeatherConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 1, 9, tzinfo=MOSCOW)
        self.start = (self.now + timedelta(days=1)).replace(hour=14)
        self.employee = get_user_model().objects.create_user("court-race-employee", is_staff=True)
        self.court = Court.objects.create(name="Fictional Court race", court_type="outdoor", latitude=55, longitude=49)
        self.customer = Customer.objects.create(name="Fictional Court race customer", phone="DEMO-COURT-RACE")
        self.clients = [Client(), Client()]
        for client in self.clients:
            client.force_login(self.employee)

    def payload(self, start):
        return {"court_id": self.court.pk, "customer_id": self.customer.pk,
                "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat()}

    def request(self, client, path, payload):
        try:
            response = client.post(path, payload, content_type="application/json")
            return response.status_code, response.json()
        finally:
            connections.close_all()

    def trace(self):
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '10s'")
            cursor.execute("SELECT current_database(), current_user, pg_backend_pid()")
            row = cursor.fetchone()
        self.assertEqual(row[:2], ("test_kazan_courts", "kazan_test"))
        return row[2]

    def wait_for_lock(self, pid):
        deadline = monotonic() + 3
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_stat_clear_snapshot()")
                cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid])
                if cursor.fetchone() == ("Lock",):
                    return
            sleep(0.01)
        self.fail("Worker did not reach the real PostgreSQL lock wait")

    def test_two_bookings_hold_shared_court_locks_simultaneously(self):
        barrier, traces = Barrier(2), Queue()
        real_check = services._has_active_conflict
        def synchronize(**values):
            occupied = real_check(**values)
            traces.put(self.trace())
            barrier.wait(timeout=10)
            return occupied
        with patch("bookings.services.timezone.now", return_value=self.now):
            with patch("bookings.services._has_active_conflict", side_effect=synchronize):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(self.request, client, "/api/bookings/", self.payload(self.start + timedelta(hours=number * 2)))
                               for number, client in enumerate(self.clients)]
                    results = [future.result(timeout=20) for future in futures]
        pids = [traces.get_nowait(), traces.get_nowait()]
        self.assertEqual(len(set(pids)), 2)
        self.assertEqual([row[0] for row in results], [201, 201])
        self.assertEqual((Booking.objects.count(), BookingEvent.objects.count()), (2, 2))
        print(f"Court SHARE compatibility: test_kazan_courts/kazan_test; PIDs={pids}; HTTP 201/201", flush=True)

    def test_closing_first_blocks_creation_until_new_surface_state_is_visible(self):
        traces = Queue()
        real_share = services._court_for_share
        def observe_share(pk):
            traces.put(self.trace())
            return real_share(pk)
        with patch("bookings.services.timezone.now", return_value=self.now):
            with patch("bookings.services._court_for_share", side_effect=observe_share):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    with transaction.atomic():
                        court = services._court_for_surface_write(self.court.pk)
                        court.surface_status = "drying"
                        court.save(update_fields=["surface_status"])
                        future = executor.submit(self.request, self.clients[0], "/api/bookings/", self.payload(self.start))
                        pid = traces.get(timeout=5)
                        self.wait_for_lock(pid)
                    status, body = future.result(timeout=15)
        self.assertEqual((status, body["error"]["code"]), (409, "surface_unavailable"))
        self.assertEqual((Booking.objects.count(), BookingEvent.objects.count()), (0, 0))
        self.assertEqual(Court.objects.get(pk=self.court.pk).surface_status, "drying")
        print(f"Court close first: PID={pid}; real Lock wait; HTTP 409; no booking/event", flush=True)

    def test_creation_first_makes_closing_wait_without_deleting_booking(self):
        gate, reader, writer = Event(), Queue(), Queue()
        real_check, real_write = services._has_active_conflict, services._court_for_surface_write
        def pause_reader(**values):
            occupied = real_check(**values)
            reader.put(self.trace())
            if not gate.wait(timeout=5):
                raise AssertionError("Reader was not released")
            return occupied
        def observe_writer(pk):
            writer.put(self.trace())
            return real_write(pk)
        with patch("bookings.services.timezone.now", return_value=self.now):
            with patch("bookings.services._has_active_conflict", side_effect=pause_reader), patch("bookings.services._court_for_surface_write", side_effect=observe_writer):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    create = executor.submit(self.request, self.clients[0], "/api/bookings/", self.payload(self.start))
                    reader_pid = reader.get(timeout=5)
                    close = executor.submit(self.request, self.clients[1], f"/api/courts/{self.court.pk}/surface/close/", {})
                    writer_pid = writer.get(timeout=5)
                    try:
                        self.wait_for_lock(writer_pid)
                    finally:
                        gate.set()
                    results = [create.result(timeout=15), close.result(timeout=15)]
        self.assertNotEqual(reader_pid, writer_pid)
        self.assertEqual([row[0] for row in results], [201, 200])
        self.assertEqual((Booking.objects.count(), BookingEvent.objects.count()), (1, 1))
        self.assertEqual(Court.objects.get(pk=self.court.pk).surface_status, "drying")
        print(f"Court booking first: PIDs={reader_pid},{writer_pid}; writer Lock wait; HTTP 201/200; booking retained", flush=True)

    def time_after_court_wait(self, path, payload, new_now):
        traces = Queue()
        real_share = services._court_for_share
        def observe_share(pk):
            traces.put(self.trace())
            return real_share(pk)
        with patch("bookings.services.timezone.now", return_value=self.now) as clock:
            with patch("bookings.services._court_for_share", side_effect=observe_share):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    with transaction.atomic():
                        services._court_for_surface_write(self.court.pk)
                        future = executor.submit(self.request, self.clients[0], path, payload)
                        pid = traces.get(timeout=5)
                        self.wait_for_lock(pid)
                        clock.return_value = new_now
                    result = future.result(timeout=15)
        return result, pid

    def test_creation_rechecks_now_after_court_wait(self):
        (status, body), pid = self.time_after_court_wait("/api/bookings/", self.payload(self.start), self.start)
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_booking"))
        self.assertEqual((Booking.objects.count(), BookingEvent.objects.count()), (0, 0))
        print(f"Court post-wait creation time: PID={pid}; HTTP 400; unchanged", flush=True)

    def test_reschedule_rechecks_original_time_after_court_wait(self):
        with patch("bookings.services.timezone.now", return_value=self.now):
            booking = services.create_booking(actor=self.employee, court_id=self.court.pk, customer_id=self.customer.pk,
                                              starts_at=self.start, ends_at=self.start + timedelta(hours=1))
        before = services.booking_snapshot(booking)
        target = self.start + timedelta(hours=2)
        payload = {"starts_at": target.isoformat(), "ends_at": (target + timedelta(hours=1)).isoformat()}
        (status, body), pid = self.time_after_court_wait(f"/api/bookings/{booking.pk}/reschedule/", payload, self.start)
        self.assertEqual((status, body["error"]["code"]), (409, "booking_already_started"))
        booking.refresh_from_db()
        self.assertEqual(services.booking_snapshot(booking), before)
        self.assertEqual(BookingEvent.objects.count(), 1)
        print(f"Court post-wait reschedule time: PID={pid}; HTTP 409; original booking/history retained", flush=True)
