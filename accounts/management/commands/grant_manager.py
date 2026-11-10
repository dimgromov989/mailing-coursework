"""Assign moderation privileges locally."""
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from accounts.models import MANAGER_GROUP, User


class Command(BaseCommand):
    """Grant the manager role to an existing confirmed account."""

    help = "Назначить менеджера: python manage.py grant_manager <логин>"

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, **options):
        user = User.objects.filter(
            username=options["username"], is_active=True, email_confirmed=True,
        ).first()
        if user is None:
            raise CommandError("Активный пользователь с подтверждённой почтой не найден.")
        group, _ = Group.objects.get_or_create(name=MANAGER_GROUP)
        user.groups.add(group)
        self.stdout.write(self.style.SUCCESS(f"{user.username}: назначен менеджером."))
