from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from bookings.models import Booking, BookingEvent, Court, Customer


class PostgreSQLConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = get_user_model().objects.create(username="constraint-employee")
        cls.court = Court.objects.create(
            name="Test court", court_type="outdoor", latitude="55.790000", longitude="49.120000"
        )
        cls.customer = Customer.objects.create(name="Fictional customer", phone="DEMO-002")
        cls.start = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)

    def create_booking(self, **changes):
        values = {
            "court": self.court,
            "customer": self.customer,
            "created_by": self.employee,
            "starts_at": self.start,
            "ends_at": self.start + timedelta(hours=1),
        }
        return Booking.objects.create(**{**values, **changes})

    def assert_invalid_booking(self, **changes):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_booking(**changes)

    def test_database_rejects_empty_names_and_phone_without_model_validation(self):
        for instance, field in ((self.court, "name"), (self.customer, "name"), (self.customer, "phone")):
            for value in (None, "", "   ", "\t "):
                with self.subTest(model=type(instance).__name__, field=field, value=value):
                    with self.assertRaises(IntegrityError), transaction.atomic():
                        type(instance).objects.filter(pk=instance.pk).update(**{field: value})

    def test_database_rejects_unknown_enum_values(self):
        booking = self.create_booking()
        event = BookingEvent.objects.create(
            booking=booking, actor=self.employee, event_type="created", after={"status": "active"}
        )
        for instance, field in (
            (self.court, "court_type"),
            (self.court, "surface_status"),
            (booking, "status"),
            (event, "event_type"),
        ):
            with self.subTest(model=type(instance).__name__, field=field):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    type(instance).objects.filter(pk=instance.pk).update(**{field: "unknown"})

    def test_coordinate_limits_are_enforced(self):
        for field, value in (
            ("latitude", "90.000001"),
            ("latitude", "-90.000001"),
            ("longitude", "180.000001"),
            ("longitude", "-180.000001"),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Court.objects.filter(pk=self.court.pk).update(**{field: value})
        for latitude, longitude in (("90", "180"), ("-90", "-180")):
            Court.objects.filter(pk=self.court.pk).update(latitude=latitude, longitude=longitude)

    def test_time_order_and_minimum_duration_are_enforced(self):
        for minutes in (-60, 0, 50):
            with self.subTest(minutes=minutes):
                self.assert_invalid_booking(ends_at=self.start + timedelta(minutes=minutes))
        self.create_booking(ends_at=self.start + timedelta(minutes=60))

    def test_both_boundaries_must_be_on_ten_minute_grid(self):
        for field in ("starts_at", "ends_at"):
            for offset in (timedelta(minutes=1), timedelta(seconds=1), timedelta(microseconds=1)):
                with self.subTest(field=field, offset=offset):
                    values = {"starts_at": self.start, "ends_at": self.start + timedelta(hours=2)}
                    values[field] += offset
                    self.assert_invalid_booking(**values)
        self.create_booking(
            starts_at=self.start + timedelta(minutes=10), ends_at=self.start + timedelta(minutes=80)
        )

    def test_active_overlaps_are_rejected_for_the_same_court(self):
        self.create_booking(ends_at=self.start + timedelta(hours=2))
        for start_minutes, end_minutes in ((0, 120), (-60, 60), (60, 180), (30, 90), (-60, 180)):
            with self.subTest(start=start_minutes, end=end_minutes):
                self.assert_invalid_booking(
                    starts_at=self.start + timedelta(minutes=start_minutes),
                    ends_at=self.start + timedelta(minutes=end_minutes),
                )

    def test_adjacent_bookings_are_allowed(self):
        self.create_booking()
        self.create_booking(starts_at=self.start + timedelta(hours=1), ends_at=self.start + timedelta(hours=2))
        self.assertEqual(Booking.objects.count(), 2)

    def test_same_instants_in_kazan_time_still_conflict(self):
        self.create_booking()
        self.assert_invalid_booking(
            starts_at=self.start.astimezone(ZoneInfo("Europe/Moscow")),
            ends_at=(self.start + timedelta(hours=1)).astimezone(ZoneInfo("Europe/Moscow")),
        )

    def test_simultaneous_bookings_on_different_courts_are_allowed(self):
        other_court = Court.objects.create(
            name="Other test court", court_type="indoor", latitude="55.800000", longitude="49.130000"
        )
        self.create_booking()
        self.create_booking(court=other_court)
        self.assertEqual(Booking.objects.count(), 2)

    def test_cancellation_releases_interval_but_conflicting_reactivation_is_rejected(self):
        cancelled = self.create_booking()
        Booking.objects.filter(pk=cancelled.pk).update(status="cancelled")
        self.create_booking()
        self.create_booking(status="cancelled")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.filter(pk=cancelled.pk).update(status="active")
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, "cancelled")

    def test_conflicting_update_preserves_original_booking(self):
        self.create_booking()
        later = self.create_booking(
            starts_at=self.start + timedelta(hours=2), ends_at=self.start + timedelta(hours=3)
        )
        original_times = (later.starts_at, later.ends_at)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.filter(pk=later.pk).update(starts_at=self.start + timedelta(minutes=30))
        later.refresh_from_db()
        self.assertEqual((later.starts_at, later.ends_at), original_times)
        self.assertEqual(Booking.objects.count(), 2)

    def test_only_cancelled_bookings_can_be_archived(self):
        self.assert_invalid_booking(archived_at=self.start)
        archived = self.create_booking(status="cancelled", archived_at=self.start)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.filter(pk=archived.pk).update(status="active")
