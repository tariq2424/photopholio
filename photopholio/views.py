from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.http import Http404
from django.utils.http import url_has_allowed_host_and_scheme
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.views import redirect_to_login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Min, Q
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import availability, booking_flow, emails, favourites, policy, reviews
from .booking_flow import FlowError
from .forms import (BlockDatesForm, BookingForm, CancelReasonForm, PackageForm, ContactForm, FeedbackForm, PaymentForm,
                    PhotoUploadForm, PhotographerProfileForm, PortfolioForm, RescheduleForm, ReplyForm,
                    ProfileForm, RegisterForm, SmartLoginForm)
from .models import (BlockedDate, Booking, City, Feedback, Package, Payment, Photo, Photographer,
                     Portfolio, RescheduleRequest, UserProfile)
from .roles import get_role, photographer_required


def _fav_context(request):
    """Which photographers to show a filled heart on, and whether to show hearts at all."""
    return {"fav_ids": favourites.saved_ids(request.user), "show_hearts": get_role(request.user) != "photographer"}


def _parse_day(value):
    """'2026-11-15' -> date, anything else -> None."""
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return None


def home(request):
    featured = Photographer.objects.approved().select_related("city").prefetch_related("packages").order_by("-rating")[:6]
    cities = City.objects.filter(photographers__status="approved").distinct()
    context = {
        "featured": featured,
        "cities": cities,
        "specialities": Photographer.SPECIALITY_CHOICES,
        "photographer_count": Photographer.objects.approved().count(),
        "city_count": cities.count(),
        "today": timezone.localdate().isoformat(),
        **_fav_context(request),
    }
    return render(request, "photopholio/home.html", context)


