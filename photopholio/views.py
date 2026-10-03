from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.views import redirect_to_login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import booking_flow, policy
from .booking_flow import FlowError
from .forms import (BookingForm, CancelReasonForm, ContactForm, FeedbackForm, PaymentForm,
                    PhotoUploadForm, PhotographerProfileForm, PortfolioForm,
                    ProfileForm, RegisterForm, SmartLoginForm)
from .models import (Booking, City, Feedback, Payment, Photo, Photographer,
                     Portfolio, UserProfile)
from .roles import get_role, photographer_required


def home(request):
    featured = Photographer.objects.approved().select_related("city").order_by("-rating")[:6]
    cities = City.objects.filter(photographers__status="approved").distinct()
    context = {
        "featured": featured,
        "cities": cities,
        "specialities": Photographer.SPECIALITY_CHOICES,
        "photographer_count": Photographer.objects.approved().count(),
        "city_count": cities.count(),
    }
    return render(request, "photopholio/home.html", context)


def photographer_list(request):
    qs = Photographer.objects.approved().select_related("city__state")
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
        "cities": City.objects.filter(photographers__status="approved").distinct(),
        "specialities": Photographer.SPECIALITY_CHOICES,
        "querystring": params.urlencode(),
        "filters": {"q": q, "city": city, "speciality": speciality, "min_rating": min_rating,
                    "available": available, "sort": sort},
    }
    return render(request, "photopholio/photographer_list.html", context)


def photographer_detail(request, slug):
    photographer = get_object_or_404(Photographer.objects.select_related("city__state__country"), slug=slug)
    is_owner = request.user.is_authenticated and photographer.user_id == request.user.id
    if photographer.status != "approved" and not (is_owner or request.user.is_staff):
        raise Http404("Photographer not found")  # not approved yet: hidden from customers
    role = get_role(request.user)
    can_book = photographer.status == "approved" and role != "photographer"
    booking_flow.expire_stale()
    form = BookingForm(request.POST or None, photographer=photographer, user=request.user)

    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not can_book:
            messages.error(request, "Photographer accounts can't make bookings. Use a customer account to book.")
            return redirect("photographer_detail", slug=photographer.slug)
        if form.is_valid():
            booking = form.save(commit=False)
            booking.user = request.user
            booking.photographer = photographer
            booking.save()
            messages.success(request, f"Request sent to {photographer.name}. You can pay once they accept it.")
            return redirect("my_bookings")

    context = {
        "p": photographer,
        "form": form,
        "portfolios": photographer.portfolios.prefetch_related("photos"),
        "reviews": Feedback.objects.filter(booking__photographer=photographer).select_related("user"),
        "is_owner": is_owner,
        "can_book": can_book,
        "role": role,
    }
    return render(request, "photopholio/photographer_detail.html", context)


def cancellation_policy(request):
    return render(request, "photopholio/policy.html", {
        "rows": policy.rows(), "window": policy.PAYMENT_WINDOW_HOURS})


def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    wanted = request.GET.get("role")
    initial = {"role": wanted} if wanted in ("customer", "photographer") else None
    form = RegisterForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        if form.cleaned_data["role"] == "photographer":
            messages.success(request, f"Welcome, {user.first_name}! Let's set up your photographer profile.")
            return redirect("pro_profile")
        messages.success(request, f"Welcome, {user.first_name}! Your account is ready.")
        return redirect("profile")
    return render(request, "photopholio/register.html", {"form": form})


@login_required
def profile(request):
    if get_role(request.user) == "photographer":
        return redirect("pro_dashboard")
    obj, _ = UserProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile saved.")
        return redirect("profile")
    return render(request, "photopholio/profile.html", {"form": form, "profile": obj})


@login_required
def my_bookings(request):
    if get_role(request.user) == "photographer":
        return redirect("pro_dashboard")
    booking_flow.expire_stale()
    bookings = (Booking.objects.filter(user=request.user)
                .select_related("photographer__city").prefetch_related("payments", "feedbacks"))
    return render(request, "photopholio/my_bookings.html", {"bookings": bookings})


