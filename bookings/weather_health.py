"""Persistent heartbeat for future automatic weather checks only."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import WeatherIntegrationStatus


SAFE_ERROR_CODES = frozenset({"forecast_unavailable", "timeout", "rate_limited", "invalid_response"})


@transaction.atomic
def record_automatic_result(*, succeeded, attempted_at, error_code=""):
    if not succeeded and error_code not in SAFE_ERROR_CODES:
        raise ValueError("Unsupported weather integration error code")
    status, _ = WeatherIntegrationStatus.objects.get_or_create(pk=1)
    status = WeatherIntegrationStatus.objects.select_for_update().get(pk=status.pk)
    # A slower older attempt must not overwrite a newer result.
    if status.last_attempt_at and attempted_at < status.last_attempt_at:
        return status
    status.last_attempt_at = attempted_at
    if succeeded:
        status.status = WeatherIntegrationStatus.Status.SUCCESS
        status.last_success_at = attempted_at
        status.error_code = ""
        status.consecutive_failures = 0
    else:
        status.status = WeatherIntegrationStatus.Status.ERROR
        status.error_code = error_code
        status.consecutive_failures += 1
    status.save()
    return status


def integration_health(*, now=None):
    now = now or timezone.now()
    row = WeatherIntegrationStatus.objects.filter(pk=1).first()
    if row is None or row.last_attempt_at is None:
        return {"state": "not_started", "last_attempt_at": None, "last_success_at": None}
    stale = row.last_success_at is not None and now - row.last_success_at > timedelta(hours=2)
    state = "error" if row.status == WeatherIntegrationStatus.Status.ERROR else "stale" if stale else "success"
    return {"state": state, "last_attempt_at": row.last_attempt_at.isoformat(),
            "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None}
