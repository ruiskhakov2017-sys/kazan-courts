from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bookings", "0005_weather_outbox")]
    operations = [migrations.AddField(model_name="weatherintegrationstatus", name="next_allowed_at",
                                      field=models.DateTimeField(blank=True, null=True))]
