"""Employee pages, session authentication and read-only API."""

from django.contrib.auth import views as auth_views
from django.urls import include, path

from bookings.forms import EmployeeAuthenticationForm

from .views import home

urlpatterns = [
    path("", home, name="home"),
    path(
        "login/",
        auth_views.LoginView.as_view(authentication_form=EmployeeAuthenticationForm),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("api/", include("bookings.urls")),
]
