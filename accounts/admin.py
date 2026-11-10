"""Account administration."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class AccountAdmin(UserAdmin):
    """Expose confirmation alongside the standard Django account fields."""

    fieldsets = UserAdmin.fieldsets + (("Подтверждение", {"fields": ("email_confirmed",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Контакты", {"fields": ("email",)}),)
