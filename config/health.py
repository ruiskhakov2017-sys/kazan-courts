"""Process liveness and dependency readiness; no business writes or live weather."""
from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from kafka import KafkaAdminClient
from kafka.errors import KafkaError


def postgres_available():
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            return cursor.fetchone() == (1,)
    except DatabaseError:
        connection.close()
        return False


def kafka_available():
    client = None
    try:
        client = KafkaAdminClient(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            client_id='kazancourts-readiness',
            request_timeout_ms=1500,
            bootstrap_timeout_ms=1500,
        )
        return bool(client.describe_cluster().get('brokers'))
    except (KafkaError, OSError):
        return False
    finally:
        if client is not None:
            client.close()


@never_cache
@require_safe
def live(request):
    return JsonResponse({'status': 'live'})


@never_cache
@require_safe
def ready(request):
    # Conservative full-system policy: DB and Kafka are both required. Outbox
    # lets bookings survive a broker outage; this probe does not block the API.
    checks = {'postgres': postgres_available(), 'kafka': kafka_available()}
    available = all(checks.values())
    return JsonResponse({'status': 'ready' if available else 'not_ready', 'checks': checks},
                        status=200 if available else 503)
