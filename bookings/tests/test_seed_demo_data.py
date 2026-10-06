from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from bookings.models import Booking, BookingEvent, Court, Customer


class DemoDataTests(TestCase):
    def seed(self):
        call_command("seed_demo_data", stdout=StringIO())

    def test_creates_only_seven_synthetic_courts_and_three_customers(self):
        self.seed()
        self.assertEqual(Court.objects.count(), 7)
        self.assertEqual(Court.objects.filter(court_type="indoor").count(), 3)
        self.assertEqual(Court.objects.filter(court_type="outdoor").count(), 4)
        self.assertEqual(Customer.objects.count(), 3)
        for court in Court.objects.all():
            court.full_clean()
            self.assertTrue(court.name.startswith("Демо "))
            self.assertEqual(court.surface_status, "available")
            self.assertIsNone(court.last_inspected_at)
            self.assertTrue(55 < court.latitude < 56)
            self.assertTrue(49 < court.longitude < 50)
        for customer in Customer.objects.all():
            customer.full_clean()
            self.assertTrue(customer.name.startswith("Демо "))
            self.assertTrue(customer.phone)
            self.assertEqual(customer.email, "")
        for model in (get_user_model(), Booking, BookingEvent):
            self.assertEqual(model.objects.count(), 0)

    def test_repeated_seed_keeps_the_same_records(self):
        self.seed()
        courts = list(Court.objects.order_by("pk").values())
        customers = list(Customer.objects.order_by("pk").values())
        self.seed()
        self.assertEqual(list(Court.objects.order_by("pk").values()), courts)
        self.assertEqual(list(Customer.objects.order_by("pk").values()), customers)

    def test_existing_changes_and_unrelated_records_are_preserved(self):
        self.seed()
        court = Court.objects.first()
        customer = Customer.objects.first()
        Court.objects.filter(pk=court.pk).update(surface_status="maintenance")
        Customer.objects.filter(pk=customer.pk).update(phone="DEMO-EDITED", email="demo@example.invalid")
        unrelated = Customer.objects.create(name="Other fictional customer", phone="DEMO-OTHER")
        self.seed()
        court.refresh_from_db()
        customer.refresh_from_db()
        self.assertEqual(court.surface_status, "maintenance")
        self.assertEqual(customer.phone, "DEMO-EDITED")
        self.assertEqual(customer.email, "demo@example.invalid")
        self.assertTrue(Customer.objects.filter(pk=unrelated.pk).exists())
        self.assertEqual(Court.objects.count(), 7)
        self.assertEqual(Customer.objects.count(), 4)

    def test_ambiguous_court_name_rolls_back_partial_seed(self):
        for _ in range(2):
            Court.objects.create(
                name="Демо открытый корт 4", court_type="outdoor",
                latitude="55.796000", longitude="49.126000",
            )
        with self.assertRaises(CommandError):
            self.seed()
        self.assertEqual(Court.objects.count(), 2)
        self.assertEqual(Customer.objects.count(), 0)

    def test_ambiguous_customer_name_rolls_back_courts_and_customers(self):
        for phone in ("DEMO-A", "DEMO-B"):
            Customer.objects.create(name="Демо клиент Тимур", phone=phone)
        with self.assertRaises(CommandError):
            self.seed()
        self.assertEqual(Court.objects.count(), 0)
        self.assertEqual(Customer.objects.count(), 2)
