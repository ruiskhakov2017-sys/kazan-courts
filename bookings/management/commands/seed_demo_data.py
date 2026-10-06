from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bookings.models import Court, Customer


class Command(BaseCommand):
    help = "Create seven synthetic Kazan courts and three fictional customers."

    @transaction.atomic
    def handle(self, *args, **options):
        courts_created = 0
        customers_created = 0
        courts = [
            ("Демо крытый корт 1", "indoor", "55.790000", "49.120000"),
            ("Демо крытый корт 2", "indoor", "55.791000", "49.121000"),
            ("Демо крытый корт 3", "indoor", "55.792000", "49.122000"),
            ("Демо открытый корт 1", "outdoor", "55.793000", "49.123000"),
            ("Демо открытый корт 2", "outdoor", "55.794000", "49.124000"),
            ("Демо открытый корт 3", "outdoor", "55.795000", "49.125000"),
            ("Демо открытый корт 4", "outdoor", "55.796000", "49.126000"),
        ]
        for name, court_type, latitude, longitude in courts:
            courts_created += self.create_missing(
                Court, name, court_type=court_type, latitude=latitude, longitude=longitude
            )

        # Intentionally fictional numbers; do not call or send messages to them.
        for name, phone in (
            ("Демо клиент Алексей", "+70000000001"),
            ("Демо клиент Мария", "+70000000002"),
            ("Демо клиент Тимур", "+70000000003"),
        ):
            customers_created += self.create_missing(Customer, name, phone=phone)

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo data: {courts_created} courts and {customers_created} customers created."
            )
        )

    @staticmethod
    def create_missing(model, name, **fields):
        matches = list(model.objects.filter(name=name)[:2])
        if len(matches) > 1:
            raise CommandError(f"Ambiguous demo name for {model.__name__}: {name}")
        if matches:
            return 0
        instance = model(name=name, **fields)
        instance.full_clean()
        instance.save()
        return 1
