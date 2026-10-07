from datetime import datetime, timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from bookings.weather_policy import MOSCOW


class WeatherMigrationTests(TransactionTestCase):
    def test_existing_indoor_and_outdoor_bookings_get_flags_without_rewriting_history(self):
        old = [("bookings", "0002_booking_no_overlap")]
        new = [("bookings", "0003_booking_weather")]
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(new))
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        User, Court, Customer, Booking, Event = [apps.get_model(app, name) for app, name in
            (("auth", "User"), ("bookings", "Court"), ("bookings", "Customer"), ("bookings", "Booking"), ("bookings", "BookingEvent"))]
        actor = User.objects.create(username="fictional-migration-employee", password="!", is_staff=True)
        customer = Customer.objects.create(name="Fictional migration customer", phone="DEMO-MIGRATION")
        start = datetime(2026, 7, 2, 12, tzinfo=MOSCOW)
        rows = []
        for kind in ("indoor", "outdoor"):
            court = Court.objects.create(name=f"Fictional {kind}", court_type=kind, latitude=55, longitude=49)
            booking = Booking.objects.create(court=court, customer=customer, created_by=actor,
                                              starts_at=start, ends_at=start + timedelta(hours=1))
            event = Event.objects.create(booking=booking, actor=actor, event_type="created", after={"legacy": kind})
            rows.append((booking.pk, event.pk, kind))
        executor = MigrationExecutor(connection)
        executor.migrate(new)
        apps = executor.loader.project_state(new).apps
        Booking, Event = apps.get_model("bookings", "Booking"), apps.get_model("bookings", "BookingEvent")
        for pk, event_pk, kind in rows:
            booking = Booking.objects.get(pk=pk)
            self.assertEqual(booking.weather_status, "not_applicable" if kind == "indoor" else "unknown")
            self.assertIsNone(booking.weather_checked_at)
            self.assertEqual(Event.objects.get(pk=event_pk).after, {"legacy": kind})
