"""Registration, reset and manager moderation flows."""
import re
from unittest.mock import patch
from urllib.parse import urlsplit

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User

PASSWORD = "swift-blue-letter_2753"
NEW_PASSWORD = "wooden-blue-mailbox_3951"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AccountTests(TestCase):
    """Use Django's test client and capture confirmation and reset emails."""

    def email_path(self):
        match = re.search(r"http://testserver/\S+", mail.outbox[-1].body)
        self.assertIsNotNone(match)
        return urlsplit(match.group()).path

    def test_signup_confirm_login_and_logout(self):
        data = {"username": "anna", "email": "Anna@Example.com", "password1": PASSWORD, "password2": PASSWORD}
        self.assertContains(self.client.post(reverse("signup"), data), "Проверьте почту")
        user = User.objects.get(email="anna@example.com")
        self.assertFalse(user.is_active)
        self.assertFalse(self.client.login(username="anna", password=PASSWORD))
        path = self.email_path()
        self.assertContains(self.client.get(path), "Подтвердите электронную почту")
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertRedirects(self.client.post(path), reverse("login"))
        self.assertEqual(self.client.post(path).status_code, 400)
        user.refresh_from_db()
        self.assertTrue(user.email_confirmed and user.is_active)
        self.assertRedirects(
            self.client.post(reverse("login"), {"username": "anna", "password": PASSWORD}), reverse("mailing-list"),
        )
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("logout")), reverse("home"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_signup_failure_does_not_leave_an_unusable_account(self):
        data = {"username": "anna", "email": "anna@example.com", "password1": PASSWORD, "password2": PASSWORD}
        with patch("accounts.views.send_mail", side_effect=OSError("offline")):
            response = self.client.post(reverse("signup"), data)
        self.assertContains(response, "Не удалось отправить письмо")
        self.assertFalse(User.objects.filter(username="anna").exists())

    def test_password_reset(self):
        User.objects.create_user("anna", "anna@example.com", PASSWORD, email_confirmed=True)
        self.assertRedirects(
            self.client.post(reverse("password_reset"), {"email": "anna@example.com"}), reverse("password_reset_done"),
        )
        confirmation = self.client.get(self.email_path(), follow=True)
        self.assertContains(confirmation, "Новый пароль")
        tokenless_path = confirmation.redirect_chain[-1][0]
        self.assertRedirects(self.client.post(tokenless_path, {
            "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD,
        }), reverse("password_reset_complete"))
        self.assertFalse(self.client.login(username="anna", password=PASSWORD))
        self.assertTrue(self.client.login(username="anna", password=NEW_PASSWORD))

    def test_only_manager_can_block_regular_user(self):
        target = User.objects.create_user("anna", "anna@example.com", PASSWORD, email_confirmed=True)
        manager = User.objects.create_user("manager", "manager@example.com", PASSWORD, email_confirmed=True)
        call_command("grant_manager", manager.username, verbosity=0)
        self.client.force_login(target)
        self.assertEqual(self.client.get(reverse("user-list")).status_code, 403)
        self.assertEqual(self.client.post(reverse("user-block", args=[manager.pk])).status_code, 403)
        self.client.force_login(manager)
        self.assertContains(self.client.get(reverse("user-list")), target.email)
        self.assertEqual(self.client.post(reverse("user-block", args=[manager.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("user-block", args=[target.pk])).status_code, 405)
        self.assertRedirects(self.client.post(reverse("user-block", args=[target.pk])), reverse("user-list"))
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertFalse(self.client.login(username="anna", password=PASSWORD))
