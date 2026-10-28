from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from bookings.models import Booking, BookingEvent, Court, Customer, OutboxMessage, SimulatedNotification
from bookings.weather_transitions import apply_weather_transition, record_no_answer


class WeatherOutboxTests(TestCase):
    def setUp(self):
        self.employee = get_user_model().objects.create_user("outbox-employee", is_staff=True)
        self.customer = Customer.objects.create(name="Fictional", phone="DEMO")
        self.court = Court.objects.create(name="Outdoor", court_type="outdoor", latitude=55, longitude=49)
        start = timezone.now() + timedelta(days=2)
        start = start.replace(minute=(start.minute // 10) * 10, second=0, microsecond=0)
        self.booking = Booking.objects.create(court=self.court, customer=self.customer, created_by=self.employee,
                                              starts_at=start, ends_at=start + timedelta(hours=1), weather_status="clear")
        self.now = timezone.now()

    def transition(self, status):
        return apply_weather_transition(booking_id=self.booking.pk, expected_starts_at=self.booking.starts_at,
                                        expected_ends_at=self.booking.ends_at, status=status, checked_at=self.now)

    def test_episodes_no_duplicate_and_message_identity(self):
        first = self.transition("blocked")
        self.assertIsNone(self.transition("blocked"))
        cleared = self.transition("clear")
        second = self.transition("blocked")
        self.assertEqual(cleared.booking_event.weather_episode_id, first.booking_event_id)
        self.assertNotEqual(first.payload["episode_id"], second.payload["episode_id"])
        self.assertEqual(cleared.payload["episode_id"], first.booking_event_id)
        self.assertEqual(first.payload["event_id"], str(first.event_id))
        self.assertEqual(first.payload["event_type"], "booking.weather_blocked")
        self.assertNotIn("phone", first.payload)
        self.assertEqual(OutboxMessage.objects.count(), 3)

    def test_atomic_rollback_on_history_and_outbox_failure(self):
        for target in ("bookings.weather_transitions.BookingEvent.objects.create", "bookings.weather_transitions.OutboxMessage.objects.create"):
            with self.subTest(target=target), patch(target, side_effect=IntegrityError("synthetic")):
                with self.assertRaises(IntegrityError):
                    self.transition("blocked")
                self.booking.refresh_from_db()
                self.assertEqual(self.booking.weather_status, "clear")
                self.assertFalse(BookingEvent.objects.exists())
                self.assertFalse(OutboxMessage.objects.exists())

    def test_actor_constraint_and_legacy_event(self):
        legacy = BookingEvent.objects.create(booking=self.booking, actor=self.employee, event_type="weather_checked", after={})
        with self.assertRaises(IntegrityError), transaction.atomic():
            BookingEvent.objects.create(booking=self.booking, actor=None, event_type="created", after={})
        with self.assertRaises(IntegrityError), transaction.atomic():
            BookingEvent.objects.create(booking=self.booking, actor=self.employee, event_type="weather_blocked", after={})
        legacy.refresh_from_db()
        self.assertEqual(legacy.actor, self.employee)

    def test_legacy_blocked_can_clear_without_invented_episode(self):
        Booking.objects.filter(pk=self.booking.pk).update(weather_status="blocked")
        result = self.transition("clear")
        self.assertIsNone(result.booking_event.weather_episode_id)
        self.assertIsNone(result.payload["episode_id"])

    def test_contact_and_notification_uniqueness(self):
        message = self.transition("blocked")
        event = record_no_answer(actor=self.employee, booking_id=self.booking.pk)
        self.assertEqual(event.actor, self.employee)
        self.assertEqual(event.before, event.after)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "active")
        SimulatedNotification.objects.create(event=message, result="simulated", text="Fictional")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SimulatedNotification.objects.create(event=message, result="simulated")
        with self.assertRaises(IntegrityError), transaction.atomic():
            OutboxMessage.objects.create(event_id=message.event_id, booking_event=event,
                                         event_type="booking.weather_blocked", payload={})

    def test_cancelled_or_rescheduled_snapshot_is_not_updated(self):
        Booking.objects.filter(pk=self.booking.pk).update(status="cancelled")
        self.assertIsNone(self.transition("blocked"))
        Booking.objects.filter(pk=self.booking.pk).update(status="active", starts_at=self.booking.starts_at + timedelta(hours=2), ends_at=self.booking.ends_at + timedelta(hours=2))
        self.assertIsNone(self.transition("blocked"))
        self.assertFalse(OutboxMessage.objects.exists())
