import getpass
import warnings
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from bookings.models import Booking, BookingEvent, Court, Customer


class CreateEmployeeCommandTests(TestCase):
    def create(self, passwords):
        output = StringIO()
        with patch("bookings.management.commands.create_employee.getpass.getpass", side_effect=passwords):
            call_command("create_employee", stdout=output)
        return output.getvalue()

    def test_creates_one_employee_with_hashed_password_and_no_domain_records(self):
        password = "Fictional-command-password-04"
        output = self.create([password, password])
        user = get_user_model().objects.get()
        self.assertEqual(user.username, "admin")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(password))
        self.assertNotEqual(user.password, password)
        self.assertNotIn(password, output)
        self.assertEqual([model.objects.count() for model in (Court, Customer, Booking, BookingEvent)], [0, 0, 0, 0])

    def test_repeated_creation_does_not_change_existing_password_or_flags(self):
        self.create(["Fictional-command-password-04"] * 2)
        before = list(get_user_model().objects.values())
        with patch("bookings.management.commands.create_employee.getpass.getpass") as prompt:
            with self.assertRaises(CommandError):
                call_command("create_employee", stdout=StringIO())
            prompt.assert_not_called()
        self.assertEqual(list(get_user_model().objects.values()), before)

    def test_existing_other_employee_prevents_additional_account(self):
        get_user_model().objects.create_user("existing-employee", is_staff=True)
        with self.assertRaises(CommandError):
            self.create(["unused", "unused"])
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_existing_admin_is_not_silently_promoted(self):
        get_user_model().objects.create_user("admin")
        with self.assertRaises(CommandError):
            self.create(["unused", "unused"])
        self.assertFalse(get_user_model().objects.get().is_staff)

    def test_empty_and_mismatched_passwords_create_nothing(self):
        for passwords in (["", ""], ["first", "second"]):
            with self.subTest(passwords=passwords):
                with self.assertRaises(CommandError):
                    self.create(passwords)
                self.assertEqual(get_user_model().objects.count(), 0)

    def test_terminal_without_hidden_input_fails_without_account(self):
        def unavailable_prompt(*args):
            warnings.warn("Hidden input unavailable", getpass.GetPassWarning)
        with patch("bookings.management.commands.create_employee.getpass.getpass", side_effect=unavailable_prompt):
            with self.assertRaises(CommandError):
                call_command("create_employee", stdout=StringIO())
        self.assertEqual(get_user_model().objects.count(), 0)
