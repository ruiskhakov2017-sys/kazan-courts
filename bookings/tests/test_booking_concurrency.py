from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from queue import Queue
from threading import Barrier
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import Client, TransactionTestCase

from bookings import services
from bookings.models import Booking, BookingEvent, Court, Customer


class PostgreSQLBookingConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.employee = get_user_model().objects.create_user("concurrent-employee", is_staff=True)
        self.court = Court.objects.create(name="Fictional concurrent court", court_type="indoor", latitude=55, longitude=49)
        self.customer = Customer.objects.create(name="Fictional concurrent customer", phone="DEMO-CONCURRENT")
        self.now = datetime(2026, 7, 1, 12, tzinfo=ZoneInfo("Europe/Moscow"))
        self.clients = [Client(), Client()]
        for client in self.clients:
            client.force_login(self.employee)

    def request(self, client, start):
        try:
            response = client.post("/api/bookings/", {
                "customer_id": self.customer.pk, "court_id": self.court.pk,
                "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(),
            }, content_type="application/json")
            return response.status_code, response.json()
        finally:
            connections.close_all()

    def run_pair(self, starts):
        barrier = Barrier(2)
        observations = Queue()
        real_check = services._has_active_conflict
        def synchronized_check(**values):
            occupied = real_check(**values)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute("SET LOCAL statement_timeout = '10s'")
                cursor.execute("SELECT current_database(), current_user, pg_backend_pid()")
                observations.put((*cursor.fetchone(), occupied))
            barrier.wait(timeout=10)
            return occupied
        with patch("bookings.services.timezone.now", return_value=self.now):
            # Both real prechecks finish before either INSERT; production has no barrier or test hook.
            with patch("bookings.services._has_active_conflict", side_effect=synchronized_check):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(self.request, client, start)
                               for client, start in zip(self.clients, starts)]
                    results = [future.result(timeout=20) for future in futures]
        traces = [observations.get_nowait(), observations.get_nowait()]
        self.assertTrue(observations.empty())
        self.assertEqual({row[0] for row in traces}, {"test_kazan_courts"})
        self.assertEqual({row[1] for row in traces}, {"kazan_test"})
        self.assertEqual(len({row[2] for row in traces}), 2)
        self.assertEqual([row[3] for row in traces], [False, False])
        return results, traces

    def test_two_simultaneous_requests_leave_one_booking_and_event_repeatedly(self):
        for round_number in range(3):
            start = self.now + timedelta(days=round_number + 1)
            with self.subTest(round=round_number + 1):
                results, traces = self.run_pair([start, start])
                self.assertEqual(sorted(status for status, body in results), [201, 409])
                winner = next(body["booking"] for status, body in results if status == 201)
                loser = next(body for status, body in results if status == 409)
                self.assertEqual(loser["error"]["code"], "time_conflict")
                booking = Booking.objects.get(court=self.court, starts_at=start, status="active")
                self.assertEqual(booking.pk, winner["id"])
                self.assertEqual(Booking.objects.count(), round_number + 1)
                self.assertEqual(BookingEvent.objects.count(), round_number + 1)
                event = booking.events.get()
                self.assertEqual(event.event_type, "created")
                self.assertEqual(event.after, services.booking_snapshot(booking))
                print(f"Concurrency round {round_number + 1}: test_kazan_courts/kazan_test; "
                      f"PIDs={sorted(row[2] for row in traces)}; both prechecks free; HTTP 201/409; one booking/event", flush=True)

    def test_simultaneous_adjacent_bookings_both_succeed(self):
        start = self.now + timedelta(days=1)
        results, traces = self.run_pair([start, start + timedelta(hours=1)])
        self.assertEqual([status for status, body in results], [201, 201])
        self.assertEqual(Booking.objects.count(), 2)
        self.assertEqual(BookingEvent.objects.count(), 2)
        print(f"Adjacent concurrency: PIDs={sorted(row[2] for row in traces)}; HTTP 201/201; two bookings/events", flush=True)
