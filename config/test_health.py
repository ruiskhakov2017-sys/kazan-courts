from unittest.mock import patch

from django.db import OperationalError
from django.test import SimpleTestCase
from kafka.errors import KafkaError

from .health import kafka_available, postgres_available


class HealthTests(SimpleTestCase):
    def test_live_never_touches_dependencies(self):
        with patch('config.health.postgres_available', side_effect=AssertionError), patch(
                'config.health.kafka_available', side_effect=AssertionError):
            response = self.client.get('/health/live')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'live'})
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_readiness_matrix_and_recovery(self):
        for pg, kafka in [(True, True), (False, True), (True, False), (False, False), (True, True)]:
            with self.subTest(postgres=pg, kafka=kafka), patch(
                    'config.health.postgres_available', return_value=pg), patch(
                    'config.health.kafka_available', return_value=kafka):
                response = self.client.get('/health/ready')
                self.assertEqual(response.status_code, 200 if pg and kafka else 503)
                self.assertEqual(response.json()['checks'], {'postgres': pg, 'kafka': kafka})
                self.assertIn('no-store', response.headers['Cache-Control'])

    def test_probes_are_read_only(self):
        for path in ['/health/live', '/health/ready']:
            self.assertEqual(self.client.post(path).status_code, 405)

    def test_postgres_failure_is_sanitized(self):
        with patch('config.health.connection') as db:
            db.cursor.side_effect = OperationalError('sensitive details')
            self.assertFalse(postgres_available())
            db.close.assert_called_once()

    def test_broker_failure_is_sanitized(self):
        with patch('config.health.KafkaAdminClient', side_effect=KafkaError):
            self.assertFalse(kafka_available())

    def test_broker_client_is_closed(self):
        with patch('config.health.KafkaAdminClient') as factory:
            client = factory.return_value
            client.describe_cluster.return_value = {'brokers': [{'node_id': 1}]}
            self.assertTrue(kafka_available())
            client.close.assert_called_once()
