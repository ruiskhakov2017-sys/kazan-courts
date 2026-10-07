from datetime import datetime, time, timedelta

from bookings.weather_client import parse_forecast
from bookings.weather_policy import MOSCOW


def weather_payload(day, days=2, changes=None):
    start = datetime.combine(day, time.min, tzinfo=MOSCOW)
    stamps = [start + timedelta(hours=hour) for hour in range(days * 24)]
    changes = changes or {}
    return {"timezone": "Europe/Moscow", "hourly": {
        "time": [stamp.strftime("%Y-%m-%dT%H:%M") for stamp in stamps],
        "weather_code": [changes.get(stamp, 0) for stamp in stamps],
    }}


def weather_forecast(now, days=2, changes=None):
    return parse_forecast(weather_payload(now.astimezone(MOSCOW).date(), days, changes), now)
