"""Atomic building blocks for future automated weather processing."""

import uuid

from django.db import transaction
from django.utils import timezone

from .models import Booking, BookingEvent, OutboxMessage
from .services import BookingOperationError, _employee, booking_snapshot


@transaction.atomic
def apply_weather_transition(*, booking_id, expected_starts_at, expected_ends_at, status, checked_at):
    if status not in (Booking.WeatherStatus.CLEAR, Booking.WeatherStatus.BLOCKED):
        raise ValueError("A reliable clear or blocked result is required")
    booking = Booking.objects.select_for_update(of=("self",)).select_related("court").get(pk=booking_id)
    if (booking.status != Booking.Status.ACTIVE or booking.archived_at is not None or
            booking.court.court_type != "outdoor" or booking.starts_at <= timezone.now() or
            (booking.starts_at, booking.ends_at) != (expected_starts_at, expected_ends_at)):
        return None
    previous = booking.weather_status
    if previous == status or (status == "clear" and previous != "blocked"):
        return None
    before = booking_snapshot(booking)
    last_transition = booking.events.filter(event_type__in=["weather_blocked", "weather_cleared", "weather_checked", "rescheduled"]).order_by("-pk").first()
    # Legacy manual weather checks can leave a blocked booking with no system episode.
    episode = last_transition if status == "clear" and last_transition and last_transition.event_type == "weather_blocked" else None
    booking.weather_status = status
    booking.weather_checked_at = checked_at
    booking.save(update_fields=["weather_status", "weather_checked_at", "updated_at"])
    event = BookingEvent.objects.create(
        booking=booking, actor=None, event_type="weather_blocked" if status == "blocked" else "weather_cleared",
        weather_episode=episode, before=before, after=booking_snapshot(booking), occurred_at=checked_at,
    )
    event_id = uuid.uuid4()
    event_type = "booking.weather_blocked" if status == "blocked" else "booking.weather_cleared"
    message = OutboxMessage.objects.create(
        event_id=event_id, booking_event=event, event_type=event_type, schema_version=1,
        payload={"event_id": str(event_id), "event_type": event_type, "schema_version": 1,
                 "booking_id": booking.pk, "court_id": booking.court_id, "occurred_at": checked_at.isoformat(),
                 "episode_id": event.pk if status == "blocked" else episode.pk if episode else None},
    )
    return message


@transaction.atomic
def record_no_answer(*, actor, booking_id):
    employee = _employee(actor)
    booking = Booking.objects.select_for_update().get(pk=booking_id)
    if booking.status != Booking.Status.ACTIVE or booking.archived_at is not None:
        raise BookingOperationError("invalid_booking_state", "Бронь больше не действует.", 409)
    snapshot = booking_snapshot(booking)
    return BookingEvent.objects.create(booking=booking, actor=employee, event_type="contact_no_answer",
                                       before=snapshot, after=snapshot, reason="Не дозвонились")
