"""URL routes for the foundation page."""

from django.urls import path

from .views import home

urlpatterns = [
    path("", home, name="home"),
]
