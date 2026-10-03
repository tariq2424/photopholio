from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from .models import (Booking, City, ContactUs, Feedback, Payment, Photo, Photographer,
                     Portfolio, State, UserProfile)
from .accounts import authenticate_identifier
from .roles import get_role


class BootstrapMixin:
    """Adds Bootstrap classes to every widget so forms look right without extra HTML."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.RadioSelect)):
                css = "form-select" if isinstance(widget, forms.Select) else ""
            else:
                css = "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {css}".strip()


class RegisterForm(BootstrapMixin, UserCreationForm):
    ROLE_CHOICES = [
        ("customer", "I want to book photographers"),
        ("photographer", "I am a photographer"),
    ]
    role = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.RadioSelect, initial="customer", label="I am joining as")
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=60, required=True, label="Full name")

    class Meta:
        model = User
        fields = ("role", "first_name", "email", "username", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].widget.attrs["autocomplete"] = "name"
        self.fields["email"].widget.attrs["autocomplete"] = "email"
        self.fields["username"].help_text = "You can log in with this or with your email."

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists. Try logging in instead.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            UserProfile.objects.get_or_create(user=user, defaults={"role": self.cleaned_data["role"]})
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

    def __init__(self, *args, photographer=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.photographer = photographer
        self.user = user if (user is not None and user.is_authenticated) else None
        self.fields["event_date"].widget.attrs["min"] = timezone.localdate().isoformat()

    def clean_event_date(self):
        date = self.cleaned_data["event_date"]
        if date < timezone.localdate():
            raise forms.ValidationError("Choose a date that is today or later.")
        # Only an accepted or paid booking blocks a date. Several customers may ask for the same day;
        # the photographer picks one and the others are declined automatically.
        clash = Booking.objects.filter(
            photographer=self.photographer,
            event_date=date,
            status__in=["accepted", "confirmed"],
        ).exists()
        if clash:
            raise forms.ValidationError("This photographer is already booked on that date. Try another day.")
        if self.user is not None and Booking.objects.filter(
                user=self.user, photographer=self.photographer, event_date=date,
                status__in=["pending", "accepted", "confirmed"]).exists():
            raise forms.ValidationError("You have already requested this photographer for that date.")
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


class SmartLoginForm(BootstrapMixin, AuthenticationForm):
    """
    One login for everybody: customers and photographers both use it.
    Accepts a username or an email, in any capitalisation. Where you land afterwards depends on your role.
    """
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "That username or email and password do not match. Check the spelling and try again.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["username"]
        field.label = "Username or email"
        field.max_length = 254
        field.widget.attrs.update({"maxlength": 254, "autocapitalize": "none", "autocomplete": "username"})

    def clean(self):
        identifier = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if identifier is not None and password:
            self.user_cache = authenticate_identifier(self.request, identifier, password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


# Older names, kept so nothing that imports them breaks.
LoginForm = SmartLoginForm
PhotographerLoginForm = SmartLoginForm


# --- Photographer area --------------------------------------------------------

MAX_IMAGE_MB = 5


def _check_image_size(image):
    if isinstance(image, UploadedFile) and image.size > MAX_IMAGE_MB * 1024 * 1024:
        raise forms.ValidationError(f"Image is too large. Please keep it under {MAX_IMAGE_MB} MB.")
    return image


class PhotographerProfileForm(BootstrapMixin, forms.ModelForm):
    state = forms.ModelChoiceField(queryset=State.objects.none(), label="State")
    city_name = forms.CharField(max_length=100, label="City",
                                widget=forms.TextInput(attrs={"list": "city-options", "placeholder": "Start typing your city"}))

    field_order = ["name", "specialities", "state", "city_name", "experience_years",
                   "price_per_day", "bio", "image", "is_available"]

    class Meta:
        model = Photographer
        fields = ("name", "specialities", "experience_years", "price_per_day", "bio", "image", "is_available")
        labels = {
            "name": "Name shown to customers",
            "specialities": "Main style",
            "experience_years": "Years of experience",
            "price_per_day": "Price for a full day (INR)",
            "image": "Profile photo",
            "is_available": "I am currently taking bookings",
        }
        widgets = {"bio": forms.Textarea(attrs={"rows": 5, "placeholder": "Tell customers about your style, experience and what a shoot with you is like."})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["state"].queryset = State.objects.select_related("country").order_by("country__name", "name")
        if self.instance.pk and self.instance.city_id:
            self.fields["state"].initial = self.instance.city.state
            self.fields["city_name"].initial = self.instance.city.name

    def clean_image(self):
        return _check_image_size(self.cleaned_data.get("image"))

    def clean_experience_years(self):
        v = self.cleaned_data["experience_years"]
        if v > 60:
            raise forms.ValidationError("Please enter a realistic number of years.")
        return v

    def clean_price_per_day(self):
        v = self.cleaned_data["price_per_day"]
        if v < 500:
            raise forms.ValidationError("Please enter a price of at least 500.")
        return v

    def save(self, commit=True):
        obj = super().save(commit=False)
        state = self.cleaned_data["state"]
        name = self.cleaned_data["city_name"].strip()
        city = City.objects.filter(state=state, name__iexact=name).first() or City.objects.create(state=state, name=name)
        obj.city = city
        if commit:
            obj.save()
        return obj


class PortfolioForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Portfolio
        fields = ("title", "description")
        widgets = {"title": forms.TextInput(attrs={"placeholder": "e.g. Wedding in Udaipur"}),
                   "description": forms.Textarea(attrs={"rows": 3})}


class PhotoUploadForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Photo
        fields = ("portfolio", "image", "caption")
        widgets = {"caption": forms.TextInput(attrs={"placeholder": "One line about this photo"})}

    def __init__(self, *args, photographer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["portfolio"].queryset = Portfolio.objects.filter(photographer=photographer)
        self.fields["portfolio"].empty_label = None

    def clean_image(self):
        return _check_image_size(self.cleaned_data.get("image"))


class CancelReasonForm(BootstrapMixin, forms.Form):
    reason = forms.CharField(label="Reason (the customer will see this)", widget=forms.Textarea(attrs={"rows": 3}),
                             max_length=500)
