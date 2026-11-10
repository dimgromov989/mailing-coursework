"""Send a campaign and persist the outcomes in a batch."""
from dataclasses import dataclass
from smtplib import SMTPException

from django.conf import settings
from django.core.mail import BadHeaderError, send_mail
from django.db import transaction
from django.utils import timezone

from .metrics import invalidate_report
from .models import DeliveryAttempt, Mailing


class MailingUnavailable(ValueError):
    """The mailing cannot be sent in its current state."""


@dataclass(frozen=True)
class DeliveryResult:
    """Counts returned to the caller after the batch is saved."""

    successful: int
    failed: int


def send_mailing(mailing):
    """Check schedule and ownership, send separately, and write attempt history."""
    mailing.refresh_from_db()
    now = timezone.now()
    if mailing.update_status(now) != Mailing.Status.RUNNING:
        raise MailingUnavailable("Отправлять письма можно только в интервале рассылки.")
    if mailing.is_disabled:
        raise MailingUnavailable("Рассылка отключена менеджером.")
    if not mailing.owner.is_active:
        raise MailingUnavailable("Владелец рассылки заблокирован.")
    if mailing.message.owner_id != mailing.owner_id:
        raise MailingUnavailable("Письмо должно принадлежать владельцу рассылки.")
    recipients = list(mailing.recipients.values_list("email", "owner_id"))
    if not recipients:
        raise MailingUnavailable("У рассылки нет получателей.")
    if any(owner_id != mailing.owner_id for _, owner_id in recipients):
        raise MailingUnavailable("Все получатели должны принадлежать владельцу рассылки.")

    attempts = []
    successful = 0
    for email, _ in recipients:
        attempt = deliver_to(mailing, email)
        successful += attempt.status == DeliveryAttempt.Status.SUCCESS
        attempts.append(attempt)
    with transaction.atomic():
        DeliveryAttempt.objects.bulk_create(attempts, batch_size=500)
        invalidate_report(mailing.owner_id)
    return DeliveryResult(successful, len(attempts) - successful)


def deliver_to(mailing, email):
    """Build an attempt for one recipient, keeping email addresses private."""
    attempt = DeliveryAttempt(mailing=mailing, recipient_email=email)
    try:
        count = send_mail(
            mailing.message.subject, mailing.message.body,
            settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False,
        )
    except (BadHeaderError, OSError, SMTPException) as error:
        attempt.status = DeliveryAttempt.Status.FAILURE
        attempt.server_response = str(error) or type(error).__name__
    else:
        if count == 1:
            attempt.status = DeliveryAttempt.Status.SUCCESS
            attempt.server_response = "Письмо принято почтовым сервером."
        else:
            attempt.status = DeliveryAttempt.Status.FAILURE
            attempt.server_response = "Почтовый сервер не принял письмо."
    return attempt
