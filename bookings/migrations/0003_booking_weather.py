from django.db import migrations, models


def mark_existing_indoor_bookings(apps, schema_editor):
    Booking = apps.get_model("bookings", "Booking")
    Booking.objects.using(schema_editor.connection.alias).filter(court__court_type="indoor").update(
        weather_status="not_applicable",
    )


class Migration(migrations.Migration):
    dependencies = [("bookings", "0002_booking_no_overlap")]

    operations = [
        migrations.AddField(
            model_name="booking", name="weather_status",
            field=models.CharField(
                choices=[("not_applicable", "Не требуется"), ("clear", "Без дождевого запрета"),
                         ("unknown", "Не проверена"), ("blocked", "Погодный запрет")],
                default="unknown", max_length=14,
            ),
        ),
        migrations.AddField(
            model_name="booking", name="weather_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_indoor_bookings, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=models.Q(weather_status__in=["not_applicable", "clear", "unknown", "blocked"]),
                name="booking_weather_status_valid",
            ),
        ),
        migrations.RemoveConstraint(model_name="bookingevent", name="booking_event_type_valid"),
        migrations.AlterField(
            model_name="bookingevent", name="event_type",
            field=models.CharField(
                choices=[("created", "Создание"), ("rescheduled", "Перенос"), ("cancelled", "Отмена"),
                         ("archived", "Архивация"), ("weather_checked", "Погода перепроверена")],
                max_length=15,
            ),
        ),
        migrations.AddConstraint(
            model_name="bookingevent",
            constraint=models.CheckConstraint(
                condition=models.Q(event_type__in=["created", "rescheduled", "cancelled", "archived", "weather_checked"]),
                name="booking_event_type_valid",
            ),
        ),
    ]
