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
    try:
        return Court.objects.get(pk=court_id)
    except Court.DoesNotExist as error:
        raise BookingOperationError("court_not_found", "Корт не найден.", 404) from error


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


@_retry_deadlock
def create_booking(*, actor, customer_id, court_id, starts_at, ends_at):
    with _booking_transaction():
        employee = _employee(actor)
        customer = _customer_with_phone(customer_id)
        court = _court(court_id)
        start, end = validate_booking_times(
            starts_at=starts_at, ends_at=ends_at, court_type=court.court_type, now=timezone.now(),
        )
        if _has_active_conflict(court_id=court.pk, starts_at=start, ends_at=end):
            raise _time_conflict()
        booking = Booking.objects.create(
            customer=customer, court=court, starts_at=start, ends_at=end,
            status=Booking.Status.ACTIVE, created_by=employee,
        )
        BookingEvent.objects.create(
            booking=booking, actor=employee, event_type=BookingEvent.EventType.CREATED,
            before=None, after=booking_snapshot(booking), occurred_at=booking.created_at,
        )
    return booking


@_retry_deadlock
def reschedule_booking(*, actor, booking_id, starts_at, ends_at, reason=""):
    with _booking_transaction():
        employee = _employee(actor)
        booking = _locked_booking(booking_id)
        now = timezone.now()
        _require_future_active(booking, now)
        reason = _reason(reason)
        _customer_with_phone(booking.customer_id)
        court = _court(booking.court_id)
        start, end = validate_booking_times(
            starts_at=starts_at, ends_at=ends_at, court_type=court.court_type, now=now,
        )
        if (start, end) == (booking.starts_at, booking.ends_at):
            raise BookingOperationError("no_change", "Выбрано прежнее время брони.", 400)
        if _has_active_conflict(court_id=court.pk, starts_at=start, ends_at=end, exclude_booking_id=booking.pk):
            raise _time_conflict()
        before = booking_snapshot(booking)
        booking.starts_at, booking.ends_at = start, end
        booking.save(update_fields=["starts_at", "ends_at", "updated_at"])
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
