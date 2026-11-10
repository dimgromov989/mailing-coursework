"""Registration input and unique email validation."""
from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class SignUpForm(UserCreationForm):
    """Create a password-protected account pending email confirmation."""

    email = forms.EmailField(label="Электронная почта", max_length=254)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_email(self):
        """Normalize and reject an email that already belongs to an account."""
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Этот адрес уже зарегистрирован.")
        return email
