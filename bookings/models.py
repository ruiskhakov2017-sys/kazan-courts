from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.lookups import Exact
from django.utils import timezone


class Court(models.Model):
    class CourtType(models.TextChoices):
        INDOOR = "indoor", "Крытый"
        OUTDOOR = "outdoor", "Открытый"

    class SurfaceStatus(models.TextChoices):
        AVAILABLE = "available", "Доступен"
        DRYING = "drying", "Просушка"
        MAINTENANCE = "maintenance", "Обслуживание"

    name = models.CharField(max_length=128)
    court_type = models.CharField(max_length=7, choices=CourtType.choices)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    surface_status = models.CharField(
        max_length=11, choices=SurfaceStatus.choices, default=SurfaceStatus.AVAILABLE
    )
    last_inspected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(name__regex=r"\S"), name="court_name_not_blank"
            ),
            models.CheckConstraint(
                condition=models.Q(court_type__in=["indoor", "outdoor"]),
                name="court_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(surface_status__in=["available", "drying", "maintenance"]),
                name="court_surface_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(latitude__gte=-90, latitude__lte=90),
                name="court_latitude_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(longitude__gte=-180, longitude__lte=180),
                name="court_longitude_valid",
            ),
        ]

    def __str__(self):
        return self.name


class Customer(models.Model):
    name = models.CharField(max_length=128)
    phone = models.CharField(max_length=32)
    email = models.EmailField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(name__regex=r"\S"), name="customer_name_not_blank"
            ),
            models.CheckConstraint(
                condition=models.Q(phone__regex=r"\S"), name="customer_phone_not_blank"
            ),
        ]

    def __str__(self):
        return self.name


class Booking(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Действующая"
        CANCELLED = "cancelled", "Отменена"

    court = models.ForeignKey(Court, on_delete=models.PROTECT, related_name="bookings")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="bookings")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=9, choices=Status.choices, default=Status.ACTIVE)
    cancellation_reason = models.TextField(blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_bookings"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "cancelled"]),
                name="booking_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(archived_at__isnull=True) | models.Q(status="cancelled"),
                name="booking_archive_only_cancelled",
            ),
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="booking_end_after_start",
            ),
            models.CheckConstraint(
                condition=models.Q(ends_at__gte=models.F("starts_at") + timedelta(hours=1)),
                name="booking_minimum_one_hour",
            ),
            *[
                models.CheckConstraint(
                    condition=Exact(
                        models.F(field),
                        models.Func(
                            models.Value(timedelta(minutes=10)),
                            models.F(field),
                            models.Value(datetime(2000, 1, 1, tzinfo=UTC)),
                            function="DATE_BIN",
                            output_field=models.DateTimeField(),
                        ),
                    ),
                    name=f"booking_{field}_ten_minutes",
                )
                for field in ("starts_at", "ends_at")
            ],
            ExclusionConstraint(
                name="booking_active_no_overlap",
                expressions=[
                    ("court", RangeOperators.EQUAL),
                    (
                        models.Func(
                            "starts_at", "ends_at", models.Value("[)"),
                            function="TSTZRANGE", output_field=DateTimeRangeField(),
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=models.Q(status="active"),
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        for field in ("starts_at", "ends_at"):
            value = getattr(self, field)
            if isinstance(value, datetime):
                if timezone.is_naive(value):
                    errors[field] = "Укажите время с часовым поясом."
                elif value.minute % 10 or value.second or value.microsecond:
                    errors[field] = "Выберите время с шагом 10 минут без секунд."
        if not errors and isinstance(self.starts_at, datetime) and isinstance(self.ends_at, datetime):
            if self.ends_at - self.starts_at < timedelta(hours=1):
                errors["ends_at"] = "Бронирование должно длиться не менее 60 минут."
        if errors:
            raise ValidationError(errors)


class BookingEvent(models.Model):
    class EventType(models.TextChoices):
        CREATED = "created", "Создание"
        RESCHEDULED = "rescheduled", "Перенос"
        CANCELLED = "cancelled", "Отмена"
        ARCHIVED = "archived", "Архивация"

    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="booking_events"
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    event_type = models.CharField(max_length=11, choices=EventType.choices)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField()
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["occurred_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(event_type__in=["created", "rescheduled", "cancelled", "archived"]),
                name="booking_event_type_valid",
            ),
        ]
