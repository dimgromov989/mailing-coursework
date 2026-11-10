"""Account model and role checks."""
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils.functional import cached_property

MANAGER_GROUP = "Менеджеры"


class User(AbstractUser):
    """An account with a unique email and a separately recorded confirmation."""

    email = models.EmailField("Электронная почта", unique=True)
    email_confirmed = models.BooleanField("Почта подтверждена", default=False)

    class Meta(AbstractUser.Meta):
        constraints = [models.UniqueConstraint(Lower("email"), name="unique_account_email_ci")]

    @cached_property
    def is_manager(self):
        """Return whether the account has access to moderation."""
        return self.is_active and (
            self.is_superuser or any(group.name == MANAGER_GROUP for group in self.groups.all())
        )
