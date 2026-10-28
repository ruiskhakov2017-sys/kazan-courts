"""Open-Meteo adapter. Live requests require an explicit local opt-in."""

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from types import MappingProxyType
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.utils import timezone

from .weather_policy import Forecast, MOSCOW

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
MAX_RESPONSE_BYTES = 1_048_576
# Bound implausible server delays while preserving plausible multi-day limits.
MAX_RETRY_AFTER = timedelta(days=7)
logger = logging.getLogger(__name__)


def parse_forecast(payload, checked_at):
    if not isinstance(payload, dict) or payload.get("error") or payload.get("timezone") != "Europe/Moscow":
        raise ValueError("Invalid forecast metadata")
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("Missing hourly forecast")
    times, codes = hourly.get("time"), hourly.get("weather_code")
    if not isinstance(times, list) or not isinstance(codes, list) or len(times) != len(codes) or len(times) > 384:
        raise ValueError("Invalid hourly arrays")
    hours = {}
    for value, code in zip(times, codes):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:00", value):
            raise ValueError("Invalid hourly time")
        stamp = datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=MOSCOW)
        if stamp in hours:
            raise ValueError("Duplicate hourly time")
        # JSON numbers such as 61.0 still represent the exact WMO integer code.
        if type(code) is float and code.is_integer():
            code = int(code)
        hours[stamp] = code if type(code) is int else None
    return Forecast(MappingProxyType(hours), checked_at)


def fetch_forecast():
    checked_at = timezone.now()
    if not settings.WEATHER_NETWORK_ENABLED:
        return Forecast.unavailable(checked_at, "network_disabled")
    parameters = {
        "latitude": settings.WEATHER_LATITUDE, "longitude": settings.WEATHER_LONGITUDE,
        "hourly": "weather_code", "timezone": "Europe/Moscow", "forecast_days": 16,
    }
    try:
        with urlopen(ENDPOINT + "?" + urlencode(parameters), timeout=5) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Forecast response too large")
        def reject_constant(value):
            raise ValueError("Non-finite JSON number")
        payload = json.loads(body.decode("utf-8"), parse_constant=reject_constant)
        return parse_forecast(payload, checked_at)
    except (URLError, OSError, ValueError, UnicodeError):
        return Forecast.unavailable(checked_at, "forecast_unavailable")


@dataclass(frozen=True)
class AutomaticForecastResult:
    forecast: Forecast | None
    error_code: str = ""
    next_allowed_at: datetime | None = None


def _retry_after(value, now):
    if value is None:
        return None
    value = value.strip()
    maximum = now + MAX_RETRY_AFTER
    if value.isascii() and value.isdigit():
        try:
            # Avoid converting unbounded integers from an external header.
            seconds = value.lstrip("0") or "0"
            if len(seconds) > len(str(int(MAX_RETRY_AFTER.total_seconds()))):
                logger.warning("Open-Meteo Retry-After exceeds the seven-day safety limit")
                return maximum
            delay = int(seconds)
            if delay > int(MAX_RETRY_AFTER.total_seconds()):
                logger.warning("Open-Meteo Retry-After exceeds the seven-day safety limit")
            return now + timedelta(seconds=min(delay, int(MAX_RETRY_AFTER.total_seconds())))
        except (OverflowError, ValueError):
            logger.warning("Invalid Open-Meteo Retry-After; using seven-day safety limit")
            return maximum
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            raise ValueError("Missing timezone")
        if date > maximum:
            logger.warning("Open-Meteo Retry-After exceeds the seven-day safety limit")
        return min(max(now, date), maximum)
    except (TypeError, ValueError, OverflowError):
        # A malformed server delay is not silently ignored.
        logger.warning("Invalid Open-Meteo Retry-After; using one-hour safety delay")
        return now + timedelta(hours=1)


def fetch_automatic_forecast():
    """Bounded attempts; returned defer time must survive process restarts."""
    if not settings.WEATHER_NETWORK_ENABLED:
        return AutomaticForecastResult(None, "forecast_unavailable")
    parameters = {"latitude": settings.WEATHER_LATITUDE, "longitude": settings.WEATHER_LONGITUDE,
                  "hourly": "weather_code", "timezone": "Europe/Moscow", "forecast_days": 16}
    for attempt in range(3):
        try:
            with urlopen(ENDPOINT + "?" + urlencode(parameters), timeout=5) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("Forecast response too large")
            def reject_constant(value):
                raise ValueError("Non-finite JSON number")
            forecast = parse_forecast(json.loads(body.decode("utf-8"), parse_constant=reject_constant), timezone.now())
            return AutomaticForecastResult(forecast)
        except HTTPError as error:
            now = timezone.now()
            retryable = error.code in (429, 500, 502, 503, 504)
            retry_at = _retry_after(error.headers.get("Retry-After"), now) if retryable and error.headers else None
            code = "rate_limited" if error.code == 429 else "forecast_unavailable"
            if not retryable:
                return AutomaticForecastResult(None, code)
            if attempt == 2:
                return AutomaticForecastResult(None, code, retry_at)
            delay = (1, 3)[attempt]
            if retry_at and retry_at > now + timedelta(seconds=delay):
                return AutomaticForecastResult(None, code, retry_at)
        except (URLError, OSError):
            if attempt == 2:
                return AutomaticForecastResult(None, "forecast_unavailable")
            delay = (1, 3)[attempt]
        except (ValueError, UnicodeError):
            return AutomaticForecastResult(None, "invalid_response")
        time.sleep(delay)
    raise AssertionError("unreachable")
