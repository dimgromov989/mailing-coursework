"""Persistence for recipients, messages, mailings and delivery attempts."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone


class OwnedModel(models.Model):
    """Base for objects owned by a single account."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        abstract = True


class Recipient(OwnedModel):
    """A contact with a unique email address."""

    email = models.EmailField("Email", unique=True)
    full_name = models.CharField("Ф. И. О.", max_length=200)
    comment = models.TextField("Комментарий", blank=True)

    class Meta:
        ordering = ("full_name", "pk")
        constraints = [models.UniqueConstraint(Lower("email"), name="unique_recipient_email_ci")]

    def __str__(self):
        return f"{self.full_name} ({self.email})"


class Message(OwnedModel):
    """Reusable subject and body of an email."""

    subject = models.CharField("Тема письма", max_length=200)
    body = models.TextField("Текст письма")

    class Meta:
        ordering = ("subject", "pk")

    def __str__(self):
        return self.subject


class Mailing(OwnedModel):
    """Scheduled time window and audience for a message."""

    class Status(models.TextChoices):
        CREATED = "created", "Создана"
        RUNNING = "running", "Запущена"
        FINISHED = "finished", "Завершена"

    start_time = models.DateTimeField("Начало отправки")
    end_time = models.DateTimeField("Окончание отправки")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.CREATED)
    message = models.ForeignKey(Message, on_delete=models.PROTECT, verbose_name="Письмо")
    recipients = models.ManyToManyField(Recipient, verbose_name="Получатели")
    is_disabled = models.BooleanField("Отключена менеджером", default=False)

    class Meta:
        ordering = ("-start_time", "-pk")
        constraints = [models.CheckConstraint(
            condition=Q(start_time__lt=F("end_time")), name="mailing_start_before_end",
        )]

    def __str__(self):
        return str(self.message)

    def clean(self):
        """Reject past starts on creation or rescheduling and invalid windows."""
        super().clean()
        errors = {}
        if self.start_time:
            previous = (
                type(self).objects.filter(pk=self.pk).values_list("start_time", flat=True).first()
                if not self._state.adding else None
            )
            if self.start_time != previous and self.start_time < timezone.now():
                errors["start_time"] = "Начало отправки не может быть в прошлом."
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            errors["end_time"] = "Окончание должно быть позже начала отправки."
        if self.owner_id and self.message_id and self.message.owner_id != self.owner_id:
            errors["message"] = "Выберите письмо из своего списка."
        if errors:
            raise ValidationError(errors)

    def status_at(self, now):
        """Compute status, including both boundaries in the sending window."""
        if now < self.start_time:
            return self.Status.CREATED
        if now > self.end_time:
            return self.Status.FINISHED
        return self.Status.RUNNING

    def update_status(self, now=None):
        """Persist a changed status, avoiding a write if it is already current."""
        new_status = self.status_at(timezone.now() if now is None else now)
        if self.status != new_status:
            self.status = new_status
            self.save(update_fields=["status"])
        return new_status


class DeliveryAttempt(models.Model):
    """The outcome of a single email, retained even if a contact is deleted."""

    class Status(models.TextChoices):
        SUCCESS = "success", "Успешно"
        FAILURE = "failure", "Не успешно"

    mailing = models.ForeignKey(Mailing, related_name="attempts", on_delete=models.CASCADE)
    recipient_email = models.EmailField()
    attempt_time = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=10, choices=Status.choices)
    server_response = models.TextField()

    class Meta:
        ordering = ("-attempt_time", "-pk")

    def __str__(self):
        return f"{self.recipient_email}: {self.get_status_display()}"
