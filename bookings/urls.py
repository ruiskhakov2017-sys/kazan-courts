from django.urls import path

from . import api

app_name = "api"

urlpatterns = [
    path("courts/", api.courts, name="courts"),
    path("customers/", api.customers, name="customers"),
    path("schedule/", api.schedule, name="schedule"),
    path("bookings/", api.create, name="create_booking"),
    path("bookings/<int:booking_id>/reschedule/", api.reschedule, name="reschedule_booking"),
    path("bookings/<int:booking_id>/cancel/", api.cancel, name="cancel_booking"),
    path("bookings/<int:booking_id>/archive/", api.archive, name="archive_booking"),
    path("bookings/<int:booking_id>/history/", api.history, name="booking_history"),
]
