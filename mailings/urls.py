"""Routes for recipients, letters, campaigns and reports."""
from django.urls import path

from . import views
from .forms import MailingForm, MessageForm, RecipientForm
from .models import Mailing, Message, Recipient

urlpatterns = [
    path("", views.home, name="home"),
    path("report/", views.report, name="report"),
    path("mailings/<int:pk>/send/", views.send, name="mailing-send"),
    path("mailings/<int:pk>/disable/", views.disable, name="mailing-disable"),
]
for kind, prefix, model, form in (
    ("recipient", "recipients", Recipient, RecipientForm),
    ("message", "messages", Message, MessageForm),
    ("mailing", "mailings", Mailing, MailingForm),
):
    common = {"kind": kind, "model": model}
    urlpatterns += [
        path(f"{prefix}/", views.OwnedList.as_view(**common), name=f"{kind}-list"),
        path(f"{prefix}/new/", views.OwnedCreate.as_view(**common, form_class=form), name=f"{kind}-create"),
        path(f"{prefix}/<int:pk>/", views.OwnedDetail.as_view(**common), name=f"{kind}-detail"),
        path(f"{prefix}/<int:pk>/edit/", views.OwnedUpdate.as_view(**common, form_class=form), name=f"{kind}-update"),
        path(f"{prefix}/<int:pk>/delete/", views.OwnedDelete.as_view(**common), name=f"{kind}-delete"),
    ]
