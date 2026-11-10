"""Dashboard, ownership-scoped CRUD and mailing actions."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers, patch_cache_control, patch_vary_headers
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .metrics import get_overview, get_report
from .models import Mailing, Message, Recipient
from .services import MailingUnavailable, send_mailing

LABELS = {
    "recipient": ("Получатели", "Новый получатель"),
    "message": ("Письма", "Новое письмо"),
    "mailing": ("Рассылки", "Новая рассылка"),
}


@require_safe
def home(request):
    """Display public aggregate data with short browser caching for guests."""
    response = render(request, "mailings/home.html", {"overview": get_overview()})
    if request.user.is_authenticated or "messages" in response.cookies or request.COOKIES:
        add_never_cache_headers(response)
    else:
        patch_cache_control(response, private=True, max_age=10)
        patch_vary_headers(response, ["Cookie"])
    return response


class OwnedView(LoginRequiredMixin):
    """Filter all object lookups by ownership or manager read access."""

    kind = None
    allow_manager_read = False

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        add_never_cache_headers(response)
        return response

    def get_queryset(self):
        """Use the same permission filter for list and direct object URLs."""
        queryset = super().get_queryset().select_related("owner")
        if self.model is Mailing:
            queryset = queryset.select_related("message")
        if self.allow_manager_read and self.model in (Recipient, Mailing) and self.request.user.is_manager:
            return queryset
        return queryset.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        """Supply titles and named URLs shared by the CRUD templates."""
        context = super().get_context_data(**kwargs)
        plural, create = LABELS[self.kind]
        context.update({
            "kind": self.kind, "section_title": plural, "create_title": create,
            **{f"{action}_url": f"{self.kind}-{action}" for action in (
                "list", "create", "detail", "update", "delete",
            )},
        })
        return context

    def get_success_url(self):
        """Return to the corresponding list after saving or deleting."""
        return reverse(f"{self.kind}-list")


class OwnedList(OwnedView, ListView):
    """Show the permitted objects with up-to-date status labels."""

    allow_manager_read = True
    template_name = "mailings/list.html"
    paginate_by = 25

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.model is Mailing:
            now = timezone.now()
            for mailing in context["object_list"]:
                mailing.status = mailing.status_at(now)
        return context


class OwnedDetail(OwnedView, DetailView):
    """Show one permitted object; persist a refreshed mailing status."""

    allow_manager_read = True
    template_name = "mailings/detail.html"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if isinstance(obj, Mailing):
            obj.update_status()
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if isinstance(self.object, Mailing):
            context["recipients"] = self.object.recipients.all()
            context["attempts"] = self.object.attempts.all()[:30]
        return context


class OwnedEdit(OwnedView):
    """Assign ownership server-side and save the form in one transaction."""

    template_name = "mailings/edit.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.model is Mailing:
            kwargs["owner"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.owner = self.request.user
        try:
            with transaction.atomic():
                response = super().form_valid(form)
        except IntegrityError:
            form.add_error(None, "Данные уже используются или изменились. Проверьте форму.")
            return self.form_invalid(form)
        messages.success(self.request, "Изменения сохранены.")
        return response


class OwnedCreate(OwnedEdit, CreateView):
    """Create a new object for the signed-in account."""


class OwnedUpdate(OwnedEdit, UpdateView):
    """Edit an object belonging to the signed-in account."""


class OwnedDelete(OwnedView, DeleteView):
    """Delete the owner's object unless it is required by a mailing."""

    template_name = "mailings/delete.html"

    def form_valid(self, form):
        try:
            response = super().form_valid(form)
        except ProtectedError:
            messages.error(self.request, "Письмо используется в рассылке. Сначала измените или удалите рассылку.")
            return redirect(self.get_success_url())
        messages.success(self.request, "Запись удалена.")
        return response


@login_required
@never_cache
@require_POST
def send(request, pk):
    """Run a mailing on the owner's explicit request."""
    mailing = get_object_or_404(Mailing, pk=pk, owner=request.user)
    try:
        result = send_mailing(mailing)
    except MailingUnavailable as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Отправлено: {result.successful}. Ошибок: {result.failed}.")
    return redirect("mailing-detail", pk=pk)


@login_required
@never_cache
@require_POST
def disable(request, pk):
    """Let managers disable a mailing without changing its schedule."""
    if not request.user.is_manager:
        raise PermissionDenied
    mailing = get_object_or_404(Mailing, pk=pk)
    if not mailing.is_disabled:
        mailing.is_disabled = True
        mailing.save(update_fields=["is_disabled"])
        messages.success(request, "Рассылка отключена. Отправка писем запрещена.")
    return redirect("mailing-detail", pk=pk)


@login_required
@never_cache
@require_safe
def report(request):
    """Display the current user's cached delivery statistics."""
    return render(request, "mailings/report.html", {"report": get_report(request.user.pk)})
