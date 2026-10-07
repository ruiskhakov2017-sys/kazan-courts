"""Open-Meteo adapter. Live requests require an explicit local opt-in."""

import json
import re
from datetime import datetime
from types import MappingProxyType
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.utils import timezone

from .weather_policy import Forecast, MOSCOW

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
MAX_RESPONSE_BYTES = 1_048_576


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
