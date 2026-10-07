"""Employee JSON endpoints for reads and booking operations."""

import json
import logging
from datetime import datetime, time, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.cache import never_cache

from .forms import BookingActionForm, BookingCreateForm, BookingRescheduleForm, EmptyActionForm, ScheduleQueryForm
from .models import Booking, Court, Customer
from .services import (
    BookingOperationError, archive_booking, booking_snapshot, cancel_booking,
    close_surface, confirm_surface, create_booking, recheck_weather, reschedule_booking,
)
from .weather_client import fetch_forecast
from .weather_policy import blocks_for_day, warnings_for_status

logger = logging.getLogger(__name__)


def error_response(code, message, status, **details):
    return JsonResponse({"error": {"code": code, "message": message, **details}}, status=status)


def employee_read(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            if not request.user.is_authenticated:
                return error_response("authentication_required", "Войдите в систему.", 401)
            if not request.user.is_active or not request.user.is_staff:
                return error_response("forbidden", "Доступ разрешён только сотруднику.", 403)
            if request.method != "GET":
                response = error_response("method_not_allowed", "Разрешено только чтение GET.", 405)
                response["Allow"] = "GET"
                return response
            return view(request, *args, **kwargs)
        except DatabaseError:
            logger.exception("Employee read failed for %s", request.path)
            return error_response("database_unavailable", "Не удалось загрузить данные. Повторите позже.", 503)

    return never_cache(wrapped)


@employee_read
def courts(request):
    rows = Court.objects.order_by("name", "pk").values(
        "id", "name", "court_type", "latitude", "longitude",
        "surface_status", "last_inspected_at",
    )
    return JsonResponse({"courts": list(rows)})


@employee_read
def customers(request):
    rows = Customer.objects.order_by("name", "pk").values("id", "name", "phone", "email")
    return JsonResponse({"customers": list(rows)})


@employee_read
def schedule(request):
    form = ScheduleQueryForm(request.GET)
    if not form.is_valid():
        fields = {name: list(errors) for name, errors in form.errors.items()}
        return error_response("invalid_query", "Проверьте корт и дату.", 400, fields=fields)
    court_id = form.cleaned_data["court_id"]
    day = form.cleaned_data["date"]
    if not Court.objects.filter(pk=court_id).exists():
        return error_response("court_not_found", "Корт не найден.", 404)
    start = datetime.combine(day, time.min, tzinfo=ZoneInfo(settings.TIME_ZONE))
    end = start + timedelta(days=1)
    rows = Booking.objects.filter(
        court_id=court_id, status=Booking.Status.ACTIVE,
        starts_at__lt=end, ends_at__gt=start,
    ).order_by("starts_at", "pk").values(
        "id", "court_id", "customer_id", "starts_at", "ends_at", "status", "weather_status", "weather_checked_at",
    )
    return JsonResponse({
        "court_id": court_id, "date": day.isoformat(), "time_zone": settings.TIME_ZONE,
        "bookings": list(rows),
    })


def employee_booking(methods):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                if not request.user.is_authenticated:
                    return error_response("authentication_required", "Войдите в систему.", 401)
                if not request.user.is_active or not request.user.is_staff:
                    return error_response("forbidden", "Доступ разрешён только сотруднику.", 403)
                if request.method not in methods:
                    response = error_response("method_not_allowed", "Метод не поддерживается.", 405)
                    response["Allow"] = ", ".join(methods)
                    return response
                return view(request, *args, **kwargs)
            except BookingOperationError as error:
                return error_response(error.code, str(error), error.status, fields=error.fields)
            except DatabaseError:
                logger.exception("Booking operation failed for %s", request.path)
                return error_response("database_unavailable", "Не удалось выполнить операцию. Повторите позже.", 503)
        return never_cache(wrapped)
    return decorate


def _json_form(request, form_class):
    if request.content_type != "application/json":
        raise BookingOperationError("invalid_payload", "Передайте JSON с параметрами действия.")
    def reject_constant(value):
        raise ValueError("Non-finite JSON number")
    try:
        payload = json.loads(request.body, parse_constant=reject_constant)
    except (ValueError, UnicodeDecodeError, RequestDataTooBig) as error:
        raise BookingOperationError("invalid_payload", "Не удалось прочитать JSON запроса.") from error
    if not isinstance(payload, dict):
        raise BookingOperationError("invalid_payload", "JSON должен содержать объект с параметрами действия.")
    form = form_class(payload)
    if not form.is_valid():
        fields = {name: list(errors) for name, errors in form.errors.items()}
        raise BookingOperationError("invalid_payload", "Проверьте параметры запроса.", fields=fields)
    return form.cleaned_data


def _write_booking(request, form_class, operation, *, booking_id=None, status=200):
    values = {"actor": request.user, **_json_form(request, form_class)}
    if booking_id is not None:
        values["booking_id"] = booking_id
    booking = operation(**values)
    return JsonResponse({"booking": {"id": booking.pk, **booking_snapshot(booking)},
                         "warnings": warnings_for_status(booking.weather_status)}, status=status)


@employee_booking(("GET", "POST"))
def create(request):
    if request.method == "POST":
        return _write_booking(request, BookingCreateForm, create_booking, status=201)
    form = ScheduleQueryForm(request.GET)
    if not form.is_valid():
        fields = {name: list(errors) for name, errors in form.errors.items()}
        return error_response("invalid_query", "Проверьте корт и дату.", 400, fields=fields)
    court_id, day = form.cleaned_data["court_id"], form.cleaned_data["date"]
    if not Court.objects.filter(pk=court_id).exists():
        return error_response("court_not_found", "Корт не найден.", 404)
    start = datetime.combine(day, time.min, tzinfo=ZoneInfo(settings.TIME_ZONE))
    end = start + timedelta(days=1)
    rows = Booking.objects.filter(
        court_id=court_id, archived_at__isnull=True, starts_at__lt=end, ends_at__gt=start,
    ).order_by("starts_at", "pk")
    return JsonResponse({
        "court_id": court_id, "date": day.isoformat(), "time_zone": settings.TIME_ZONE,
        "bookings": [{"id": booking.pk, **booking_snapshot(booking)} for booking in rows],
    })


@employee_booking(("POST",))
def reschedule(request, booking_id):
    return _write_booking(request, BookingRescheduleForm, reschedule_booking, booking_id=booking_id)


@employee_booking(("POST",))
def cancel(request, booking_id):
    return _write_booking(request, BookingActionForm, cancel_booking, booking_id=booking_id)


@employee_booking(("POST",))
def archive(request, booking_id):
    return _write_booking(request, BookingActionForm, archive_booking, booking_id=booking_id)


@employee_read
def history(request, booking_id):
    if not 1 <= booking_id <= 9223372036854775807:
        return error_response("booking_not_found", "Бронь не найдена.", 404)
    booking = Booking.objects.filter(pk=booking_id).first()
    if booking is None:
        return error_response("booking_not_found", "Бронь не найдена.", 404)
    events = booking.events.order_by("occurred_at", "pk").values(
        "id", "event_type", "actor_id", "occurred_at", "before", "after", "reason",
    )
    return JsonResponse({"booking_id": booking_id, "events": list(events)})


@employee_read
def weather(request):
    form = ScheduleQueryForm(request.GET)
    if not form.is_valid():
        fields = {name: list(errors) for name, errors in form.errors.items()}
        return error_response("invalid_query", "Проверьте корт и дату.", 400, fields=fields)
    court = Court.objects.filter(pk=form.cleaned_data["court_id"]).first()
    if court is None:
        return error_response("court_not_found", "Корт не найден.", 404)
    day = form.cleaned_data["date"]
    if court.court_type == Court.CourtType.INDOOR:
        return JsonResponse({"court_id": court.pk, "date": day.isoformat(), "status": "not_applicable",
                             "blocks": [], "checked_at": None, "warnings": []})
    forecast = fetch_forecast()
    blocks = blocks_for_day(forecast, day)
    return JsonResponse({"court_id": court.pk, "date": day.isoformat(), "status": "applicable",
                         "time_zone": "Europe/Moscow", "checked_at": forecast.checked_at.isoformat(),
                         "blocks": [block.as_dict() for block in blocks],
                         "warnings": warnings_for_status("unknown") if any(block.status == "unknown" for block in blocks) else []})


@employee_booking(("POST",))
def weather_recheck(request):
    _json_form(request, EmptyActionForm)
    return JsonResponse(recheck_weather(actor=request.user))


@employee_booking(("POST",))
def surface_close(request, court_id):
    _json_form(request, EmptyActionForm)
    return JsonResponse(close_surface(actor=request.user, court_id=court_id))


@employee_booking(("POST",))
def surface_ready(request, court_id):
    _json_form(request, EmptyActionForm)
    return JsonResponse(confirm_surface(actor=request.user, court_id=court_id))
