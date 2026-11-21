"""Replay only unfinished notifications after restoring a DB to a fresh broker."""
import os
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from bookings.models import OutboxMessage


class Command(BaseCommand):
    help = 'Requeue outbox rows with no persisted notification, ONLY in restore-db recovery mode.'

    def handle(self, *args, **options):
        if os.environ.get('KAZAN_RECOVERY_MODE') != '1' or settings.DATABASES['default']['HOST'] != 'restore-db':
            raise CommandError('Requires explicitly isolated restore-db recovery mode')
        count = OutboxMessage.objects.filter(notification__isnull=True, published_at__isnull=False).update(published_at=None)
        self.stdout.write(f'Requeued unfinished restored notifications: {count}')
