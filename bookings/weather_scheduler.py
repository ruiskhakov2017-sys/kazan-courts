"""One automatic check. The caller may schedule it hourly later."""

from django.db import connection
from django.utils import timezone

from .models import Booking, WeatherIntegrationStatus
from .weather_client import fetch_automatic_forecast
from .weather_health import record_automatic_result
from .weather_policy import blocks_for_interval, interval_status
from .weather_transitions import apply_weather_transition

# Fixed application-specific 64-bit session advisory lock, shared by all workers.
LOCK_KEY = 0x4B43574541544852


def run_weather_check():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
        acquired = cursor.fetchone()[0]
    if not acquired:
        return {"status": "already_running", "checked": 0, "events": 0}
    try:
        now = timezone.now()
        state = WeatherIntegrationStatus.objects.filter(pk=1).first()
        if state and state.next_allowed_at and state.next_allowed_at > now:
            return {"status": "deferred", "checked": 0, "events": 0, "next_allowed_at": state.next_allowed_at}
        result = fetch_automatic_forecast()
        if result.forecast is None:
            record_automatic_result(succeeded=False, attempted_at=now, error_code=result.error_code,
                                    next_allowed_at=result.next_allowed_at)
            return {"status": "unavailable", "checked": 0, "events": 0,
                    "retryable": result.retryable, "next_allowed_at": result.next_allowed_at}
        record_automatic_result(succeeded=True, attempted_at=now)
        forecast = result.forecast
        rows = Booking.objects.filter(status="active", archived_at__isnull=True, starts_at__gt=timezone.now(),
                                      court__court_type="outdoor").values_list("pk", "starts_at", "ends_at")
        checked = events = 0
        for pk, start, end in rows.iterator():
            status = interval_status(blocks_for_interval(forecast, start, end))
            if status == "unknown":
                continue
            checked += 1
            if apply_weather_transition(booking_id=pk, expected_starts_at=start, expected_ends_at=end,
                                        status=status, checked_at=forecast.checked_at):
                events += 1
        return {"status": "success", "checked": checked, "events": events}
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])
