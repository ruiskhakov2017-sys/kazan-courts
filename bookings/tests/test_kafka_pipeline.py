import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest import skipUnless
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import OperationalError, close_old_connections, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from kafka import KafkaConsumer, KafkaProducer

from bookings.kafka_pipeline import TOPIC, consume_one, process_notification, publish_pending
from bookings.models import Booking, BookingEvent, Court, Customer, OutboxMessage, SimulatedNotification
from bookings.services import _recheck_weather
from bookings.tests.weather_fixtures import weather_forecast
from bookings.weather_client import AutomaticForecastResult
from bookings.weather_policy import Forecast, MOSCOW
from bookings.weather_scheduler import run_weather_check
from bookings.weather_transitions import apply_weather_transition


class SchedulerCommandTests(SimpleTestCase):
    @override_settings(WEATHER_NETWORK_ENABLED=False)
    def test_disabled_scheduler_never_calls_weather_check(self):
        with patch("bookings.management.commands.schedule_weather.run_weather_check") as run, \
             patch("bookings.management.commands.schedule_weather.time.sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                call_command("schedule_weather")
        run.assert_not_called()

    @override_settings(WEATHER_NETWORK_ENABLED=True)
    def test_enabled_scheduler_uses_hourly_interval(self):
        with patch("bookings.management.commands.schedule_weather.run_weather_check", return_value={"status": "success"}) as run, \
             patch("bookings.management.commands.schedule_weather.time.sleep", side_effect=KeyboardInterrupt) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                call_command("schedule_weather")
        run.assert_called_once()
        sleep.assert_called_once_with(3600)

    @override_settings(WEATHER_NETWORK_ENABLED=True)
    def test_temporary_failures_use_three_bounded_retries_then_hourly(self):
        results = [{"status": "unavailable", "retryable": True}] * 4 + [{"status": "success"}]
        with patch("bookings.management.commands.schedule_weather.run_weather_check", side_effect=results), \
             patch("bookings.management.commands.schedule_weather.time.sleep",
                   side_effect=[None, None, None, None, KeyboardInterrupt]) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                call_command("schedule_weather")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [60, 120, 300, 3600, 3600])

    @override_settings(WEATHER_NETWORK_ENABLED=True)
    def test_permanent_failure_is_not_short_retried(self):
        with patch("bookings.management.commands.schedule_weather.run_weather_check",
                   return_value={"status": "unavailable", "retryable": False}), \
             patch("bookings.management.commands.schedule_weather.time.sleep", side_effect=KeyboardInterrupt) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                call_command("schedule_weather")
        sleep.assert_called_once_with(3600)

    @override_settings(WEATHER_NETWORK_ENABLED=True)
    def test_temporary_database_failure_is_retried(self):
        with patch("bookings.management.commands.schedule_weather.run_weather_check", side_effect=OperationalError("synthetic")), \
             patch("bookings.management.commands.schedule_weather.time.sleep", side_effect=KeyboardInterrupt) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                call_command("schedule_weather")
        sleep.assert_called_once_with(60)

    @override_settings(WEATHER_NETWORK_ENABLED=True)
    def test_retry_after_is_honored_before_next_cycle(self):
        now = timezone.now()
        result = {"status": "unavailable", "retryable": True, "next_allowed_at": now + timedelta(minutes=10)}
        with patch("bookings.management.commands.schedule_weather.timezone.now", return_value=now), \
             patch("bookings.management.commands.schedule_weather.run_weather_check", return_value=result), \
             patch("bookings.management.commands.schedule_weather.time.sleep", side_effect=KeyboardInterrupt) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                call_command("schedule_weather")
        sleep.assert_called_once_with(600)

    @override_settings(WEATHER_NETWORK_ENABLED=True)
    def test_long_deferral_polls_without_bypassing_database_guard(self):
        now = timezone.now()
        result = {"status": "deferred", "next_allowed_at": now + timedelta(hours=48)}
        with patch("bookings.management.commands.schedule_weather.timezone.now", return_value=now), \
             patch("bookings.management.commands.schedule_weather.run_weather_check", return_value=result), \
             patch("bookings.management.commands.schedule_weather.time.sleep", side_effect=KeyboardInterrupt) as sleep:
            with self.assertRaises(KeyboardInterrupt):
                call_command("schedule_weather")
        sleep.assert_called_once_with(3600)


