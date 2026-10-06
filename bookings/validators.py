"""Time rules for booking operations, evaluated with one server timestamp."""

from calendar import monthrange
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

MOSCOW = ZoneInfo(settings.TIME_ZONE)


def calendar_month_deadline(now):
    local = now.astimezone(MOSCOW)
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    return local.replace(year=year, month=month, day=min(local.day, monthrange(year, month)[1]))


def validate_booking_times(*, starts_at, ends_at, court_type, now):
    errors = {}
    values = {}
    for field, value in (("starts_at", starts_at), ("ends_at", ends_at)):
        if not isinstance(value, datetime) or timezone.is_naive(value):
            errors[field] = "Укажите дату и время с часовым поясом."
            continue
        try:
            values[field] = value.astimezone(MOSCOW)
        except (OverflowError, ValueError):
            errors[field] = "Дата выходит за поддерживаемый диапазон."
    if errors:
        raise ValidationError(errors)

    start, end = values["starts_at"], values["ends_at"]
    for field, value in values.items():
        if value.minute % 10 or value.second or value.microsecond:
            errors[field] = "Выберите время с шагом 10 минут без секунд."
    if end <= start:
        errors.setdefault("ends_at", "Окончание должно быть позже начала.")
    elif end - start < timedelta(hours=1):
        errors.setdefault("ends_at", "Бронирование должно длиться не менее 60 минут.")
    if start <= now:
        errors.setdefault("starts_at", "Начало брони должно быть в будущем.")
    deadline = calendar_month_deadline(now)
    for field, value in values.items():
        if value > deadline:
            errors.setdefault(field, "Время должно быть не более чем на календарный месяц вперёд.")
    if court_type == "outdoor":
        season_start = datetime(start.year, 5, 1, tzinfo=MOSCOW)
        season_end = datetime(start.year, 11, 1, tzinfo=MOSCOW)
        if start < season_start or start >= season_end:
            errors.setdefault("starts_at", "Открытый корт работает с 1 мая по 31 октября.")
        if end > season_end:
            errors.setdefault("ends_at", "Бронь должна закончиться не позже 1 ноября 00:00 по Казани.")
    if errors:
        raise ValidationError(errors)
    return start, end
