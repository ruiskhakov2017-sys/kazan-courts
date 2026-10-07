"""The employee screen for booking operations, history and weather."""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache


@never_cache
@login_required
def home(request):
    if not request.user.is_active or not request.user.is_staff:
        return HttpResponseForbidden("Доступ разрешён только сотруднику.")
    return render(
        request, "home.html",
        {"initial_date": timezone.localdate(), "time_zone": settings.TIME_ZONE},
    )