@login_required
def booking_cancel(request, pk):
    booking_flow.expire_stale()
    booking = get_object_or_404(Booking.objects.select_related("photographer"), pk=pk, user=request.user)
    if booking.status not in ("pending", "accepted", "confirmed"):
        messages.error(request, "This booking can no longer be cancelled.")
        return redirect("my_bookings")
    paid = booking.paid_payment
    if request.method == "POST":
        try:
            refund = booking_flow.cancel_by_customer(booking)
        except FlowError as e:
            messages.error(request, str(e))
            return redirect("my_bookings")
        if paid and refund:
            messages.info(request, f"Booking cancelled. A refund of Rs.{refund} is pending.")
        elif paid:
            messages.info(request, "Booking cancelled. Under the policy this cancellation gets no refund.")
        else:
            messages.info(request, "Booking cancelled. Nothing was charged.")
        return redirect("my_bookings")
    context = {
        "booking": booking, "paid": paid,
        "percent": policy.refund_percent(booking.event_date) if paid else None,
        "refund": policy.refund_amount(paid.amount, booking.event_date) if paid else 0,
        "days": policy.days_before(booking.event_date),
    }
    return render(request, "photopholio/cancel_booking.html", context)


@login_required
def payment(request, pk):
    booking_flow.expire_stale()
    booking = get_object_or_404(Booking.objects.select_related("photographer"), pk=pk, user=request.user)
    if booking.status == "pending":
        messages.info(request, f"{booking.photographer.name} has not accepted this request yet. You can pay once they do.")
        return redirect("my_bookings")
    if booking.status != "accepted" or booking.is_paid:
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
    return render(request, "photopholio/payment.html", {"booking": booking, "form": form, "policy_rows": policy.rows()})


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


# --- Photographer area ---------------------------------------------------------

class SmartLoginView(LoginView):
    """
    The only login view. Customers and photographers sign in here (also reachable at /photographer/login/).
    Photographers land on their dashboard, everyone else on the home page, unless a ?next= page was requested.
    """
    template_name = "photopholio/login.html"
    authentication_form = SmartLoginForm
    redirect_authenticated_user = True

    def get_default_redirect_url(self):
        if get_role(self.request.user) == "photographer":
            return reverse("pro_dashboard")
        return super().get_default_redirect_url()


def _own_profile(request):
    return Photographer.objects.filter(user=request.user).first()


@photographer_required
def pro_dashboard(request):
    ph = _own_profile(request)
    context = {"ph": ph}
    if ph:
        booking_flow.expire_stale()
        today = timezone.localdate()
        bookings = Booking.objects.filter(photographer=ph).select_related("user").prefetch_related("payments")
        context.update({
            "checklist": ph.checklist(),
            "ready": ph.ready_for_review,
            "photo_count": ph.published_photo_count(),
            "requests": bookings.filter(status="pending").order_by("event_date"),
            "awaiting_payment": bookings.filter(status="accepted").order_by("event_date"),
            "to_complete": bookings.filter(status="confirmed", event_date__lte=today).order_by("event_date"),
            "upcoming": bookings.filter(status="confirmed", event_date__gt=today).order_by("event_date"),
            "history": bookings.filter(status__in=["completed", "cancelled", "declined"]).order_by("-event_date")[:15],
        })
    return render(request, "photopholio/pro_dashboard.html", context)


@photographer_required
def pro_profile(request):
    ph = _own_profile(request)
    form = PhotographerProfileForm(request.POST or None, request.FILES or None, instance=ph,
                                   initial=None if ph else {"name": request.user.get_full_name()})
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        creating = obj.pk is None
        obj.user = request.user
        if creating:
            obj.status = "draft"
        obj.save()
        if creating:
            messages.success(request, "Profile created. Now add your portfolio photos.")
            return redirect("pro_portfolio")
        messages.success(request, "Profile saved.")
        return redirect("pro_dashboard")
    return render(request, "photopholio/pro_profile.html", {
        "form": form, "ph": ph, "city_names": City.objects.values_list("name", flat=True).distinct().order_by("name")})


