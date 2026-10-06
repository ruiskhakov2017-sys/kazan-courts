from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from bookings.forms import BookingCreateForm
from bookings.validators import calendar_month_deadline, validate_booking_times

MOSCOW = ZoneInfo("Europe/Moscow")


class BookingTimeRuleTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 1, 12, tzinfo=MOSCOW)
        self.start = self.now + timedelta(days=1)

    def validate(self, **changes):
        values = {"starts_at": self.start, "ends_at": self.start + timedelta(hours=1),
                  "court_type": "indoor", "now": self.now}
        return validate_booking_times(**{**values, **changes})

    def test_calendar_month_clamps_day_and_preserves_local_time(self):
        for year, month, day, expected in (
            (2026, 1, 31, (2026, 2, 28)), (2024, 1, 31, (2024, 2, 29)),
            (2026, 5, 31, (2026, 6, 30)), (2026, 12, 31, (2027, 1, 31)),
            (2026, 1, 15, (2026, 2, 15)),
        ):
            with self.subTest(year=year, month=month, day=day):
                now = datetime(year, month, day, 14, 21, 37, 123456, tzinfo=MOSCOW)
                result = calendar_month_deadline(now.astimezone(UTC))
                self.assertEqual((result.year, result.month, result.day), expected)
                self.assertEqual((result.hour, result.minute, result.second, result.microsecond), (14, 21, 37, 123456))

    def test_future_is_strict_and_duration_is_at_least_one_hour(self):
        for start in (self.now - timedelta(hours=1), self.now):
            with self.subTest(start=start), self.assertRaises(ValidationError) as error:
                self.validate(starts_at=start, ends_at=start + timedelta(hours=1))
            self.assertIn("starts_at", error.exception.message_dict)
        for minutes in (-60, 0, 50):
            with self.subTest(minutes=minutes), self.assertRaises(ValidationError) as error:
                self.validate(ends_at=self.start + timedelta(minutes=minutes))
            self.assertIn("ends_at", error.exception.message_dict)
        self.validate()

    def test_both_boundaries_require_ten_minute_grid_without_rounding(self):
        for field in ("starts_at", "ends_at"):
            for offset in (timedelta(minutes=1), timedelta(seconds=1), timedelta(microseconds=1)):
                value = self.start + (timedelta(hours=2) if field == "ends_at" else timedelta()) + offset
                with self.subTest(field=field, offset=offset), self.assertRaises(ValidationError) as error:
                    self.validate(**{field: value})
                self.assertIn(field, error.exception.message_dict)

    def test_naive_and_out_of_range_datetimes_are_controlled_errors(self):
        for value in (self.start.replace(tzinfo=None), datetime.max.replace(tzinfo=UTC)):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.validate(starts_at=value)

    def test_time_grid_is_checked_after_moscow_normalization(self):
        start, end = self.validate(
            starts_at=self.start.astimezone(UTC), ends_at=(self.start + timedelta(hours=1)).astimezone(UTC),
        )
        self.assertEqual(start.tzinfo, MOSCOW)
        self.assertEqual(end - start, timedelta(hours=1))
        offset_time = datetime.fromisoformat("2026-07-02T12:00:00+05:45")
        with self.assertRaises(ValidationError):
            self.validate(starts_at=offset_time, ends_at=offset_time + timedelta(hours=1))

    def test_entire_interval_must_fit_in_inclusive_month_horizon(self):
        deadline = calendar_month_deadline(self.now)
        self.validate(starts_at=deadline - timedelta(hours=1), ends_at=deadline)
        for changes in (
            {"starts_at": deadline, "ends_at": deadline + timedelta(hours=1)},
            {"starts_at": deadline - timedelta(minutes=50), "ends_at": deadline + timedelta(minutes=10)},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError) as error:
                self.validate(**changes)
            self.assertIn("ends_at", error.exception.message_dict)

    def test_month_deadline_does_not_round_to_a_later_ten_minute_slot(self):
        now = self.now.replace(minute=3)
        deadline = calendar_month_deadline(now)
        self.validate(now=now, starts_at=deadline.replace(minute=0) - timedelta(hours=1), ends_at=deadline.replace(minute=0))
        with self.assertRaises(ValidationError):
            self.validate(now=now, starts_at=deadline.replace(minute=0), ends_at=deadline.replace(minute=10) + timedelta(hours=1))

    def test_outdoor_season_starts_at_may_first_midnight(self):
        now = datetime(2026, 4, 15, 12, tzinfo=MOSCOW)
        may = datetime(2026, 5, 1, tzinfo=MOSCOW)
        self.validate(now=now, court_type="outdoor", starts_at=may, ends_at=may + timedelta(hours=1))
        with self.assertRaises(ValidationError):
            self.validate(now=now, court_type="outdoor", starts_at=may - timedelta(hours=1), ends_at=may)

    def test_outdoor_booking_can_end_at_november_midnight_but_not_later(self):
        now = datetime(2026, 10, 15, 12, tzinfo=MOSCOW)
        november = datetime(2026, 11, 1, tzinfo=MOSCOW)
        self.validate(now=now, court_type="outdoor", starts_at=november - timedelta(hours=1), ends_at=november)
        for start, end in ((november - timedelta(minutes=50), november + timedelta(minutes=10)),
                           (november, november + timedelta(hours=1))):
            with self.subTest(start=start), self.assertRaises(ValidationError):
                self.validate(now=now, court_type="outdoor", starts_at=start, ends_at=end)

    def test_indoor_bookings_are_allowed_in_winter(self):
        now = datetime(2026, 12, 15, 12, tzinfo=MOSCOW)
        start = datetime(2027, 1, 1, tzinfo=MOSCOW)
        self.validate(now=now, starts_at=start, ends_at=start + timedelta(hours=1))

    def test_creation_form_requires_integer_ids_and_explicit_timezone(self):
        payload = {"customer_id": 1, "court_id": 1,
                   "starts_at": "2026-07-02T12:00:00+03:00", "ends_at": "2026-07-02T13:00:00+03:00"}
        self.assertTrue(BookingCreateForm(payload).is_valid())
        for field, value in (
            ("court_id", True), ("court_id", "1"), ("court_id", 1.0), ("court_id", []),
            ("customer_id", 0), ("customer_id", 9223372036854775808),
            ("starts_at", "2026-07-02T12:00:00"), ("starts_at", "2026-02-30T12:00:00+03:00"),
            ("starts_at", "2026-07-02T12:00:00.0000001+03:00"), ("starts_at", {}),
            ("starts_at", "2026-07-02T12:00:00+02:60"), ("starts_at", "2026-07-02T12:00:00+03:99"),
        ):
            with self.subTest(field=field, value=value):
                form = BookingCreateForm({**payload, field: value})
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)
        self.assertFalse(BookingCreateForm({**payload, "created_by_id": 999}).is_valid())
