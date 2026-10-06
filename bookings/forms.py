import re
from datetime import date

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError


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