def photographer_list(request):
    qs = Photographer.objects.approved().select_related("city__state").prefetch_related("packages")
    q = request.GET.get("q", "").strip()
    city = request.GET.get("city", "")
    speciality = request.GET.get("speciality", "")
    min_rating = request.GET.get("min_rating", "")
    available = request.GET.get("available", "")
    verified = request.GET.get("verified", "")
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
    if verified:
        qs = qs.filter(is_verified=True)
    # "Free on this date": leave out photographers who blocked the day or already have an accepted/paid booking on it
    wanted = _parse_day(request.GET.get("date"))
    if wanted and wanted >= timezone.localdate():
        qs = availability.exclude_unavailable(qs, wanted)
    else:
        wanted = None

    # Price means the lowest package price, or the day rate for photographers without packages.
    qs = qs.annotate(start_price=Coalesce(Min("packages__price", filter=Q(packages__is_active=True)), F("price_per_day")))
    ordering = {"rating": "-rating", "price_low": "start_price", "price_high": "-start_price",
                "experience": "-experience_years"}.get(sort, "-rating")
    qs = qs.order_by(ordering, "name")

    page = Paginator(qs, 9).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    without_date = params.copy()
    without_date.pop("date", None)
    context = {
        "page": page,
        "cities": City.objects.filter(photographers__status="approved").distinct(),
        "specialities": Photographer.SPECIALITY_CHOICES,
        "querystring": params.urlencode(),
        "querystring_without_date": without_date.urlencode(),
        "wanted_date": wanted,
        **_fav_context(request),
        "today": timezone.localdate().isoformat(),
        "filters": {"q": q, "city": city, "speciality": speciality, "min_rating": min_rating,
                    "available": available, "verified": verified, "sort": sort, "date": wanted.isoformat() if wanted else ""},
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
    wanted = _parse_day(request.GET.get("date"))
    initial = {"event_date": wanted} if wanted and request.method == "GET" and availability.is_free(photographer, wanted) else None
    form = BookingForm(request.POST or None, initial=initial, photographer=photographer, user=request.user)

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
            emails.booking_requested(booking)
            messages.success(request, f"Request sent to {photographer.name}. You can pay once they accept it.")
            return redirect("my_bookings")

    context = {
        "p": photographer,
        "form": form,
        "portfolios": photographer.portfolios.prefetch_related("photos"),
        "reviews": Feedback.objects.filter(booking__photographer=photographer, is_hidden=False).select_related("user"),
        "is_saved": request.user.is_authenticated and photographer.pk in favourites.saved_ids(request.user),
        "calendar_months": availability.months(photographer, 3),
        "packages": photographer.active_packages,
        "is_owner": is_owner,
        "can_book": can_book,
        "role": role,
    }
    return render(request, "photopholio/photographer_detail.html", context)


def cancellation_policy(request):
    return render(request, "photopholio/policy.html", {
        "rows": policy.rows(), "window": policy.PAYMENT_WINDOW_HOURS,
        "resched_days": policy.RESCHEDULE_MIN_DAYS, "resched_max": policy.MAX_RESCHEDULES})


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
    today = timezone.localdate()
    bookings = list(Booking.objects.filter(user=request.user)
                    .select_related("photographer__city").prefetch_related("payments", "feedbacks", "reschedules"))
    by_status = lambda *s: [b for b in bookings if b.status in s]
    upcoming = sorted(by_status("confirmed"), key=lambda b: b.event_date)
    payments = list(Payment.objects.filter(user=request.user, status="completed")
                    .select_related("booking__photographer").order_by("-payment_date"))
    refunded = sum(p.refund_amount for p in payments if p.refund_status == "refunded")
    refund_waiting = sum(p.refund_amount for p in payments if p.refund_status == "pending")
    context = {
        "to_pay": sorted(by_status("accepted"), key=lambda b: b.event_date),
        "upcoming": upcoming,
        "waiting": sorted(by_status("pending"), key=lambda b: b.event_date),
        "past": by_status("completed", "cancelled", "declined"),
        "next_shoot": next((b for b in upcoming if b.event_date >= today), None),
        "payments": payments,
        "spent": sum(p.amount for p in payments) - refunded,
        "refund_waiting": refund_waiting,
        "saved_count": favourites.saved_photographers(request.user).count(),
        "total": len(bookings),
    }
    return render(request, "photopholio/my_bookings.html", context)


@login_required
def my_favourites(request):
    if get_role(request.user) == "photographer":
        return redirect("pro_dashboard")
    return render(request, "photopholio/favourites.html", {
        "photographers": favourites.saved_photographers(request.user),
        "fav_ids": favourites.saved_ids(request.user), "show_hearts": True,
        "today": timezone.localdate().isoformat()})


@login_required
@require_POST
def favourite_toggle(request, slug):
    photographer = get_object_or_404(Photographer, slug=slug)
    if not favourites.can_save(request.user, photographer):
        messages.error(request, "You can only save photographers from a customer account.")
    elif favourites.toggle(request.user, photographer):
        messages.success(request, f"Saved {photographer.name} to your list.")
    else:
        messages.info(request, f"Removed {photographer.name} from your list.")
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = reverse("photographer_detail", args=[photographer.slug])
    return redirect(target)


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
def booking_reschedule(request, pk):
    booking_flow.expire_stale()
    booking = get_object_or_404(Booking.objects.select_related("photographer__city"), pk=pk, user=request.user)
    form = RescheduleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            booking_flow.request_reschedule(booking, form.cleaned_data["new_date"], form.cleaned_data["reason"])
            messages.success(request, f"Request sent. Your booking stays on {booking.event_date:%d %b %Y} until {booking.photographer.name} accepts.")
            return redirect("my_bookings")
        except FlowError as e:
            form.add_error(None, str(e))
    elif request.method == "GET" and not booking.can_request_reschedule():
        messages.error(request, "This booking cannot be rescheduled right now. See the rules on the cancellation policy page.")
        return redirect("my_bookings")
    return render(request, "photopholio/reschedule.html", {
        "booking": booking, "form": form, "min_days": policy.RESCHEDULE_MIN_DAYS,
        "calendar_months": availability.months(booking.photographer, 3)})


@login_required
@require_POST
def reschedule_withdraw(request, pk):
    req = get_object_or_404(RescheduleRequest, pk=pk, booking__user=request.user)
    try:
        booking_flow.withdraw_reschedule(req)
        messages.info(request, "Reschedule request withdrawn. Your booking date is unchanged.")
    except FlowError as e:
        messages.error(request, str(e))
    return redirect("my_bookings")


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
        paid = Payment.objects.create(
            user=request.user, booking=booking, amount=booking.amount,
            payment_method=form.cleaned_data["payment_method"], status="completed",
        )
        booking.status = "confirmed"
        booking.save(update_fields=["status"])
        emails.payment_received(booking, paid)
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
            "reschedule_requests": RescheduleRequest.objects.filter(booking__photographer=ph, status="pending")
                                   .select_related("booking__user").order_by("new_date"),
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
@require_POST
def pro_reschedule_action(request, pk, action):
    req = get_object_or_404(RescheduleRequest.objects.select_related("booking__user", "booking__photographer"),
                            pk=pk, booking__photographer__user=request.user)
    booking_flow.expire_stale()
    req.refresh_from_db()
    if action not in ("accept", "decline"):
        raise Http404("Unknown action")
    try:
        if action == "accept":
            booking_flow.accept_reschedule(req)
            messages.success(request, f"Date changed to {req.new_date:%d %b %Y}. The customer has been told.")
        else:
            booking_flow.decline_reschedule(req, request.POST.get("reason", ""))
            messages.info(request, "Request declined. The booking stays on its original date.")
    except FlowError as e:
        messages.error(request, str(e))
    return redirect("pro_dashboard")


@photographer_required
def pro_reviews(request):
    ph = _own_profile(request)
    if ph is None:
        return redirect("pro_dashboard")
    items = Feedback.objects.filter(booking__photographer=ph, is_hidden=False).select_related("user", "booking")
    return render(request, "photopholio/pro_reviews.html", {"ph": ph, "reviews": items, "form": ReplyForm()})


@photographer_required
@require_POST
def pro_review_reply(request, pk):
    fb = get_object_or_404(Feedback, pk=pk, booking__photographer__user=request.user)
    try:
        reviews.reply(fb, request.POST.get("reply", ""))
        messages.success(request, "Your reply is now on your profile.")
    except FlowError as e:
        messages.error(request, str(e))
    return redirect("pro_reviews")


@photographer_required
@require_POST
def pro_delivery(request, pk):
    booking = get_object_or_404(Booking.objects.select_related("user", "photographer"),
                                pk=pk, photographer__user=request.user)
    try:
        booking_flow.set_delivery(booking, request.POST.get("delivery_link", ""), request.POST.get("delivery_note", ""))
        messages.success(request, "Link saved. The customer has been told their photos are ready.")
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


@photographer_required
def pro_availability(request):
    ph = _own_profile(request)
    if ph is None:
        messages.info(request, "Create your profile first.")
        return redirect("pro_profile")
    form = BlockDatesForm(request.POST or None, photographer=ph)
    if request.method == "POST" and form.is_valid():
        n = availability.block_days(ph, form.cleaned_data["start"], form.cleaned_data["end"], form.cleaned_data["reason"].strip())
        messages.success(request, f"{n} day{'' if n == 1 else 's'} blocked." if n else "Those days were already blocked.")
        return redirect("pro_availability")
    context = {
        "ph": ph, "form": form,
        "calendar_months": availability.months(ph, 6, private=True),
        "blocks": ph.blocked_dates.filter(date__gte=timezone.localdate())[:60],
    }
    return render(request, "photopholio/pro_availability.html", context)


@photographer_required
@require_POST
def pro_unblock(request, pk):
    block = get_object_or_404(BlockedDate, pk=pk, photographer__user=request.user)
    block.delete()
    messages.info(request, "Day is open for bookings again.")
    return redirect("pro_availability")


@photographer_required
def pro_packages(request):
    ph = _own_profile(request)
    if ph is None:
        messages.info(request, "Create your profile first.")
        return redirect("pro_profile")
    tiers = [tier for tier, _ in Package.TIER_CHOICES]
    existing = {p.tier: p for p in ph.packages.all()}
    forms_by_tier = {tier: PackageForm(instance=existing.get(tier), prefix=tier) for tier in tiers}

    if request.method == "POST":
        tier = request.POST.get("tier")
        if tier not in tiers:
            raise Http404("Unknown package")
        if request.POST.get("action") == "remove":
            if tier in existing:
                existing[tier].delete()
                messages.info(request, f"{dict(Package.TIER_CHOICES)[tier]} package removed. Bookings already made keep their price.")
            return redirect("pro_packages")
        form = PackageForm(request.POST, instance=existing.get(tier), prefix=tier)
        if form.is_valid():
            package = form.save(commit=False)
            package.photographer, package.tier = ph, tier
            package.save()
            messages.success(request, f"{package.get_tier_display()} package saved.")
            return redirect("pro_packages")
        forms_by_tier[tier] = form

    rows = [{"tier": tier, "label": dict(Package.TIER_CHOICES)[tier], "form": forms_by_tier[tier], "package": existing.get(tier)}
            for tier in tiers]
    return render(request, "photopholio/pro_packages.html", {"ph": ph, "rows": rows})
