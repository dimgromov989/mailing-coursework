"""Forms validate ownership and prepare schedule inputs."""
from django import forms

from .models import Mailing, Message, Recipient


class RecipientForm(forms.ModelForm):
    """Create or update a recipient."""

    class Meta:
        model = Recipient
        fields = ("email", "full_name", "comment")

    def clean_email(self):
        """Normalize addresses and report conflicts before saving."""
        email = self.cleaned_data["email"].lower()
        if Recipient.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Этот адрес уже есть в сервисе.")
        return email


class MessageForm(forms.ModelForm):
    """Create or update a reusable letter."""

    class Meta:
        model = Message
        fields = ("subject", "body")

    def clean_subject(self):
        """Reject line breaks in email headers."""
        subject = self.cleaned_data["subject"]
        if "\r" in subject or "\n" in subject:
            raise forms.ValidationError("Тема должна быть одной строкой.")
        return subject


class MailingForm(forms.ModelForm):
    """Restrict selectable letters and recipients to the current user."""

    class Meta:
        model = Mailing
        fields = ("start_time", "end_time", "message", "recipients")
        widgets = {
            name: forms.DateTimeInput(format="%Y-%m-%dT%H:%M:%S", attrs={
                "type": "datetime-local", "step": "1",
            })
            for name in ("start_time", "end_time")
        }

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.owner = owner
        self.fields["message"].queryset = Message.objects.filter(owner=owner)
        self.fields["recipients"].queryset = Recipient.objects.filter(owner=owner)

    def save(self, commit=True):
        """Save the status corresponding to the edited schedule."""
        from django.utils import timezone

        mailing = super().save(commit=False)
        mailing.status = mailing.status_at(timezone.now())
        if commit:
            mailing.save()
            self.save_m2m()
        return mailing
