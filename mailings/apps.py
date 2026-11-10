"""Register cache invalidation when the app starts."""
from django.apps import AppConfig


class MailingsConfig(AppConfig):
    """Configure the mailings app."""

    name = "mailings"

    def ready(self):
        """Connect cache invalidation signals."""
        from . import metrics  # noqa: F401
