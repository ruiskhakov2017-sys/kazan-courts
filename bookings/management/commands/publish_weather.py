import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from bookings.kafka_pipeline import publish_pending

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Publish pending weather outbox records in order."

    def handle(self, *args, **options):
        while True:
            try:
                publish_pending()
            except Exception:
                logger.exception("Publisher stopped this iteration; retrying")
            finally:
                close_old_connections()
            time.sleep(5)