@photographer_required
def pro_portfolio(request):
    ph = _own_profile(request)
    if ph is None:
        messages.info(request, "Create your profile first, then add photos.")
        return redirect("pro_profile")

    portfolio_form = PortfolioForm(prefix="pf")
    photo_form = PhotoUploadForm(photographer=ph, prefix="ph")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "portfolio":
            portfolio_form = PortfolioForm(request.POST, prefix="pf")
            if portfolio_form.is_valid():
                pf = portfolio_form.save(commit=False)
                pf.photographer = ph
                pf.save()
                messages.success(request, "Portfolio created. Add some photos to it.")
                return redirect("pro_portfolio")
        elif action == "photo":
            photo_form = PhotoUploadForm(request.POST, request.FILES, photographer=ph, prefix="ph")
            if photo_form.is_valid():
                photo = photo_form.save(commit=False)
                last = Photo.objects.filter(portfolio=photo.portfolio).order_by("-order_id").first()
                photo.order_id = (last.order_id + 1) if last else 1
                photo.status = "published"
                photo.save()
                messages.success(request, "Photo added.")
                return redirect("pro_portfolio")

    context = {
        "ph": ph,
        "portfolios": ph.portfolios.prefetch_related("photos"),
        "portfolio_form": portfolio_form,
        "photo_form": photo_form,
        "photo_count": ph.published_photo_count(),
    }
    return render(request, "photopholio/pro_portfolio.html", context)


@photographer_required
@require_POST
def pro_photo_delete(request, pk):
    photo = get_object_or_404(Photo, pk=pk, portfolio__photographer__user=request.user)
    photo.delete()
    messages.info(request, "Photo removed.")
    return redirect("pro_portfolio")


@photographer_required
@require_POST
def pro_portfolio_delete(request, pk):
    portfolio = get_object_or_404(Portfolio, pk=pk, photographer__user=request.user)
    portfolio.delete()
    messages.info(request, "Portfolio removed.")
    return redirect("pro_portfolio")


@photographer_required
@require_POST
def pro_submit(request):
    ph = _own_profile(request)
    if ph is None:
        return redirect("pro_profile")
    if ph.status not in ("draft", "rejected"):
        messages.info(request, "Nothing to submit right now.")
        return redirect("pro_dashboard")
    missing = [label for label, ok in ph.checklist() if not ok]
    if missing:
        messages.error(request, "Before submitting, please complete: " + "; ".join(missing) + ".")
        return redirect("pro_dashboard")
    ph.status = "pending"
    ph.rejection_reason = ""
    ph.save(update_fields=["status", "rejection_reason"])
    messages.success(request, "Submitted. We will review your profile and it will go live once approved.")
    return redirect("pro_dashboard")


@photographer_required
@require_POST
def pro_booking_action(request, pk, action):
    booking = get_object_or_404(Booking.objects.select_related("user", "photographer"),
                                pk=pk, photographer__user=request.user)
    booking_flow.expire_stale()
    booking.refresh_from_db()
    handlers = {"accept": booking_flow.accept, "decline": booking_flow.decline, "complete": booking_flow.mark_completed}
    if action not in handlers:
        raise Http404("Unknown action")
    done = {"accept": f"Accepted. {booking.user.first_name or booking.user.username} has {policy.PAYMENT_WINDOW_HOURS} hours to pay.",
            "decline": "Request declined.",
            "complete": "Marked as completed. The customer can now leave a review."}
    try:
        handlers[action](booking)
        messages.success(request, done[action])
    except FlowError as e:
        messages.error(request, str(e))
    return redirect("pro_dashboard")


@photographer_required
def pro_booking_cancel(request, pk):
    booking = get_object_or_404(Booking.objects.select_related("user", "photographer"),
                                pk=pk, photographer__user=request.user)
    booking_flow.expire_stale()
    booking.refresh_from_db()
    if booking.status not in ("accepted", "confirmed"):
        messages.error(request, "Only accepted or confirmed bookings can be cancelled.")
        return redirect("pro_dashboard")
    form = CancelReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            booking_flow.cancel_by_photographer(booking, form.cleaned_data["reason"])
            messages.info(request, "Booking cancelled. The customer is refunded in full and the cancellation is recorded.")
            return redirect("pro_dashboard")
        except FlowError as e:
            messages.error(request, str(e))
    return render(request, "photopholio/pro_cancel_booking.html",
                  {"booking": booking, "form": form, "paid": booking.paid_payment})
