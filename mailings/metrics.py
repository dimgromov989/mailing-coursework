"""Cache public counters and account-specific reports."""
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Min, Q
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import DeliveryAttempt, Mailing, Recipient

OVERVIEW_KEY = "postbox:overview:v1"
OVERVIEW_TTL = 20
REPORT_TTL = 60


def calculate_overview(now):
    """Synchronize statuses and count active mailings and unique recipients."""
    mailings = Mailing.objects.all()
    started = mailings.filter(start_time__lte=now, end_time__gte=now)
    for queryset, status in (
        (mailings.filter(start_time__gt=now), Mailing.Status.CREATED),
        (started, Mailing.Status.RUNNING),
        (mailings.filter(end_time__lt=now), Mailing.Status.FINISHED),
    ):
        queryset.exclude(status=status).update(status=status)
    enabled = mailings.filter(is_disabled=False)
    upcoming = enabled.aggregate(
        starts=Min("start_time", filter=Q(start_time__gt=now)),
        ends=Min("end_time", filter=Q(start_time__lte=now, end_time__gte=now)),
    )
    expires = now + timedelta(seconds=OVERVIEW_TTL)
    if upcoming["starts"]:
        expires = min(expires, upcoming["starts"])
    if upcoming["ends"]:
        expires = min(expires, upcoming["ends"] + timedelta(microseconds=1))
    return expires, {
        "mailings": mailings.count(),
        "active": started.filter(status=Mailing.Status.RUNNING, is_disabled=False).count(),
        "recipients": Recipient.objects.count(),
    }


def get_overview():
    """Read cached counters until the next scheduled change or TTL."""
    now = timezone.now()
    cached = cache.get(OVERVIEW_KEY)
    if cached is not None and now < cached[0]:
        return cached[1]
    snapshot = calculate_overview(now)
    cache.set(OVERVIEW_KEY, snapshot, OVERVIEW_TTL)
    return snapshot[1]


def get_report(owner_id):
    """Read the signed-in owner's delivery totals without mixing accounts."""
    def calculate():
        totals = DeliveryAttempt.objects.filter(mailing__owner_id=owner_id).aggregate(
            successful=Count("pk", filter=Q(status=DeliveryAttempt.Status.SUCCESS)),
            failed=Count("pk", filter=Q(status=DeliveryAttempt.Status.FAILURE)),
            attempts=Count("pk"),
        )
        return {**totals, "sent": totals["successful"]}

    return cache.get_or_set(report_key(owner_id), calculate, REPORT_TTL)


def report_key(owner_id):
    """Build a distinct key per owner."""
    return f"postbox:report:v1:{owner_id}"


def invalidate_report(owner_id):
    """Drop the owner report after the database change is committed."""
    transaction.on_commit(lambda: cache.delete(report_key(owner_id)))


@receiver(post_save, sender=Mailing)
@receiver(post_delete, sender=Mailing)
@receiver(post_save, sender=Recipient)
@receiver(post_delete, sender=Recipient)
def invalidate_overview(sender, instance, **kwargs):
    """Drop the public counters after a mailing or recipient changes."""
    transaction.on_commit(lambda: cache.delete(OVERVIEW_KEY))
    if isinstance(instance, Mailing):
        invalidate_report(instance.owner_id)
