"""Manual sending from the command line."""
from django.core.management.base import BaseCommand, CommandError

from mailings.models import Mailing
from mailings.services import MailingUnavailable, send_mailing


class Command(BaseCommand):
    """Send a mailing through the same service as the web interface."""

    help = "Отправить рассылку: python manage.py send_mailing <ID>"

    def add_arguments(self, parser):
        parser.add_argument("mailing_id", type=int)

    def handle(self, *args, **options):
        mailing = Mailing.objects.filter(pk=options["mailing_id"]).first()
        if mailing is None:
            raise CommandError("Рассылка не найдена.")
        try:
            result = send_mailing(mailing)
        except MailingUnavailable as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"Отправлено: {result.successful}. Ошибок: {result.failed}.")
