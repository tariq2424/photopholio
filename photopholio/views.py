from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import redirect_to_login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import (BookingForm, ContactForm, FeedbackForm, PaymentForm,
                    ProfileForm, RegisterForm)
from .models import (Booking, City, Feedback, Payment, Photographer, UserProfile)


def home(request):
    featured = Photographer.objects.select_related("city").order_by("-rating")[:6]
    cities = City.objects.filter(photographers__isnull=False).distinct()
    context = {
        "featured": featured,
        "cities": cities,
        "specialities": Photographer.SPECIALITY_CHOICES,
        "photographer_count": Photographer.objects.count(),
        "city_count": cities.count(),
    }
    return render(request, "photopholio/home.html", context)


def photographer_list(request):
    qs = Photographer.objects.select_related("city__state")
    q = request.GET.get("q", "").strip()
    city = request.GET.get("city", "")
    speciality = request.GET.get("speciality", "")
    min_rating = request.GET.get("min_rating", "")
    available = request.GET.get("available", "")
    sort = request.GET.get("sort", "rating")

    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(bio__icontains=q))
    if city.isdigit():
        qs = qs.filter(city_id=city)
    if speciality:
        qs = qs.filter(specialities=speciality)
    try:
        if min_rating:
            qs = qs.filter(rating__gte=float(min_rating))
    except ValueError:
        pass
    if available:
        qs = qs.filter(is_available=True)

    ordering = {"rating": "-rating", "price_low": "price_per_day", "price_high": "-price_per_day",
                "experience": "-experience_years"}.get(sort, "-rating")
    qs = qs.order_by(ordering, "name")

    page = Paginator(qs, 9).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    context = {
        "page": page,
        "cities": City.objects.filter(photographers__isnull=False).distinct(),
        "specialities": Photographer.SPECIALITY_CHOICES,
        "querystring": params.urlencode(),
        "filters": {"q": q, "city": city, "speciality": speciality, "min_rating": min_rating,
                    "available": available, "sort": sort},
    }
    return render(request, "photopholio/photographer_list.html", context)


def photographer_detail(request, slug):
    photographer = get_object_or_404(Photographer.objects.select_related("city__state__country"), slug=slug)
    form = BookingForm(request.POST or None, photographer=photographer)

    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if form.is_valid():
            booking = form.save(commit=False)
            booking.user = request.user
            booking.photographer = photographer
            booking.save()
            messages.success(request, "Booking created. Complete the payment to confirm your date.")
            return redirect("payment", pk=booking.pk)

    context = {
        "p": photographer,
        "form": form,
        "portfolios": photographer.portfolios.prefetch_related("photos"),
        "reviews": Feedback.objects.filter(booking__photographer=photographer).select_related("user"),
    }
    return render(request, "photopholio/photographer_detail.html", context)


def register(request):
    if request.user.is_authenticated:
        return redirect("home")

    form = RegisterForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = form.save()

        messages.success(
            request,
            "Account created successfully. Please log in."
        )

        return redirect("login")

    return render(request, "photopholio/register.html", {"form": form})
    if request.user.is_authenticated:
        return redirect("home")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, f"Welcome, {user.first_name}! Your account is ready.")
        return redirect("profile")
    return render(request, "photopholio/register.html", {"form": form})


@login_required
def profile(request):
    obj, _ = UserProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile saved.")
        return redirect("profile")
    return render(request, "photopholio/profile.html", {"form": form, "profile": obj})


@login_required
def my_bookings(request):
    bookings = (Booking.objects.filter(user=request.user)
                .select_related("photographer__city").prefetch_related("payments", "feedbacks"))
    return render(request, "photopholio/my_bookings.html", {"bookings": bookings})


@login_required
@require_POST
def booking_cancel(request, pk):
    booking = get_object_or_404(Booking, pk=pk, user=request.user)
    if booking.status in ("pending", "confirmed"):
        booking.status = "cancelled"
        booking.save(update_fields=["status"])
        messages.info(request, "Booking cancelled.")
    else:
        messages.error(request, "This booking can no longer be cancelled.")
    return redirect("my_bookings")


@login_required
def payment(request, pk):
    booking = get_object_or_404(Booking.objects.select_related("photographer"), pk=pk, user=request.user)
    if booking.status == "cancelled" or booking.is_paid:
        messages.info(request, "Nothing to pay for this booking.")
        return redirect("my_bookings")
    form = PaymentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        # DEMO payment: no card details are collected and no money moves.
        Payment.objects.create(
            user=request.user, booking=booking, amount=booking.amount,
            payment_method=form.cleaned_data["payment_method"], status="completed",
        )
        booking.status = "confirmed"
        booking.save(update_fields=["status"])
        messages.success(request, "Payment received. Your booking is confirmed.")
        return redirect("my_bookings")
    return render(request, "photopholio/payment.html", {"booking": booking, "form": form})


@login_required
def feedback_create(request, pk):
    booking = get_object_or_404(Booking, pk=pk, user=request.user)
    if not booking.can_review:
        messages.error(request, "You can review a booking once it is completed, and only once.")
        return redirect("my_bookings")
    form = FeedbackForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        fb = form.save(commit=False)
        fb.user, fb.booking = request.user, booking
        fb.save()
        messages.success(request, "Thanks for your review.")
        return redirect("photographer_detail", slug=booking.photographer.slug)
    return render(request, "photopholio/feedback.html", {"booking": booking, "form": form})


def contact(request):
    initial = {}
    if request.user.is_authenticated:
        initial = {"name": request.user.get_full_name() or request.user.username, "email": request.user.email}
    form = ContactForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Message sent. We usually reply within a day.")
        return redirect("contact")
    return render(request, "photopholio/contact.html", {"form": form})
