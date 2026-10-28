from django.core.management.base import BaseCommand, CommandError

from bookings.weather_scheduler import run_weather_check


class Command(BaseCommand):
    help = "Run one automatic weather check."

    def handle(self, *args, **options):
        result = run_weather_check()
        self.stdout.write(f"status={result['status']} checked={result['checked']} events={result['events']}")
        if result["status"] == "unavailable":
            raise CommandError("Automatic weather check unavailable")
