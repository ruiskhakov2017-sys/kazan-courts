"""Pure six-hour weather policy; no network or database access."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")
RAIN_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82})
NO_RAIN_CODES = frozenset({0, 1, 2, 3, 45, 48})


@dataclass(frozen=True)
class Forecast:
    hours: Mapping[datetime, int | None]
    checked_at: datetime
    unavailable_reason: str = ""

    @classmethod
    def unavailable(cls, checked_at, reason):
        return cls(MappingProxyType({}), checked_at, reason)


@dataclass(frozen=True)
class WeatherBlock:
    starts_at: datetime
    ends_at: datetime
    status: str
    rain_codes: tuple

    def as_dict(self):
        return {"starts_at": self.starts_at.isoformat(), "ends_at": self.ends_at.isoformat(),
                "status": self.status, "rain_codes": list(self.rain_codes)}


def blocks_for_day(forecast, day):
    start = datetime.combine(day, time.min, tzinfo=MOSCOW)
    blocks = []
    for offset in (0, 6, 12, 18):
        begin = start + timedelta(hours=offset)
        codes = [forecast.hours.get(begin + timedelta(hours=hour)) for hour in range(6)]
        rainy = tuple(sorted({code for code in codes if type(code) is int and code in RAIN_CODES}))
        if rainy:
            status = "blocked"
        elif all(type(code) is int and code in NO_RAIN_CODES for code in codes):
            status = "clear"
        else:
            status = "unknown"
        blocks.append(WeatherBlock(begin, begin + timedelta(hours=6), status, rainy))
    return blocks


def blocks_for_interval(forecast, starts_at, ends_at):
    start, end = starts_at.astimezone(MOSCOW), ends_at.astimezone(MOSCOW)
    day = start.date()
    result = []
    while day <= (end - timedelta(microseconds=1)).date():
        result.extend(block for block in blocks_for_day(forecast, day)
                      if start < block.ends_at and end > block.starts_at)
        day += timedelta(days=1)
    return result


def interval_status(blocks):
    if any(block.status == "blocked" for block in blocks):
        return "blocked"
    if not blocks or any(block.status == "unknown" for block in blocks):
        return "unknown"
    return "clear"


def warnings_for_status(status):
    if status == "unknown":
        return ["Погода не проверена. Нужна последующая ручная перепроверка."]
    if status == "blocked":
        return ["Прогноз запрещает игру на открытом корте. Свяжитесь с клиентом для переноса или отмены."]
    return []
