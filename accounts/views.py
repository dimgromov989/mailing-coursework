"""Email confirmation and manager moderation."""
from smtplib import SMTPException

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from .forms import SignUpForm
from .models import User

CONFIRM_SALT = "postbox-confirm-email"
CONFIRM_TIMEOUT = 24 * 60 * 60


@never_cache
@require_http_methods(["GET", "POST"])
def signup(request):
    """Register an inactive user and email a signed confirmation URL."""
    if request.user.is_authenticated:
        return redirect("mailing-list")
    form = SignUpForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        try:
            with transaction.atomic():
                user = form.save(commit=False)
                user.is_active = False
                user.save()
                token = signing.dumps([user.pk, user.email], salt=CONFIRM_SALT)
                link = request.build_absolute_uri(reverse("activate", args=[token]))
                if send_mail(
                    "Подтвердите регистрацию в Почтовом бюро",
                    f"Откройте ссылку, чтобы подтвердить адрес (действует 24 часа):\n{link}",
                    settings.DEFAULT_FROM_EMAIL, [user.email],
                ) != 1:
                    raise OSError("Email backend did not accept the message")
        except IntegrityError:
            form.add_error(None, "Такой адрес или логин уже используется.")
        except (OSError, SMTPException):
            form.add_error(None, "Не удалось отправить письмо. Попробуйте позже.")
        else:
            return render(request, "accounts/info.html", {
                "title": "Проверьте почту",
                "detail": "Откройте ссылку из письма и подтвердите адрес.",
            })
    return render(request, "accounts/form.html", {"title": "Создать аккаунт", "form": form})


@never_cache
@require_http_methods(["GET", "POST"])
def activate(request, token):
    """Show confirmation on GET and consume a valid token on POST."""
    try:
        pk, email = signing.loads(token, salt=CONFIRM_SALT, max_age=CONFIRM_TIMEOUT)
    except signing.BadSignature:
        return invalid_activation(request)
    pending = User.objects.filter(pk=pk, email=email, email_confirmed=False, is_active=False)
    if request.method == "GET" and pending.exists():
        return render(request, "accounts/activate.html")
    if request.method == "POST" and pending.update(email_confirmed=True, is_active=True):
        messages.success(request, "Адрес подтверждён. Теперь можно войти.")
        return redirect("login")
    return invalid_activation(request)


def invalid_activation(request):
    """Render an error for expired, used or invalid activation links."""
    return render(request, "accounts/info.html", {
        "title": "Ссылка недействительна",
        "detail": "Срок ссылки истёк или адрес уже подтверждён.",
    }, status=400)


@login_required
@never_cache
@require_safe
def users(request):
    """List service users for managers."""
    if not request.user.is_manager:
        raise PermissionDenied
    accounts = User.objects.order_by("username").prefetch_related("groups")
    return render(request, "accounts/users.html", {"accounts": accounts})


@login_required
@never_cache
@require_POST
def block_user(request, pk):
    """Block a regular account and prevent managers from blocking peers."""
    if not request.user.is_manager:
        raise PermissionDenied
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk or user.is_staff or user.is_manager:
        raise PermissionDenied
    if user.email_confirmed:
        User.objects.filter(pk=pk).update(is_active=False)
        messages.success(request, f"Доступ пользователя {user.username} заблокирован.")
    return redirect("user-list")
