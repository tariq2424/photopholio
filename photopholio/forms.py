from datetime import timedelta
from django import forms
from django.contrib.auth.forms import (AuthenticationForm, PasswordResetForm, SetPasswordForm,
                                       UserCreationForm)
from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from . import availability
from .models import (BlockedDate, Booking, City, ContactUs, Feedback, Package, Payment, Photo, Photographer,
                     Portfolio, State, UserProfile)
from .accounts import authenticate_identifier
from .roles import get_role
from . import throttle
from .throttle import ThrottledLoginMixin


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


class PackageChoice(forms.ModelChoiceField):
    def label_from_instance(self, package):
        return f"{package.get_tier_display()}: \u20b9{package.price} ({package.hours} h, {package.edited_photos} photos)"


class BookingForm(BootstrapMixin, forms.ModelForm):
    # Shown only when the photographer has packages. Without any, the booking is for the plain day rate.
    package = PackageChoice(queryset=Package.objects.none(), widget=forms.RadioSelect, empty_label=None,
                            label="Package", error_messages={"required": "Please choose a package."})
    field_order = ["event_date", "package", "tool"]

    class Meta:
        model = Booking
        fields = ("event_date", "package", "tool")
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
        offered = photographer.active_packages if photographer else []
        if offered:
            self.fields["package"].queryset = Package.objects.filter(pk__in=[p.pk for p in offered])
            standard = next((p for p in offered if p.tier == "standard"), offered[0])
            self.fields["package"].initial = standard.pk
        else:
            del self.fields["package"]

    def save(self, commit=True):
        """Copy the chosen package's name and price onto the booking (the snapshot)."""
        booking = super().save(commit=False)
        package = self.cleaned_data.get("package")
        if package:
            booking.package, booking.package_name, booking.price = package, package.get_tier_display(), package.price
        else:
            booking.package, booking.package_name, booking.price = None, "Full day", self.photographer.price_per_day
        if commit:
            booking.save()
        return booking

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
        if BlockedDate.objects.filter(photographer=self.photographer, date=date).exists():
            raise forms.ValidationError("This photographer is not available on that date. Pick a green day in the calendar.")
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


class IdentifierLoginForm(BootstrapMixin, AuthenticationForm):
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


class SmartLoginForm(ThrottledLoginMixin, IdentifierLoginForm):
    """The login form: username or email, and a lock after too many wrong passwords."""
    throttle_policy = throttle.SITE  # 5 wrong passwords in 15 minutes -> locked for 5 minutes


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


class RescheduleForm(BootstrapMixin, forms.Form):
    new_date = forms.DateField(label="New date", widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(label="Reason (optional, the photographer will see this)", required=False, max_length=255)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_date"].widget.attrs["min"] = (timezone.localdate() + timedelta(days=1)).isoformat()


class ReplyForm(BootstrapMixin, forms.Form):
    reply = forms.CharField(label="Your reply", max_length=600, widget=forms.Textarea(attrs={"rows": 3}))


# --- Forgot password -----------------------------------------------------------

class PasswordResetRequestForm(BootstrapMixin, PasswordResetForm):
    email = forms.EmailField(max_length=254, label="Your email",
                             widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))


class NewPasswordForm(BootstrapMixin, SetPasswordForm):
    pass


class BlockDatesForm(BootstrapMixin, forms.Form):
    MAX_DAYS = 90
    start = forms.DateField(label="From", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="To (leave empty for one day)", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(label="Reason (only you see this)", required=False, max_length=120,
                             widget=forms.TextInput(attrs={"placeholder": "e.g. family wedding"}))

    def __init__(self, *args, photographer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.photographer = photographer
        today = timezone.localdate().isoformat()
        self.fields["start"].widget.attrs["min"] = today
        self.fields["end"].widget.attrs["min"] = today

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start")
        if not start:
            return cleaned
        end = cleaned.get("end") or start
        if start < timezone.localdate():
            raise forms.ValidationError("You can only block days from today onwards.")
        if end < start:
            raise forms.ValidationError("The end date cannot be before the start date.")
        if (end - start).days >= self.MAX_DAYS:
            raise forms.ValidationError(f"Block at most {self.MAX_DAYS} days at a time.")
        taken = list(Booking.objects.filter(photographer=self.photographer, status__in=availability.BLOCKING_STATUSES,
                                            event_date__range=(start, end)).order_by("event_date").values_list("event_date", flat=True))
        if taken:
            days = ", ".join(d.strftime("%d %b") for d in taken)
            raise forms.ValidationError(f"You already have a booking on {days}. Cancel it first, or leave those days out.")
        cleaned["end"] = end
        return cleaned


class PackageForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Package
        fields = ("price", "hours", "edited_photos", "description", "is_active")
        labels = {"price": "Price (INR)", "hours": "Hours of coverage", "edited_photos": "Edited photos delivered",
                  "description": "What is included", "is_active": "Offer this package"}
        widgets = {"description": forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. Full day coverage and a printed album"})}

    def clean_price(self):
        v = self.cleaned_data["price"]
        if not 500 <= v <= 1000000:
            raise forms.ValidationError("Enter a price between 500 and 1,000,000.")
        return v

    def clean_hours(self):
        v = self.cleaned_data["hours"]
        if not 1 <= v <= 24:
            raise forms.ValidationError("Hours of coverage must be between 1 and 24.")
        return v

    def clean_edited_photos(self):
        v = self.cleaned_data["edited_photos"]
        if v > 5000:
            raise forms.ValidationError("Please enter a realistic number of photos.")
        return v
