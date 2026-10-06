from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from bookings.models import Booking, BookingEvent, Court, Customer


class ModelRelationshipTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.employee = get_user_model().objects.create(username="employee")
        cls.actor = get_user_model().objects.create(username="event-actor")
        cls.court = Court.objects.create(
            name="Test court", court_type="indoor", latitude="55.790000", longitude="49.120000"
        )
        cls.customer = Customer.objects.create(name="Fictional customer", phone="DEMO-001")
        cls.start = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
        cls.booking = Booking.objects.create(
            court=cls.court,
            customer=cls.customer,
            created_by=cls.employee,
            starts_at=cls.start,
            ends_at=cls.start + timedelta(hours=1),
        )
        cls.snapshot = {
            "court_id": cls.court.pk,
            "customer_id": cls.customer.pk,
            "starts_at": cls.booking.starts_at.isoformat(),
            "ends_at": cls.booking.ends_at.isoformat(),
            "status": "active",
            "cancellation_reason": "",
            "archived_at": None,
        }
        cls.event = BookingEvent.objects.create(
            booking=cls.booking, actor=cls.actor, event_type="created", after=cls.snapshot
        )

    def test_customer_email_is_optional_but_phone_is_required(self):
        self.customer.full_clean()
        self.assertEqual(self.customer.email, "")
        self.customer.phone = ""
        with self.assertRaises(ValidationError) as error:
            self.customer.full_clean()
        self.assertIn("phone", error.exception.message_dict)

    def test_customers_may_share_the_same_phone(self):
        Customer.objects.create(name="Other fictional customer", phone=self.customer.phone)
        self.assertEqual(Customer.objects.filter(phone=self.customer.phone).count(), 2)

    def test_booking_validation_reports_invalid_time_fields(self):
        cases = (
            ("starts_at", self.start.replace(tzinfo=None)),
            ("ends_at", self.start.replace(tzinfo=None) + timedelta(hours=2)),
            ("starts_at", self.start + timedelta(minutes=1)),
            ("starts_at", self.start + timedelta(seconds=1)),
            ("ends_at", self.start + timedelta(hours=2, microseconds=1)),
            ("ends_at", self.start + timedelta(minutes=50)),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.booking.starts_at = self.start
                self.booking.ends_at = self.start + timedelta(hours=2)
                setattr(self.booking, field, value)
                with self.assertRaises(ValidationError) as error:
                    self.booking.clean()
                self.assertIn(field, error.exception.message_dict)

    def test_referenced_objects_cannot_be_deleted(self):
        for instance in (self.court, self.customer, self.employee, self.actor, self.booking):
            with self.subTest(model=type(instance).__name__, pk=instance.pk):
                with self.assertRaises(ProtectedError):
                    instance.delete()
        self.assertTrue(BookingEvent.objects.filter(pk=self.event.pk).exists())

    def test_foreign_keys_reject_missing_references(self):
        references = (
            (self.booking, "court_id"),
            (self.booking, "customer_id"),
            (self.booking, "created_by_id"),
            (self.event, "booking_id"),
            (self.event, "actor_id"),
        )
        for instance, field in references:
            with self.subTest(model=type(instance).__name__, field=field):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    type(instance).objects.filter(pk=instance.pk).update(**{field: 999999})
                    connection.check_constraints()

    def test_history_preserves_snapshots_and_orders_events_with_equal_timestamps(self):
        self.booking.ends_at += timedelta(minutes=10)
        self.booking.save(update_fields=["ends_at", "updated_at"])
        updated_snapshot = {**self.snapshot, "ends_at": self.booking.ends_at.isoformat()}
        change = BookingEvent.objects.create(
            booking=self.booking,
            actor=self.actor,
            occurred_at=self.event.occurred_at,
            event_type="rescheduled",
            before=self.snapshot,
            after=updated_snapshot,
            reason="Fictional reschedule",
        )
        self.event.refresh_from_db()
        change.refresh_from_db()
        self.assertIsNone(self.event.before)
        self.assertEqual(self.event.after, self.snapshot)
        self.assertEqual(change.before, self.snapshot)
        self.assertEqual(change.after, updated_snapshot)
        self.assertEqual(change.reason, "Fictional reschedule")
        self.assertEqual(
            list(BookingEvent.objects.filter(booking=self.booking).values_list("pk", flat=True)),
            [self.event.pk, change.pk],
        )
