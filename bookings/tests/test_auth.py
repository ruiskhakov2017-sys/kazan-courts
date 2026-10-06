from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase


class EmployeeAuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # These passwords exist only for isolated test fixtures.
        cls.password = "Fictional-test-password-04"
        cls.employee = get_user_model().objects.create_user("admin", password=cls.password, is_staff=True)
        cls.outsider = get_user_model().objects.create_user("outsider", password=cls.password)
        cls.inactive = get_user_model().objects.create_user("inactive", password=cls.password, is_staff=True, is_active=False)

    def test_anonymous_home_redirects_without_contact_data(self):
        response = self.client.get("/")
        self.assertRedirects(response, "/login/?next=/", fetch_redirect_response=False)
        self.assertNotContains(response, "customer-rows", status_code=302)
        self.assertIn("no-store", response["Cache-Control"])

    def test_login_page_is_public_and_contains_csrf(self):
        response = self.client.get("/login/")
        self.assertContains(response, "<h1>KazanCourts</h1>")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'type="password"')

    def test_successful_login_uses_session_and_protects_api(self):
        response = self.client.post("/login/", {"username": "admin", "password": self.password})
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.employee.pk)
        self.assertEqual(self.client.get("/api/courts/").status_code, 200)

    def test_wrong_password_does_not_create_authenticated_session(self):
        response = self.client.post("/login/", {"username": "admin", "password": "wrong-test-password"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/api/customers/").status_code, 401)

    def test_non_employee_and_inactive_accounts_cannot_log_in(self):
        for username in (self.outsider.username, self.inactive.username):
            with self.subTest(username=username):
                response = self.client.post("/login/", {"username": username, "password": self.password})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_employee_home_returns_200_and_static_asset_links(self):
        self.client.force_login(self.employee)
        response = self.client.get("/")
        self.assertContains(response, "<h1>KazanCourts</h1>")
        self.assertContains(response, 'id="schedule-filter"')
        self.assertContains(response, "/static/bookings/employee.js")
        self.assertContains(response, "/static/bookings/employee.css")
        self.assertContains(response, 'data-time-zone="Europe/Moscow"')
        self.assertIn("no-store", response["Cache-Control"])

    def test_default_date_is_moscow_date(self):
        self.client.force_login(self.employee)
        with patch("config.views.timezone.localdate", return_value=date(2026, 7, 1)):
            self.assertContains(self.client.get("/"), 'value="2026-07-01"')

    def test_logged_in_non_employee_cannot_open_home(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get("/").status_code, 403)

    def test_external_next_url_is_rejected(self):
        response = self.client.post("/login/", {
            "username": "admin", "password": self.password, "next": "https://example.invalid/",
        })
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_logout_clears_session_and_revokes_reads(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        response = self.client.post("/logout/")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/api/customers/").status_code, 401)

    def test_login_requires_csrf_and_sets_protected_session_cookie(self):
        client = Client(enforce_csrf_checks=True)
        client.get("/login/")
        self.assertEqual(client.post("/login/", {"username": "admin", "password": self.password}).status_code, 403)
        response = client.post("/login/", {
            "username": "admin", "password": self.password, "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(client.cookies["sessionid"]["httponly"])
        self.assertEqual(client.cookies["sessionid"]["samesite"], "Lax")

    def test_logout_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.employee)
        client.get("/")
        self.assertEqual(client.post("/logout/").status_code, 403)
        response = client.post("/logout/", {"csrfmiddlewaretoken": client.cookies["csrftoken"].value})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", client.session)
