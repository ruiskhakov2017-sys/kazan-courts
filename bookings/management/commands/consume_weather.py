import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from bookings.kafka_pipeline import consume_one, new_consumer

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Consume weather events and record simulated notifications."

    def handle(self, *args, **options):
        while True:
            consumer = None
            try:
                consumer = new_consumer()
                while True:
                    for records in consumer.poll(timeout_ms=1000, max_records=1).values():
                        for record in records:
                            consume_one(consumer, record)
                    close_old_connections()
            except Exception:
                # Do not commit a malformed or failed record; retry from committed offset.
                logger.exception("Consumer failed; retrying without acknowledging the failed event")
            finally:
                if consumer is not None:
                    consumer.close(autocommit=False)
                close_old_connections()
            time.sleep(5)
