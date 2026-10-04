from django.db import connection
from django.test import TestCase


class PostgreSQLTestDatabaseTests(TestCase):
    def test_database_and_user(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_user;")
            database, user = cursor.fetchone()

        self.assertEqual(database, "test_kazan_courts")
        self.assertEqual(user, "kazan_test")
