import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bookings", "0004_weatherintegrationstatus"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.RemoveConstraint(model_name="bookingevent", name="booking_event_type_valid"),
        migrations.AlterField(model_name="bookingevent", name="actor", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="booking_events", to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name="bookingevent", name="event_type", field=models.CharField(choices=[("created", "Создание"), ("rescheduled", "Перенос"), ("cancelled", "Отмена"), ("archived", "Архивация"), ("weather_checked", "Погода перепроверена"), ("weather_blocked", "Погодный запрет"), ("weather_cleared", "Погодный запрет снят"), ("contact_no_answer", "Не дозвонились")], max_length=20)),
        migrations.AddField(model_name="bookingevent", name="weather_episode", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="episode_events", to="bookings.bookingevent")),
        migrations.AddConstraint(model_name="bookingevent", constraint=models.CheckConstraint(condition=models.Q(event_type__in=["created", "rescheduled", "cancelled", "archived", "weather_checked", "weather_blocked", "weather_cleared", "contact_no_answer"]), name="booking_event_type_valid")),
        migrations.AddConstraint(model_name="bookingevent", constraint=models.CheckConstraint(condition=(models.Q(event_type__in=["weather_blocked", "weather_cleared"], actor__isnull=True) | models.Q(event_type__in=["created", "rescheduled", "cancelled", "archived", "weather_checked", "contact_no_answer"], actor__isnull=False)), name="booking_event_actor_by_type")),
        migrations.CreateModel(name="OutboxMessage", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("event_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ("event_type", models.CharField(max_length=32)),
            ("schema_version", models.PositiveSmallIntegerField(default=1)),
            ("payload", models.JSONField()),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("published_at", models.DateTimeField(blank=True, null=True)),
            ("attempts", models.PositiveIntegerField(default=0)),
            ("error_code", models.CharField(blank=True, max_length=32)),
            ("booking_event", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="outbox_message", to="bookings.bookingevent")),
        ], options={"constraints": [models.CheckConstraint(condition=models.Q(event_type__in=["booking.weather_blocked", "booking.weather_cleared"]), name="outbox_weather_type_valid")]}),
        migrations.CreateModel(name="SimulatedNotification", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("result", models.CharField(choices=[("simulated", "Имитация"), ("suppressed", "Подавлено")], max_length=10)),
            ("text", models.TextField(blank=True)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("event", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="notification", to="bookings.outboxmessage")),
        ], options={"constraints": [models.CheckConstraint(condition=models.Q(result__in=["simulated", "suppressed"]), name="simulated_notification_result_valid")]}),
    ]