class PipelineFixture:
    def setUp(self):
        employee = get_user_model().objects.create_user("pipeline", is_staff=True)
        self.employee = employee
        customer = Customer.objects.create(name="Synthetic", phone="DEMO")
        court = Court.objects.create(name="Outside", court_type="outdoor", latitude=55, longitude=49)
        start = (timezone.now() + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
        self.booking = Booking.objects.create(court=court, customer=customer, created_by=employee,
                                              starts_at=start, ends_at=start + timedelta(hours=1), weather_status="clear")

    def transition(self, status):
        return apply_weather_transition(booking_id=self.booking.pk, expected_starts_at=self.booking.starts_at,
                                        expected_ends_at=self.booking.ends_at, status=status, checked_at=timezone.now())

    def process(self, message):
        return process_notification(json.dumps(message.payload).encode(), str(self.booking.pk).encode())


class NotificationTests(PipelineFixture, TestCase):
    def test_sequential_transitions_create_two_simulations(self):
        blocked = self.transition("blocked")
        self.assertEqual(self.process(blocked).result, "simulated")
        cleared = self.transition("clear")
        self.assertEqual(self.process(cleared).result, "simulated")
        self.assertEqual(SimulatedNotification.objects.count(), 2)

    def test_old_warning_is_suppressed_after_clear(self):
        blocked = self.transition("blocked")
        cleared = self.transition("clear")
        self.assertEqual(self.process(blocked).result, "suppressed")
        self.assertEqual(self.process(cleared).result, "simulated")

    def test_duplicate_delivery_does_not_repeat_business_result(self):
        blocked = self.transition("blocked")
        first = self.process(blocked)
        self.assertEqual(self.process(blocked).pk, first.pk)
        self.assertEqual(SimulatedNotification.objects.count(), 1)

    def test_second_episode_not_duplicate(self):
        first = self.transition("blocked")
        self.process(first)
        self.process(self.transition("clear"))
        second = self.transition("blocked")
        self.assertEqual(self.process(second).result, "simulated")
        self.assertNotEqual(first.event_id, second.event_id)
        self.assertEqual(SimulatedNotification.objects.count(), 3)

    def test_legacy_null_episode_and_newer_transition(self):
        Booking.objects.filter(pk=self.booking.pk).update(weather_status="blocked")
        cleared = self.transition("clear")
        self.assertIsNone(cleared.payload["episode_id"])
        self.transition("blocked")
        self.assertEqual(self.process(cleared).result, "suppressed")

    def test_old_cleared_event_after_unknown_then_clear_is_suppressed(self):
        self.transition("blocked")
        cleared = self.transition("clear")
        _recheck_weather(actor=self.employee, forecast=Forecast.unavailable(timezone.now(), "synthetic"))
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.weather_status, "unknown")
        _recheck_weather(actor=self.employee, forecast=weather_forecast(timezone.now(), days=3))
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.weather_status, "clear")
        self.assertEqual(self.process(cleared).result, "suppressed")

    def test_unchanged_manual_recheck_does_not_invalidate_cleared_event(self):
        self.transition("blocked")
        cleared = self.transition("clear")
        _recheck_weather(actor=self.employee, forecast=weather_forecast(timezone.now(), days=3))
        self.assertEqual(self.process(cleared).result, "simulated")

    def test_incomplete_later_manual_audit_is_suppressed_conservatively(self):
        self.transition("blocked")
        cleared = self.transition("clear")
        BookingEvent.objects.create(booking=self.booking, actor=self.employee, event_type="weather_checked",
                                    before=None, after={})
        self.assertEqual(self.process(cleared).result, "suppressed")

    def test_cancel_and_untrusted_envelope(self):
        blocked = self.transition("blocked")
        bad = dict(blocked.payload, court_id=999)
        with self.assertRaises(ValueError):
            process_notification(json.dumps(bad).encode(), str(self.booking.pk).encode())
        with self.assertRaises(ValueError):
            process_notification(json.dumps(blocked.payload).encode(), b"wrong-key")
        Booking.objects.filter(pk=self.booking.pk).update(status="cancelled")
        self.assertEqual(self.process(blocked).result, "suppressed")

    def test_manual_recheck_transitions_generate_events_and_keep_employee_audit(self):
        start_local = self.booking.starts_at.astimezone(MOSCOW)
        rain = weather_forecast(timezone.now(), days=3, changes={start_local: 61})
        _recheck_weather(actor=self.employee, forecast=rain)
        blocked = OutboxMessage.objects.get(event_type="booking.weather_blocked")
        manual = self.booking.events.filter(event_type="weather_checked").get()
        self.assertEqual(manual.actor, self.employee)
        self.assertEqual((manual.before["weather_status"], manual.after["weather_status"]), ("clear", "blocked"))
        self.assertEqual(self.process(blocked).result, "simulated")
        _recheck_weather(actor=self.employee, forecast=weather_forecast(timezone.now(), days=3))
        cleared = OutboxMessage.objects.get(event_type="booking.weather_cleared")
        self.assertEqual(self.process(cleared).result, "simulated")
        self.assertEqual(self.booking.events.filter(event_type="weather_checked", actor=self.employee).count(), 2)

    def test_manual_recheck_rolls_back_transition_and_audit_if_outbox_fails(self):
        rain = weather_forecast(timezone.now(), days=3, changes={self.booking.starts_at.astimezone(MOSCOW): 61})
        with patch("bookings.weather_transitions.OutboxMessage.objects.create", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                _recheck_weather(actor=self.employee, forecast=rain)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.weather_status, "clear")
        self.assertFalse(self.booking.events.exists())
        self.assertFalse(OutboxMessage.objects.exists())

    def test_manual_recheck_does_not_overwrite_a_newer_automatic_result(self):
        later = timezone.now() + timedelta(minutes=1)
        Booking.objects.filter(pk=self.booking.pk).update(weather_status="blocked", weather_checked_at=later)
        _recheck_weather(actor=self.employee, forecast=weather_forecast(timezone.now(), days=3))
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.weather_status, "blocked")
        self.assertEqual(self.booking.weather_checked_at, later)
        self.assertFalse(OutboxMessage.objects.exists())

    def test_offset_committed_only_after_success(self):
        consumer = MagicMock()
        record = MagicMock(value=b"invalid", key=b"1", topic=TOPIC, partition=0, offset=4)
        with self.assertRaises(ValueError):
            consume_one(consumer, record)
        consumer.commit.assert_not_called()
        message = self.transition("blocked")
        record.value = json.dumps(message.payload).encode()
        record.key = str(self.booking.pk).encode()
        consume_one(consumer, record)
        self.assertEqual(consumer.commit.call_count, 1)

    def test_db_failure_does_not_commit_offset(self):
        consumer = MagicMock()
        record = MagicMock(value=b"{}", key=b"1")
        with patch("bookings.kafka_pipeline.process_notification", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                consume_one(consumer, record)
        consumer.commit.assert_not_called()


class PublisherTests(PipelineFixture, TransactionTestCase):
    def test_broker_failure_retains_first_and_does_not_skip(self):
        first = self.transition("blocked")
        second = self.transition("clear")
        producer = MagicMock()
        producer.send.return_value.get.side_effect = TimeoutError()
        self.assertEqual(publish_pending(lambda **kw: producer), 0)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(first.published_at)
        self.assertEqual(first.attempts, 1)
        self.assertEqual(first.error_code, "kafka_unavailable")
        self.assertEqual(second.attempts, 0)
        self.assertEqual(producer.send.call_count, 1)
        producer.send.return_value.get.side_effect = None
        self.assertEqual(publish_pending(lambda **kw: producer), 2)
        first.refresh_from_db()
        self.assertIsNotNone(first.published_at)

    def test_invalid_payload_is_not_reported_as_kafka_failure_or_skipped(self):
        first = self.transition("blocked")
        second = self.transition("clear")
        OutboxMessage.objects.filter(pk=first.pk).update(payload={"bad": "synthetic"})
        factory = MagicMock()
        self.assertEqual(publish_pending(factory), 0)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.error_code, "invalid_payload")
        self.assertEqual(first.attempts, 1)
        self.assertIsNone(first.published_at)
        self.assertEqual(second.attempts, 0)
        factory.assert_not_called()

    def test_serialization_failure_is_invalid_payload(self):
        first = self.transition("blocked")
        with patch("bookings.kafka_pipeline.json.dumps", side_effect=TypeError("synthetic")):
            self.assertEqual(publish_pending(MagicMock()), 0)
        first.refresh_from_db()
        self.assertEqual(first.error_code, "invalid_payload")
        self.assertIsNone(first.published_at)

    def test_unexpected_publisher_failure_has_own_code(self):
        first = self.transition("blocked")
        self.assertEqual(publish_pending(MagicMock(side_effect=RuntimeError("synthetic"))), 0)
        first.refresh_from_db()
        self.assertEqual(first.error_code, "publisher_error")
        self.assertIsNone(first.published_at)

    def test_ack_then_database_failure_replays_same_event_id(self):
        first = self.transition("blocked")
        producer = MagicMock()
        with patch("django.utils.timezone.now", side_effect=RuntimeError("db commit window")):
            with self.assertRaises(RuntimeError):
                publish_pending(lambda **kw: producer)
        first.refresh_from_db()
        self.assertIsNone(first.published_at)
        publish_pending(lambda **kw: producer)
        sent = [json.loads(call.kwargs["value"]) for call in producer.send.call_args_list]
        self.assertEqual([item["event_id"] for item in sent], [str(first.event_id)] * 2)

    def test_concurrent_cancellation_is_seen_before_notification(self):
        message = self.transition("blocked")
        started = Event()
        backend_pids = []

        def consume():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pids.append(cursor.fetchone()[0])
                started.set()
                return self.process(message).result
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                booking = Booking.objects.select_for_update().get(pk=self.booking.pk)
                future = executor.submit(consume)
                self.assertTrue(started.wait(5))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_stat_clear_snapshot()")
                        cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", backend_pids)
                        state = cursor.fetchone()
                    if state and state[0] == "Lock":
                        break
                    time.sleep(0.05)
                else:
                    self.fail("Consumer did not wait for the booking row lock")
                booking.status = "cancelled"
                booking.save(update_fields=["status"])
            self.assertEqual(future.result(timeout=10), "suppressed")

    def test_advisory_lock_allows_only_one_publisher(self):
        self.transition("blocked")
        entered, release = Event(), Event()
        producer = MagicMock()

        def wait_for_ack(timeout):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test failed to release producer")

        producer.send.return_value.get.side_effect = wait_for_ack

        def publish():
            close_old_connections()
            try:
                return publish_pending(lambda **kw: producer)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(publish)
            self.assertTrue(entered.wait(10))
            try:
                self.assertEqual(publish_pending(lambda **kw: producer), 0)
                self.assertEqual(producer.send.call_count, 1)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=10), 1)


