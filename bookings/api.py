"""Employee JSON endpoints for reads and booking creation."""

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

from .forms import BookingCreateForm, ScheduleQueryForm
from .models import Booking, Court, Customer
from .services import BookingCreationError, booking_snapshot, create_booking

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
        "id", "court_id", "customer_id", "starts_at", "ends_at", "status",
    )
    return JsonResponse({
        "court_id": court_id, "date": day.isoformat(), "time_zone": settings.TIME_ZONE,
        "bookings": list(rows),
    })


@never_cache
def create(request):
    try:
        if not request.user.is_authenticated:
            return error_response("authentication_required", "Войдите в систему.", 401)
        if not request.user.is_active or not request.user.is_staff:
            return error_response("forbidden", "Доступ разрешён только сотруднику.", 403)
        if request.method != "POST":
            response = error_response("method_not_allowed", "Разрешено только создание POST.", 405)
            response["Allow"] = "POST"
            return response
        if request.content_type != "application/json":
            return error_response("invalid_payload", "Передайте JSON с параметрами брони.", 400)
        def reject_constant(value):
            raise ValueError("Non-finite JSON number")
        try:
            payload = json.loads(request.body, parse_constant=reject_constant)
        except (ValueError, UnicodeDecodeError, RequestDataTooBig):
            return error_response("invalid_payload", "Не удалось прочитать JSON запроса.", 400)
        if not isinstance(payload, dict):
            return error_response("invalid_payload", "JSON должен содержать объект с параметрами брони.", 400)
        form = BookingCreateForm(payload)
        if not form.is_valid():
            fields = {name: list(errors) for name, errors in form.errors.items()}
            return error_response("invalid_payload", "Проверьте параметры запроса.", 400, fields=fields)
        try:
            booking = create_booking(actor=request.user, **form.cleaned_data)
        except BookingCreationError as error:
            return error_response(error.code, str(error), error.status, fields=error.fields)
        return JsonResponse({"booking": {"id": booking.pk, **booking_snapshot(booking)}}, status=201)
    except DatabaseError:
        logger.exception("Booking creation failed")
        return error_response("database_unavailable", "Не удалось сохранить бронь. Повторите позже.", 503)
