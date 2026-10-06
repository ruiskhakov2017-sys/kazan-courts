import getpass
import warnings

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction


class Command(BaseCommand):
    help = "Interactively create the single employee admin without superuser privileges."

    @staticmethod
    def check_existing(user_model):
        if user_model.objects.filter(username="admin").exists():
            raise CommandError("admin уже существует. Пароль и права не изменены.")
        if user_model.objects.filter(is_active=True, is_staff=True).exists():
            raise CommandError("Активный сотрудник уже существует. Дополнительный не создан.")

    def handle(self, *args, **options):
        user_model = get_user_model()
        self.check_existing(user_model)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("Пароль сотрудника admin: ")
                confirmation = getpass.getpass("Повторите пароль: ")
        except (getpass.GetPassWarning, EOFError, KeyboardInterrupt) as error:
            raise CommandError("Нужен интерактивный терминал со скрытым вводом пароля.") from error
        if not password or password != confirmation:
            raise CommandError("Пароли должны быть непустыми и совпадать.")
        user = user_model(username="admin", is_active=True, is_staff=True, is_superuser=False)
        try:
            validate_password(password, user)
            user.set_password(password)
            user.full_clean()
            with transaction.atomic():
                self.check_existing(user_model)
                user.save()
        except (ValidationError, IntegrityError) as error:
            raise CommandError("Сотрудник не создан. Проверьте пароль и существующую учётную запись.") from error
        self.stdout.write(self.style.SUCCESS("Сотрудник admin создан. Пароль не выводится."))