@skipUnless(os.environ.get("KAFKA_INTEGRATION") == "1", "Kafka integration explicitly enabled only")
class KafkaIntegrationTests(PipelineFixture, TransactionTestCase):
    def test_invalid_record_is_redelivered_and_blocks_later_message(self):
        address = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
        group = "test-poison-" + uuid.uuid4().hex
        poison_id = str(uuid.uuid4())
        producer = KafkaProducer(bootstrap_servers=address, acks="all")
        try:
            poison = json.dumps({"event_id": poison_id}).encode()
            producer.send(TOPIC, key=b"poison", value=poison).get(timeout=10)
        finally:
            producer.close()
        valid = self.transition("blocked")
        self.assertEqual(publish_pending(), 1)

        def connect():
            return KafkaConsumer(TOPIC, bootstrap_servers=address, group_id=group,
                                 enable_auto_commit=False, auto_offset_reset="earliest", max_poll_records=1)

        def find_poison(consumer):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                for records in consumer.poll(timeout_ms=1000, max_records=1).values():
                    for record in records:
                        if json.loads(record.value).get("event_id") == poison_id:
                            return record
                        consumer.commit()
            self.fail("Poison record was not received")

        consumer = connect()
        try:
            record = find_poison(consumer)
            with self.assertRaises(OutboxMessage.DoesNotExist):
                consume_one(consumer, record)
            consumer.close(autocommit=False)
            consumer = connect()
            again = find_poison(consumer)
            self.assertEqual((again.partition, again.offset), (record.partition, record.offset))
            self.assertFalse(SimulatedNotification.objects.filter(event=valid).exists())
        finally:
            consumer.close(autocommit=False)

    def test_forecast_outbox_real_broker_consumer_and_redelivery(self):
        address = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
        # New group ensures test messages can be read without touching production offsets.
        group = "test-" + uuid.uuid4().hex
        def connect():
            return KafkaConsumer(TOPIC, bootstrap_servers=address, group_id=group,
                                 enable_auto_commit=False, auto_offset_reset="earliest", max_poll_records=1)
        consumer = connect()
        try:
            start_local = self.booking.starts_at.astimezone(MOSCOW)
            rain = weather_forecast(timezone.now(), days=3, changes={start_local: 61})
            with patch("bookings.weather_scheduler.fetch_automatic_forecast", return_value=AutomaticForecastResult(rain)):
                self.assertEqual(run_weather_check()["events"], 1)
            message = OutboxMessage.objects.get()
            self.assertEqual(publish_pending(), 1)
            message.refresh_from_db()
            self.assertIsNotNone(message.published_at)
            deadline = time.monotonic() + 30
            found = None
            while time.monotonic() < deadline and found is None:
                for records in consumer.poll(timeout_ms=1000, max_records=1).values():
                    for record in records:
                        if json.loads(record.value).get("event_id") == str(message.event_id):
                            found = record
                            break
                        consumer.commit()
            self.assertIsNotNone(found, "Kafka did not deliver event")
            # Simulate a crash after DB commit and before offset commit.
            process_notification(found.value, found.key)
            consumer.close(autocommit=False)
            consumer = connect()
            replay = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and replay is None:
                for records in consumer.poll(timeout_ms=1000, max_records=1).values():
                    for record in records:
                        if json.loads(record.value).get("event_id") == str(message.event_id):
                            replay = record
                            break
                        consumer.commit()
            self.assertIsNotNone(replay, "Uncommitted Kafka record was not redelivered")
            consume_one(consumer, replay)
            self.assertEqual(SimulatedNotification.objects.filter(event=message, result="simulated").count(), 1)
            self.assertNotIn("phone", json.loads(found.value))
        finally:
            consumer.close(autocommit=False)
