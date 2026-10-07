import re
from datetime import date

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime


class EmployeeAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_staff:
            raise ValidationError("Вход разрешён только сотруднику.", code="not_employee")


class ScheduleQueryForm(forms.Form):
    court_id = forms.IntegerField(min_value=1, max_value=9223372036854775807)
    date = forms.DateField(input_formats=["%Y-%m-%d"])

    def clean_date(self):
        value = self.cleaned_data["date"]
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", self.data.get("date", "")):
            raise ValidationError("Укажите дату в формате YYYY-MM-DD.")
        if value == date.max:
            raise ValidationError("Для этой даты невозможно определить конец суток.")
        return value


class AwareDateTimeField(forms.Field):
    def to_python(self, value):
        if value in self.empty_values:
            return None
        pattern = (
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-5][0-9]"
            r"(?::[0-5][0-9](?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-5][0-9])"
        )
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValidationError("Укажите время ISO 8601 с часовым поясом.")
        try:
            result = parse_datetime(value)
        except ValueError:
            result = None
        if result is None or timezone.is_naive(result):
            raise ValidationError("Укажите корректное время с часовым поясом.")
        return result


class BookingCreateForm(forms.Form):
    customer_id = forms.IntegerField(min_value=1, max_value=9223372036854775807)
    court_id = forms.IntegerField(min_value=1, max_value=9223372036854775807)
    starts_at = AwareDateTimeField()
    ends_at = AwareDateTimeField()

    def clean(self):
        cleaned = super().clean()
        if set(self.data) - set(self.fields):
            raise ValidationError("Передавайте только customer_id, court_id, starts_at и ends_at.")
        for field in ("customer_id", "court_id"):
            value = self.data.get(field)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                self.add_error(field, "ID должен быть целым числом JSON.")
        return cleaned


class BookingActionForm(forms.Form):
    reason = forms.CharField(required=False)

    def clean(self):
        cleaned = super().clean()
        if set(self.data) - set(self.fields):
            raise ValidationError("Передавайте только поля выбранного действия.")
        if "reason" in self.data and not isinstance(self.data["reason"], str):
            self.add_error("reason", "Причина должна быть текстом.")
        return cleaned


class BookingRescheduleForm(BookingActionForm):
    starts_at = AwareDateTimeField()
    ends_at = AwareDateTimeField()
