import logging
import math
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import OperationalError, close_old_connections
from django.utils import timezone

from bookings.weather_scheduler import run_weather_check

logger = logging.getLogger(__name__)
HOURLY_SECONDS = 3600
RETRY_DELAYS = (60, 120, 300)


def _respect_defer(delay, next_allowed_at):
    if next_allowed_at:
        remaining = math.ceil((next_allowed_at - timezone.now()).total_seconds())
        # Wake at most hourly for long deferrals; the DB guard still prevents early requests.
        return max(delay, min(HOURLY_SECONDS, max(1, remaining)))
    return delay


class Command(BaseCommand):
    help = "Run the automatic weather check hourly when network access is enabled."

    def handle(self, *args, **options):
        failures = 0
        while True:
            delay = HOURLY_SECONDS
            if settings.WEATHER_NETWORK_ENABLED:
                try:
                    result = run_weather_check()
                    logger.warning("Weather cycle: %s", result)
                    if result["status"] == "unavailable" and result.get("retryable"):
                        failures += 1
                        if failures <= len(RETRY_DELAYS):
                            delay = RETRY_DELAYS[failures - 1]
                        else:
                            failures = 0
                        delay = _respect_defer(delay, result.get("next_allowed_at"))
                    elif result["status"] == "deferred":
                        delay = _respect_defer(1, result.get("next_allowed_at"))
                    elif result["status"] == "already_running":
                        delay = RETRY_DELAYS[0]
                    else:
                        failures = 0
                except (OperationalError, OSError):
                    failures += 1
                    delay = RETRY_DELAYS[failures - 1] if failures <= len(RETRY_DELAYS) else HOURLY_SECONDS
                    if failures > len(RETRY_DELAYS):
                        failures = 0
                    logger.exception("Weather cycle temporarily unavailable")
                except Exception:
                    failures = 0
                    logger.exception("Weather cycle failed")
                finally:
                    close_old_connections()
            else:
                logger.warning("Weather scheduler idle: network access disabled")
            time.sleep(delay)
