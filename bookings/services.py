"""Booking writes and their history share a single short transaction."""

from datetime import UTC

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Booking, BookingEvent, Court, Customer
from .validators import validate_booking_times


class BookingCreationError(Exception):
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


def _has_active_conflict(*, court_id, starts_at, ends_at):
    return Booking.objects.filter(
        court_id=court_id, status=Booking.Status.ACTIVE,
        starts_at__lt=ends_at, ends_at__gt=starts_at,
    ).exists()


def _time_conflict():
    return BookingCreationError("time_conflict", "Выбранное время уже занято. Обновите расписание.", 409)


def create_booking(*, actor, customer_id, court_id, starts_at, ends_at):
    if not getattr(actor, "is_authenticated", False):
        raise BookingCreationError("authentication_required", "Войдите в систему.", 401)
    try:
        with transaction.atomic():
            employee = get_user_model().objects.filter(pk=actor.pk, is_active=True, is_staff=True).first()
            if employee is None:
                raise BookingCreationError("forbidden", "Доступ разрешён только сотруднику.", 403)
            try:
                customer = Customer.objects.get(pk=customer_id)
            except Customer.DoesNotExist as error:
                raise BookingCreationError("customer_not_found", "Клиент не найден.", 404) from error
            if not customer.phone or not customer.phone.strip():
                raise ValidationError({"customer_id": "У клиента должен быть указан телефон."})
            try:
                court = Court.objects.get(pk=court_id)
            except Court.DoesNotExist as error:
                raise BookingCreationError("court_not_found", "Корт не найден.", 404) from error
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
    except ValidationError as error:
        raise BookingCreationError("invalid_booking", "Проверьте данные брони.", fields=error.message_dict) from error
    except IntegrityError as error:
        cause = error.__cause__
        if (
            getattr(cause, "sqlstate", None) == "23P01"
            and getattr(getattr(cause, "diag", None), "constraint_name", None) == "booking_active_no_overlap"
        ):
            raise _time_conflict() from error
        raise
    return booking
