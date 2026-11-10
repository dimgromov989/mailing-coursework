"""End-to-end checks for ownership, scheduling, delivery and cache behavior."""
from datetime import timedelta
from smtplib import SMTPException
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from .metrics import get_overview, get_report
from .models import DeliveryAttempt, Mailing, Message, Recipient
from .services import MailingUnavailable, send_mailing


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class MailingTests(TestCase):
    """Exercise the same URLs and database writes as the browser."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("anna", "anna@example.com", email_confirmed=True)
        cls.other = User.objects.create_user("boris", "boris@example.com", email_confirmed=True)
        cls.manager = User.objects.create_user("manager", "manager@example.com", email_confirmed=True)
        call_command("grant_manager", "manager", verbosity=0)
        cls.message = Message.objects.create(owner=cls.owner, subject="Весеннее письмо", body="Здравствуйте!")
        cls.other_message = Message.objects.create(owner=cls.other, subject="Чужое письмо", body="Текст")
        cls.recipient = Recipient.objects.create(owner=cls.owner, full_name="Ирина", email="irina@example.com")
        cls.second = Recipient.objects.create(owner=cls.owner, full_name="Мария", email="maria@example.com")
        cls.foreign_recipient = Recipient.objects.create(
            owner=cls.other, full_name="Павел", email="pavel@example.com",
        )
        cls.now = timezone.now().replace(microsecond=0)
        cls.mailing = Mailing.objects.create(
            owner=cls.owner, message=cls.message,
            start_time=cls.now - timedelta(hours=1), end_time=cls.now + timedelta(hours=1),
        )
        cls.mailing.recipients.set([cls.recipient, cls.second])

    def setUp(self):
        cache.clear()
        self.client.force_login(self.owner)

    def url(self, action, item=None):
        return reverse(action, args=[item.pk] if item else [])

    def schedule(self, **changes):
        local = timezone.localtime(self.now)
        return {
            "start_time": (local + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"),
            "end_time": (local + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S"),
            "message": self.message.pk, "recipients": [self.recipient.pk],
            **changes,
        }

    def test_guest_home_and_protected_urls(self):
        self.client.logout()
        response = self.client.get(self.url("home"))
        self.assertContains(response, "Уникальные получатели")
        self.assertIn("max-age=10", response["Cache-Control"])
        self.assertIn("Cookie", response["Vary"])
        self.assertEqual(self.client.get(self.url("mailing-list")).status_code, 302)

    def test_schedule_boundaries_and_status_write_on_detail(self):
        self.assertEqual(self.mailing.status_at(self.mailing.start_time), Mailing.Status.RUNNING)
        self.assertEqual(self.mailing.status_at(self.mailing.end_time), Mailing.Status.RUNNING)
        response = self.client.get(self.url("mailing-detail", self.mailing))
        self.assertContains(response, "Запущена")
        self.assertIn("no-store", response["Cache-Control"])
        self.mailing.refresh_from_db()
        self.assertEqual(self.mailing.status, Mailing.Status.RUNNING)
        with self.assertNumQueries(0):
            self.mailing.update_status(self.now)
        with patch("mailings.models.timezone.now", return_value=self.mailing.end_time + timedelta(seconds=1)):
            self.assertContains(self.client.get(self.url("mailing-detail", self.mailing)), "Завершена")
        self.mailing.refresh_from_db()
        self.assertEqual(self.mailing.status, Mailing.Status.FINISHED)

    def test_create_update_and_delete_mailing(self):
        response = self.client.post(self.url("mailing-create"), self.schedule(), follow=True)
        self.assertContains(response, "Изменения сохранены.")
        item = Mailing.objects.exclude(pk=self.mailing.pk).get(owner=self.owner)
        self.assertEqual(item.status, Mailing.Status.CREATED)
        self.assertEqual(list(item.recipients.all()), [self.recipient])
        response = self.client.post(
            self.url("mailing-update", item), self.schedule(recipients=[self.second.pk]),
        )
        self.assertRedirects(response, self.url("mailing-list"))
        self.assertEqual(list(item.recipients.all()), [self.second])
        self.assertContains(self.client.get(self.url("mailing-delete", item)), "Удалить запись?")
        self.assertRedirects(self.client.post(self.url("mailing-delete", item)), self.url("mailing-list"))
        self.assertFalse(Mailing.objects.filter(pk=item.pk).exists())

    def test_form_rejects_past_reversed_and_missing_dates(self):
        valid = self.schedule()
        for data, field in [
            (self.schedule(start_time="2000-01-01T10:00"), "start_time"),
            (self.schedule(end_time=valid["start_time"]), "end_time"),
            (self.schedule(start_time=""), "start_time"),
        ]:
            with self.subTest(field=field, data=data):
                response = self.client.post(self.url("mailing-create"), data)
                self.assertIn(field, response.context["form"].errors)
        self.assertEqual(Mailing.objects.count(), 1)

    def test_unchanged_past_start_allows_editing_current_mailing(self):
        response = self.client.post(self.url("mailing-update", self.mailing), self.schedule(
            start_time=timezone.localtime(self.mailing.start_time).isoformat(timespec="seconds"),
        ))
        self.assertRedirects(response, self.url("mailing-list"))

    def test_model_validates_schedule_independently(self):
        with self.assertRaises(ValidationError) as error:
            Mailing(
                owner=self.owner, message=self.message,
                start_time=self.now, end_time=self.now - timedelta(days=1),
            ).full_clean()
        self.assertIn("end_time", error.exception.message_dict)

    def test_other_users_cannot_attach_foreign_objects(self):
        response = self.client.post(self.url("mailing-create"), self.schedule(
            message=self.other_message.pk, recipients=[self.foreign_recipient.pk],
        ))
        self.assertEqual(set(response.context["form"].errors), {"message", "recipients"})

    def test_contacts_crud_and_case_insensitive_uniqueness(self):
        data = {"full_name": "Алексей", "email": "Alex@example.com", "comment": "Тест"}
        self.assertRedirects(self.client.post(self.url("recipient-create"), data), self.url("recipient-list"))
        item = Recipient.objects.get(email="alex@example.com")
        self.assertContains(self.client.get(self.url("recipient-detail", item)), "Алексей")
        response = self.client.post(self.url("recipient-create"), {**data, "email": "ALEX@example.com"})
        self.assertIn("email", response.context["form"].errors)
        self.assertRedirects(
            self.client.post(self.url("recipient-update", item), {**data, "full_name": "Алексей П."}),
            self.url("recipient-list"),
        )
        item.refresh_from_db()
        self.assertEqual(item.full_name, "Алексей П.")
        self.client.post(self.url("recipient-delete", item))
        self.assertFalse(Recipient.objects.filter(pk=item.pk).exists())

    def test_letter_crud_and_referenced_letter_is_protected(self):
        data = {"subject": "Новое письмо", "body": "Текст"}
        self.assertRedirects(self.client.post(self.url("message-create"), data), self.url("message-list"))
        item = Message.objects.get(subject=data["subject"])
        self.assertContains(self.client.get(self.url("message-detail", item)), "Текст")
        self.client.post(self.url("message-update", item), {**data, "body": "Новый текст"})
        item.refresh_from_db()
        self.assertEqual(item.body, "Новый текст")
        self.client.post(self.url("message-delete", item))
        self.assertFalse(Message.objects.filter(pk=item.pk).exists())
        self.assertContains(
            self.client.post(self.url("message-delete", self.message), follow=True),
            "Письмо используется в рассылке",
        )

    def test_manager_read_only_and_disable(self):
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(self.url("mailing-list")), self.message.subject)
        self.assertEqual(self.client.get(self.url("mailing-detail", self.mailing)).status_code, 404)
        self.assertEqual(self.client.get(self.url("recipient-detail", self.recipient)).status_code, 404)
        self.assertEqual(self.client.post(self.url("mailing-disable", self.mailing)).status_code, 403)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(self.url("mailing-list")), self.message.subject)
        self.assertContains(self.client.get(self.url("recipient-detail", self.recipient)), self.recipient.email)
        self.assertEqual(self.client.get(self.url("message-detail", self.message)).status_code, 404)
        self.assertEqual(self.client.get(self.url("mailing-update", self.mailing)).status_code, 404)
        self.assertEqual(self.client.post(self.url("recipient-delete", self.recipient)).status_code, 404)
        self.assertEqual(self.client.post(self.url("mailing-send", self.mailing)).status_code, 404)
        self.assertEqual(self.client.get(self.url("mailing-disable", self.mailing)).status_code, 405)
        self.assertContains(
            self.client.post(self.url("mailing-disable", self.mailing), follow=True), "Отключена менеджером",
        )
        self.client.force_login(self.owner)
        response = self.client.post(self.url("mailing-send", self.mailing), follow=True)
        self.assertContains(response, "Рассылка отключена менеджером.")
        self.assertFalse(DeliveryAttempt.objects.exists())

    def test_send_posts_one_message_per_recipient(self):
        self.assertEqual(self.client.get(self.url("mailing-send", self.mailing)).status_code, 405)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url("mailing-send", self.mailing), follow=True)
        self.assertContains(response, "Отправлено: 2. Ошибок: 0.")
        self.assertCountEqual([msg.to for msg in mail.outbox], [[self.recipient.email], [self.second.email]])
        self.assertEqual(DeliveryAttempt.objects.filter(status=DeliveryAttempt.Status.SUCCESS).count(), 2)

    def test_send_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.url("mailing-send", self.mailing)).status_code, 403)
        self.assertFalse(DeliveryAttempt.objects.exists())

    def test_batch_stores_smtp_failure_and_continues(self):
        with (
            patch("mailings.services.send_mail", side_effect=[SMTPException("550 rejected"), 1]),
            patch.object(DeliveryAttempt.objects, "bulk_create", wraps=DeliveryAttempt.objects.bulk_create) as batch,
        ):
            result = send_mailing(self.mailing)
        self.assertEqual((result.successful, result.failed), (1, 1))
        batch.assert_called_once()
        self.assertEqual(DeliveryAttempt.objects.get(status="failure").server_response, "550 rejected")

    def test_zero_from_backend_is_a_failure(self):
        with patch("mailings.services.send_mail", return_value=0):
            result = send_mailing(self.mailing)
        self.assertEqual(result.failed, 2)
        self.assertEqual(DeliveryAttempt.objects.filter(status="failure").count(), 2)

    def test_outside_window_is_rejected_in_ui_and_cli(self):
        for now in (self.mailing.start_time - timedelta(seconds=1), self.mailing.end_time + timedelta(seconds=1)):
            with (
                patch("mailings.services.timezone.now", return_value=now),
                patch("mailings.services.send_mail") as sender,
            ):
                self.assertContains(
                    self.client.post(self.url("mailing-send", self.mailing), follow=True),
                    "только в интервале рассылки",
                )
                with self.assertRaisesMessage(CommandError, "только в интервале рассылки"):
                    call_command("send_mailing", self.mailing.pk)
            sender.assert_not_called()
        self.assertFalse(DeliveryAttempt.objects.exists())

    def test_empty_and_foreign_audiences_are_rejected(self):
        self.mailing.recipients.clear()
        with self.assertRaisesMessage(MailingUnavailable, "нет получателей"):
            send_mailing(self.mailing)
        self.mailing.recipients.add(self.foreign_recipient)
        with self.assertRaisesMessage(MailingUnavailable, "принадлежать владельцу"):
            send_mailing(self.mailing)

    def test_cached_counters_and_invalidation_after_write(self):
        self.assertEqual(get_overview(), {"mailings": 1, "active": 1, "recipients": 3})
        with self.assertNumQueries(0):
            self.assertEqual(get_overview()["active"], 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.mailing.is_disabled = True
            self.mailing.save(update_fields=["is_disabled"])
        self.assertEqual(get_overview()["active"], 0)

    def test_overview_refreshes_at_scheduled_boundaries_even_with_cached_values(self):
        with patch("mailings.metrics.timezone.now", return_value=self.mailing.start_time - timedelta(seconds=1)):
            self.assertEqual(get_overview()["active"], 0)
        with patch("mailings.metrics.timezone.now", return_value=self.mailing.start_time):
            self.assertEqual(get_overview()["active"], 1)
        with patch("mailings.metrics.timezone.now", return_value=self.mailing.end_time):
            self.assertEqual(get_overview()["active"], 1)
        with patch("mailings.metrics.timezone.now", return_value=self.mailing.end_time + timedelta(microseconds=1)):
            self.assertEqual(get_overview()["active"], 0)

    def test_cached_reports_are_isolated_and_updated_after_delivery(self):
        self.assertEqual(get_report(self.owner.pk)["sent"], 0)
        with self.assertNumQueries(0):
            self.assertEqual(get_report(self.owner.pk)["sent"], 0)
        with self.captureOnCommitCallbacks(execute=True):
            send_mailing(self.mailing)
        response = self.client.get(self.url("report"))
        self.assertEqual(response.context["report"], {"successful": 2, "failed": 0, "attempts": 2, "sent": 2})
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url("report")).context["report"]["sent"], 0)
