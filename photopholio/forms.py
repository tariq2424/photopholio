from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.utils import timezone

from .models import Booking, ContactUs, Feedback, Payment, UserProfile


class BootstrapMixin:
    """Adds Bootstrap classes to every widget so forms look right without extra HTML."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            css = "form-select" if isinstance(widget, forms.Select) else "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class RegisterForm(BootstrapMixin, UserCreationForm):
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=60, required=True, label="Full name")

    class Meta:
        model = User
        fields = ("first_name", "username", "email", "password1", "password2")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            UserProfile.objects.get_or_create(user=user)
        return user


class ProfileForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("dob", "phone_no", "city", "address", "image")
        widgets = {
            "dob": forms.DateInput(attrs={"type": "date"}),
            "address": forms.Textarea(attrs={"rows": 3}),
        }


class BookingForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Booking
        fields = ("event_date", "tool")
        labels = {"event_date": "Event date", "tool": "Equipment or special requirements"}
        widgets = {
            "event_date": forms.DateInput(attrs={"type": "date"}),
            "tool": forms.TextInput(attrs={"placeholder": "e.g. drone shots, extra lighting"}),
        }

    def __init__(self, *args, photographer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.photographer = photographer
        self.fields["event_date"].widget.attrs["min"] = timezone.localdate().isoformat()

    def clean_event_date(self):
        date = self.cleaned_data["event_date"]
        if date < timezone.localdate():
            raise forms.ValidationError("Choose a date that is today or later.")
        clash = Booking.objects.filter(
            photographer=self.photographer,
            event_date=date,
            status__in=["pending", "confirmed"],
        ).exists()
        if clash:
            raise forms.ValidationError("This photographer is already booked on that date. Try another day.")
        return date


class PaymentForm(BootstrapMixin, forms.Form):
    payment_method = forms.ChoiceField(choices=Payment.METHOD_CHOICES)


class FeedbackForm(BootstrapMixin, forms.ModelForm):
    rating = forms.TypedChoiceField(
        choices=[(5, "5 - Excellent"), (4, "4 - Very good"), (3, "3 - Good"), (2, "2 - Fair"), (1, "1 - Poor")],
        coerce=int,
    )

    class Meta:
        model = Feedback
        fields = ("rating", "comment")
        widgets = {"comment": forms.Textarea(attrs={"rows": 4, "placeholder": "How was the shoot?"})}


class ContactForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = ContactUs
        fields = ("name", "email", "phone", "subject", "message")
        widgets = {"message": forms.Textarea(attrs={"rows": 5})}


class LoginForm(BootstrapMixin, AuthenticationForm):
    pass
