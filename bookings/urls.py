from django.urls import path

from . import api

app_name = "api"

urlpatterns = [
    path("courts/", api.courts, name="courts"),
    path("customers/", api.customers, name="customers"),
    path("schedule/", api.schedule, name="schedule"),
]
