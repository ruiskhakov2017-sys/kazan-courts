from django.urls import path

from . import api

app_name = "api"

urlpatterns = [
    path("courts/", api.courts, name="courts"),
    path("customers/", api.customers, name="customers"),
    path("schedule/", api.schedule, name="schedule"),
    path("weather/", api.weather, name="weather"),
    path("weather/recheck/", api.weather_recheck, name="weather_recheck"),
    path("courts/<int:court_id>/surface/close/", api.surface_close, name="surface_close"),
    path("courts/<int:court_id>/surface/ready/", api.surface_ready, name="surface_ready"),
    path("bookings/", api.create, name="create_booking"),
    path("bookings/<int:booking_id>/reschedule/", api.reschedule, name="reschedule_booking"),
    path("bookings/<int:booking_id>/cancel/", api.cancel, name="cancel_booking"),
    path("bookings/<int:booking_id>/archive/", api.archive, name="archive_booking"),
    path("bookings/<int:booking_id>/history/", api.history, name="booking_history"),
]
