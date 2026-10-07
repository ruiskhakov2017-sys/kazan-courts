"""Booking writes and their history share a single short transaction."""

from contextlib import contextmanager
from datetime import UTC
from functools import wraps

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from .models import Booking, BookingEvent, Court, Customer
from .validators import validate_booking_times
from .weather_client import fetch_forecast
from .weather_policy import MOSCOW, blocks_for_day, blocks_for_interval, interval_status, warnings_for_status


class BookingOperationError(Exception):
    def __init__(self, code, message, status=400, fields=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.fields = fields or {}


def booking_snapshot(booking):
    def timestamp(value):
        return value.astimezone(UTC).isoformat() if value is not None else None
    return {
        "court_id": booking.court_id,
        "customer_id": booking.customer_id,
        "starts_at": timestamp(booking.starts_at),
        "ends_at": timestamp(booking.ends_at),
        "status": booking.status,
        "created_by_id": booking.created_by_id,
        "cancellation_reason": booking.cancellation_reason,
        "archived_at": timestamp(booking.archived_at),
        "created_at": timestamp(booking.created_at),
        "updated_at": timestamp(booking.updated_at),
        "weather_status": booking.weather_status,
        "weather_checked_at": timestamp(booking.weather_checked_at),
        "needs_weather_recheck": booking.status == Booking.Status.ACTIVE and booking.weather_status in ("unknown", "blocked"),
    }


def _has_active_conflict(*, court_id, starts_at, ends_at, exclude_booking_id=None):
    rows = Booking.objects.filter(
        court_id=court_id, status=Booking.Status.ACTIVE,
        starts_at__lt=ends_at, ends_at__gt=starts_at,
    )
    if exclude_booking_id is not None:
        rows = rows.exclude(pk=exclude_booking_id)
    return rows.exists()


def _time_conflict():
    return BookingOperationError("time_conflict", "Выбранное время уже занято. Обновите расписание.", 409)


@contextmanager
def _booking_transaction():
    try:
        with transaction.atomic():
            yield
    except ValidationError as error:
        raise BookingOperationError("invalid_booking", "Проверьте данные брони.", fields=error.message_dict) from error
    except IntegrityError as error:
        cause = error.__cause__
        if (
            getattr(cause, "sqlstate", None) == "23P01"
            and getattr(getattr(cause, "diag", None), "constraint_name", None) == "booking_active_no_overlap"
        ):
            raise _time_conflict() from error
        raise


def _retry_deadlock(operation):
    @wraps(operation)
    def wrapped(*args, **kwargs):
        for attempt in range(2):
            try:
                return operation(*args, **kwargs)
            except OperationalError as error:
                # The operation's atomic has rolled back before a complete retry.
                if attempt or getattr(error.__cause__, "sqlstate", None) != "40P01":
                    raise
    return wrapped


def _employee(actor):
    if not getattr(actor, "is_authenticated", False):
        raise BookingOperationError("authentication_required", "Войдите в систему.", 401)
    employee = get_user_model().objects.filter(pk=actor.pk, is_active=True, is_staff=True).first()
    if employee is None:
        raise BookingOperationError("forbidden", "Доступ разрешён только сотруднику.", 403)
    return employee


def _customer_with_phone(customer_id):
    try:
        customer = Customer.objects.get(pk=customer_id)
    except Customer.DoesNotExist as error:
        raise BookingOperationError("customer_not_found", "Клиент не найден.", 404) from error
    if not customer.phone or not customer.phone.strip():
        raise ValidationError({"customer_id": "У клиента должен быть указан телефон."})
    return customer


def _court(court_id):
    if not 1 <= court_id <= 9223372036854775807:
        raise BookingOperationError("court_not_found", "Корт не найден.", 404)
    try:
        return Court.objects.get(pk=court_id)
    except Court.DoesNotExist as error:
        raise BookingOperationError("court_not_found", "Корт не найден.", 404) from error


def _court_for_share(court_id):
    # PostgreSQL SHARE readers coexist; surface writers cannot change this row until commit.
    rows = list(Court.objects.raw("SELECT * FROM bookings_court WHERE id = %s FOR SHARE", [court_id]))
    if not rows:
        raise BookingOperationError("court_not_found", "Корт не найден.", 404)
    return rows[0]


def _court_for_surface_write(court_id):
    if not 1 <= court_id <= 9223372036854775807:
        raise BookingOperationError("court_not_found", "Корт не найден.", 404)
    try:
        court = Court.objects.select_for_update(no_key=True).get(pk=court_id)
    except Court.DoesNotExist as error:
        raise BookingOperationError("court_not_found", "Корт не найден.", 404) from error
    if court.court_type != Court.CourtType.OUTDOOR:
        raise BookingOperationError("invalid_court_type", "Действие доступно только для открытого корта.")
    return court


def _prepare_weather(court, starts_at, ends_at):
    try:
        validate_booking_times(starts_at=starts_at, ends_at=ends_at, court_type=court.court_type, now=timezone.now())
    except ValidationError as error:
        raise BookingOperationError("invalid_booking", "Проверьте данные брони.", fields=error.message_dict) from error
    return fetch_forecast() if court.court_type == Court.CourtType.OUTDOOR else None


def _booking_weather(court, forecast, start, end):
    if court.surface_status != Court.SurfaceStatus.AVAILABLE:
        raise BookingOperationError("surface_unavailable", "Покрытие корта закрыто: дождитесь проверки и подтверждения готовности.", 409)
    if court.court_type == Court.CourtType.INDOOR:
        return Booking.WeatherStatus.NOT_APPLICABLE, None
    if forecast is None:
        raise BookingOperationError("booking_context_changed", "Данные корта изменились. Повторите запрос.", 409)
    blocks = blocks_for_interval(forecast, start, end)
    status = interval_status(blocks)
    if status == "blocked":
        raise BookingOperationError("weather_conflict", "Интервал пересекает шестичасовой блок с дождём или моросью.", 409,
                                    fields={"starts_at": ["Выберите время вне погодного запрета."]})
    return status, forecast.checked_at


def _locked_booking(booking_id):
    if not 1 <= booking_id <= 9223372036854775807:
        raise BookingOperationError("booking_not_found", "Бронь не найдена.", 404)
    try:
        # No joined tables: only this Booking row is locked.
        return Booking.objects.select_for_update().get(pk=booking_id)
    except Booking.DoesNotExist as error:
        raise BookingOperationError("booking_not_found", "Бронь не найдена.", 404) from error


def _require_future_active(booking, now):
    if booking.status != Booking.Status.ACTIVE or booking.archived_at is not None:
        raise BookingOperationError("invalid_booking_state", "Изменять можно только действующую бронь.", 409)
    if booking.starts_at <= now:
        raise BookingOperationError("booking_already_started", "Бронь уже началась; перенос и отмена недоступны.", 409)


def _reason(value):
    if not isinstance(value, str):
        raise ValidationError({"reason": "Причина должна быть текстом."})
    return value.strip()


def _record_change(booking, employee, event_type, before, reason):
    BookingEvent.objects.create(
        booking=booking, actor=employee, event_type=event_type,
        before=before, after=booking_snapshot(booking), reason=reason,
        occurred_at=booking.updated_at,
    )


def create_booking(*, actor, customer_id, court_id, starts_at, ends_at):
    _employee(actor)
    court = _court(court_id)
    forecast = _prepare_weather(court, starts_at, ends_at)
    return _create_booking(actor=actor, customer_id=customer_id, court_id=court_id,
                           starts_at=starts_at, ends_at=ends_at, forecast=forecast)


@_retry_deadlock
def _create_booking(*, actor, customer_id, court_id, starts_at, ends_at, forecast):
    with _booking_transaction():
        employee = _employee(actor)
        customer = _customer_with_phone(customer_id)
        court = _court_for_share(court_id)
        start, end = validate_booking_times(
            starts_at=starts_at, ends_at=ends_at, court_type=court.court_type, now=timezone.now(),
        )
        if _has_active_conflict(court_id=court.pk, starts_at=start, ends_at=end):
            raise _time_conflict()
        weather_status, checked_at = _booking_weather(court, forecast, start, end)
        booking = Booking.objects.create(
            customer=customer, court=court, starts_at=start, ends_at=end,
            status=Booking.Status.ACTIVE, created_by=employee,
            weather_status=weather_status, weather_checked_at=checked_at,
        )
        BookingEvent.objects.create(
            booking=booking, actor=employee, event_type=BookingEvent.EventType.CREATED,
            before=None, after=booking_snapshot(booking), occurred_at=booking.created_at,
        )
    return booking


def reschedule_booking(*, actor, booking_id, starts_at, ends_at, reason=""):
    _employee(actor)
    if not 1 <= booking_id <= 9223372036854775807:
        raise BookingOperationError("booking_not_found", "Бронь не найдена.", 404)
    original = Booking.objects.filter(pk=booking_id).first()
    if original is None:
        raise BookingOperationError("booking_not_found", "Бронь не найдена.", 404)
    court = _court(original.court_id)
    forecast = _prepare_weather(court, starts_at, ends_at)
    return _reschedule_booking(actor=actor, booking_id=booking_id, court_id=court.pk,
                               starts_at=starts_at, ends_at=ends_at, reason=reason, forecast=forecast)


@_retry_deadlock
def _reschedule_booking(*, actor, booking_id, court_id, starts_at, ends_at, reason, forecast):
    with _booking_transaction():
        employee = _employee(actor)
        court = _court_for_share(court_id)
        booking = _locked_booking(booking_id)
        if booking.court_id != court.pk:
            raise BookingOperationError("booking_context_changed", "Корт брони изменился. Повторите запрос.", 409)
        now = timezone.now()
        _require_future_active(booking, now)
        reason = _reason(reason)
        _customer_with_phone(booking.customer_id)
        start, end = validate_booking_times(
            starts_at=starts_at, ends_at=ends_at, court_type=court.court_type, now=now,
        )
        if (start, end) == (booking.starts_at, booking.ends_at):
            raise BookingOperationError("no_change", "Выбрано прежнее время брони.", 400)
        if _has_active_conflict(court_id=court.pk, starts_at=start, ends_at=end, exclude_booking_id=booking.pk):
            raise _time_conflict()
        weather_status, checked_at = _booking_weather(court, forecast, start, end)
        before = booking_snapshot(booking)
        booking.starts_at, booking.ends_at = start, end
        booking.weather_status, booking.weather_checked_at = weather_status, checked_at
        booking.save(update_fields=["starts_at", "ends_at", "weather_status", "weather_checked_at", "updated_at"])
        _record_change(booking, employee, BookingEvent.EventType.RESCHEDULED, before, reason)
    return booking


@_retry_deadlock
def cancel_booking(*, actor, booking_id, reason=""):
    with _booking_transaction():
        employee = _employee(actor)
        booking = _locked_booking(booking_id)
        now = timezone.now()
        _require_future_active(booking, now)
        reason = _reason(reason)
        before = booking_snapshot(booking)
        booking.status = Booking.Status.CANCELLED
        booking.cancellation_reason = reason
        booking.save(update_fields=["status", "cancellation_reason", "updated_at"])
        _record_change(booking, employee, BookingEvent.EventType.CANCELLED, before, reason)
    return booking


@_retry_deadlock
def archive_booking(*, actor, booking_id, reason=""):
    with _booking_transaction():
        employee = _employee(actor)
        booking = _locked_booking(booking_id)
        now = timezone.now()
        if booking.status != Booking.Status.CANCELLED or booking.archived_at is not None:
            raise BookingOperationError("invalid_booking_state", "Архивировать можно только ещё не архивированную отменённую бронь.", 409)
        reason = _reason(reason)
        before = booking_snapshot(booking)
        booking.archived_at = now
        booking.save(update_fields=["archived_at", "updated_at"])
        _record_change(booking, employee, BookingEvent.EventType.ARCHIVED, before, reason)
    return booking


def court_snapshot(court):
    return {"id": court.pk, "surface_status": court.surface_status,
            "last_inspected_at": court.last_inspected_at.isoformat() if court.last_inspected_at else None}


@_retry_deadlock
def close_surface(*, actor, court_id):
    with _booking_transaction():
        _employee(actor)
        court = _court_for_surface_write(court_id)
        if court.surface_status != Court.SurfaceStatus.AVAILABLE:
            raise BookingOperationError("invalid_surface_state", "Корт уже закрыт на просушку или обслуживание.", 409)
        court.surface_status = Court.SurfaceStatus.DRYING
        court.save(update_fields=["surface_status"])
    return {"court": court_snapshot(court), "warnings": []}


def confirm_surface(*, actor, court_id):
    _employee(actor)
    court = _court(court_id)
    if court.court_type != Court.CourtType.OUTDOOR:
        raise BookingOperationError("invalid_court_type", "Действие доступно только для открытого корта.")
    forecast = fetch_forecast()
    return _confirm_surface(actor=actor, court_id=court_id, forecast=forecast)


@_retry_deadlock
def _confirm_surface(*, actor, court_id, forecast):
    with _booking_transaction():
        _employee(actor)
        court = _court_for_surface_write(court_id)
        now = timezone.now()
        if court.surface_status != Court.SurfaceStatus.DRYING:
            raise BookingOperationError("invalid_surface_state", "Подтвердить готовность можно после просушки; обслуживание требует отдельного решения.", 409)
        block = next(block for block in blocks_for_day(forecast, now.astimezone(MOSCOW).date())
                     if block.starts_at <= now < block.ends_at)
        # Physical readiness does not lift the independent weather ban.
        court.surface_status = Court.SurfaceStatus.AVAILABLE
        court.last_inspected_at = now
        court.save(update_fields=["surface_status", "last_inspected_at"])
    return {"court": court_snapshot(court), "warnings": warnings_for_status(block.status)}


def recheck_weather(*, actor):
    _employee(actor)
    forecast = fetch_forecast()
    return _recheck_weather(actor=actor, forecast=forecast)


@_retry_deadlock
def _recheck_weather(*, actor, forecast):
    with _booking_transaction():
        employee = _employee(actor)
        courts = {pk: _court_for_share(pk) for pk in Court.objects.filter(court_type="outdoor").order_by("pk").values_list("pk", flat=True)}
        rows = list(Booking.objects.select_for_update().filter(
            court_id__in=courts, status="active", archived_at__isnull=True, starts_at__gt=timezone.now(),
        ).order_by("pk"))
        now = timezone.now()
        affected = []
        checked_count = 0
        for booking in rows:
            if booking.starts_at <= now:
                continue
            blocks = blocks_for_interval(forecast, booking.starts_at, booking.ends_at)
            before = booking_snapshot(booking)
            booking.weather_status = interval_status(blocks)
            booking.weather_checked_at = forecast.checked_at
            booking.save(update_fields=["weather_status", "weather_checked_at", "updated_at"])
            _record_change(booking, employee, BookingEvent.EventType.WEATHER_CHECKED, before, "Ручная перепроверка прогноза")
            checked_count += 1
            if booking.weather_status in ("blocked", "unknown"):
                customer = _customer_with_phone(booking.customer_id)
                affected.append({"booking": {"id": booking.pk, **booking_snapshot(booking)},
                                 "court_name": courts[booking.court_id].name,
                                 "customer": {"id": customer.pk, "name": customer.name, "phone": customer.phone},
                                 "blocks": [block.as_dict() for block in blocks if block.status != "clear"],
                                 "warnings": warnings_for_status(booking.weather_status)})
    return {"checked_count": checked_count, "affected_bookings": affected,
            "checked_at": forecast.checked_at.isoformat()}
