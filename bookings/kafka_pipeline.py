"""Transactional outbox publisher and idempotent notification consumer."""

import json
import logging
import os
import uuid

from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone
from kafka import KafkaConsumer, KafkaProducer, TopicPartition
from kafka.errors import KafkaError
from kafka.structs import OffsetAndMetadata

from .models import Booking, BookingEvent, OutboxMessage, SimulatedNotification

logger = logging.getLogger(__name__)
TOPIC = "booking.weather.v1"
PUBLISHER_LOCK = 0x4B435055424C4953
INVALIDATING_EVENTS = ("weather_blocked", "weather_cleared", "rescheduled", "cancelled", "archived")
TEXTS = {
    "booking.weather_blocked": "Возможен дождь. Бронь сохраняется; если прогноз не улучшится, с вами свяжется оператор.",
    "booking.weather_cleared": "Погодное ограничение снято. Бронь сохраняется.",
}


def kafka_address():
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")


def _same_manual_transition_audit(event, manual):
    """A manual audit immediately after its own system event isn't a new transition."""
    before, after = event.before, event.after
    manual_before, manual_after = manual["before"], manual["after"]
    if not all(isinstance(value, dict) for value in (before, after, manual_before, manual_after)):
        return False
    # The employee audit is saved just after the system event; updated_at may differ.
    return (before == manual_before and
            {k: v for k, v in after.items() if k != "updated_at"} ==
            {k: v for k, v in manual_after.items() if k != "updated_at"})


def _has_later_relevant_change(booking, event):
    if BookingEvent.objects.filter(booking=booking, pk__gt=event.pk,
                                   event_type__in=INVALIDATING_EVENTS).exists():
        return True
    later = booking.events.filter(pk__gt=event.pk).order_by("pk").values("pk", "event_type", "before", "after")
    for position, item in enumerate(later.iterator()):
        if item["event_type"] != "weather_checked":
            continue
        before, after = item["before"], item["after"]
        if (not isinstance(before, dict) or not isinstance(after, dict) or
                "weather_status" not in before or "weather_status" not in after):
            return True
        if before["weather_status"] != after["weather_status"]:
            if position == 0 and _same_manual_transition_audit(event, item):
                continue
            return True
    return False


def _encode_message(message):
    payload = message.payload
    fields = {"event_id", "event_type", "schema_version", "booking_id", "court_id", "occurred_at", "episode_id"}
    if (not isinstance(payload, dict) or set(payload) != fields or
            payload["event_id"] != str(message.event_id) or payload["event_type"] != message.event_type or
            type(payload["schema_version"]) is not int or payload["schema_version"] != 1 or
            message.schema_version != 1 or message.event_type not in TEXTS or
            type(payload["booking_id"]) is not int or payload["booking_id"] != message.booking_event.booking_id or
            type(payload["court_id"]) is not int or payload["court_id"] <= 0 or
            not isinstance(payload["occurred_at"], str) or
            (payload["episode_id"] is not None and type(payload["episode_id"]) is not int)):
        raise ValueError("Invalid outbox envelope")
    return (str(payload["booking_id"]).encode("ascii"),
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"))


def _record_publisher_failure(message, code):
    OutboxMessage.objects.filter(pk=message.pk, published_at__isnull=True).update(
        attempts=F("attempts") + 1, error_code=code)
    logger.warning("Outbox delivery failed; pending message %s: %s", message.pk, code)


def publish_pending(producer_factory=None):
    """Single publisher, ordered by outbox id. Stop on the first failure."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [PUBLISHER_LOCK])
        if not cursor.fetchone()[0]:
            return 0
    producer = None
    count = 0
    try:
        for message in OutboxMessage.objects.filter(published_at__isnull=True).select_related("booking_event").order_by("pk").iterator():
            try:
                key, payload = _encode_message(message)
            except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
                _record_publisher_failure(message, "invalid_payload")
                break
            try:
                if producer is None:
                    factory = producer_factory or KafkaProducer
                    producer = factory(bootstrap_servers=kafka_address(), acks="all", retries=0,
                                       request_timeout_ms=5000, max_block_ms=5000)
                # send() only queues locally: get() waits for the broker acknowledgement.
                producer.send(TOPIC, key=key, value=payload).get(timeout=10)
            except (KafkaError, OSError, TimeoutError):
                _record_publisher_failure(message, "kafka_unavailable")
                break
            except Exception:
                _record_publisher_failure(message, "publisher_error")
                break
            OutboxMessage.objects.filter(pk=message.pk, published_at__isnull=True).update(
                published_at=timezone.now(), attempts=F("attempts") + 1, error_code="")
            count += 1
    finally:
        try:
            if producer is not None:
                producer.close(timeout=5)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [PUBLISHER_LOCK])
    return count


@transaction.atomic
def process_notification(value, key):
    """Validate persisted event, lock the booking, then persist one result."""
    payload = json.loads(value.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid event envelope")
    event_id = uuid.UUID(payload["event_id"])
    message = OutboxMessage.objects.select_related("booking_event").get(event_id=event_id)
    event = message.booking_event
    if (payload != message.payload or key != str(event.booking_id).encode() or
            message.event_type not in TEXTS or message.schema_version != 1 or
            payload.get("schema_version") != 1 or payload.get("event_type") != message.event_type or
            event.event_type != message.event_type.removeprefix("booking.")):
        raise ValueError("Event does not match persisted outbox")
    booking = Booking.objects.select_for_update(of=("self",)).select_related("court").get(pk=event.booking_id)
    existing = SimulatedNotification.objects.filter(event=message).first()
    if existing:
        return existing
    # Event pk orders committed transitions for the same row: every writer locks booking.
    newer = _has_later_relevant_change(booking, event)
    expected = "blocked" if message.event_type == "booking.weather_blocked" else "clear"
    current = (booking.status == "active" and booking.archived_at is None and
               booking.starts_at > timezone.now() and booking.court.court_type == "outdoor" and
               booking.weather_status == expected and not newer)
    return SimulatedNotification.objects.create(
        event=message, result="simulated" if current else "suppressed",
        text=TEXTS[message.event_type] if current else "")


def consume_one(consumer, record):
    process_notification(record.value, record.key)
    # Exact offset for this record; never commit prefetched, unprocessed records.
    consumer.commit({TopicPartition(record.topic, record.partition): OffsetAndMetadata(record.offset + 1, "")})


def new_consumer():
    return KafkaConsumer(TOPIC, bootstrap_servers=kafka_address(), group_id="kazan-courts-notifications-v1",
                         enable_auto_commit=False, auto_offset_reset="earliest", max_poll_records=1,
                         request_timeout_ms=10000)
