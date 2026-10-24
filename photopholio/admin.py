from django import forms
from django.contrib import admin, messages
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.admin import helpers
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from . import analytics, booking_flow, emails, money, reviews, tracking
from .throttle import ThrottledLoginMixin
from .models import (Favourite, RescheduleRequest, Booking, City, ContactUs, Country, Feedback, BlockedDate, LoginAttempt, Package, Payment,
                     Photo, Photographer, Portfolio, ShootDay, ShootEvent, State, UserProfile)


def thumb(image, size=70):
    # format_html is the safe modern replacement for mark_safe(): it escapes the values.
    if image:
        return format_html('<img src="{}" style="height:{}px;border-radius:6px">', image.url, size)
    return "-"


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(State)
class StateAdmin(admin.ModelAdmin):
    list_display = ["name", "country"]
    list_filter = ["country"]
    search_fields = ["name"]


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ["name", "state"]
    list_filter = ["state__country", "state"]
    search_fields = ["name"]


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "phone_no", "city", "state", "country", "photo"]
    list_filter = ["role", "country", "state"]
    search_fields = ["user__username", "user__email"]

    @admin.display(description="Photo")
    def photo(self, obj):
        return thumb(obj.image, 40)


class PackageInline(admin.TabularInline):
    model = Package
    extra = 0


class PortfolioInline(admin.TabularInline):
    model = Portfolio
    extra = 0


STATUS_COLOURS = {"draft": "#6c757d", "pending": "#d98f00", "approved": "#198754", "rejected": "#dc3545"}


class PhotographerAdminForm(forms.ModelForm):
    class Meta:
        model = Photographer
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") == "rejected" and not (cleaned.get("rejection_reason") or "").strip():
            self.add_error("rejection_reason", "Write a short reason so the photographer knows what to fix.")
        return cleaned


@admin.register(Photographer)
class PhotographerAdmin(admin.ModelAdmin):
    form = PhotographerAdminForm
    list_display = ["name", "status_badge", "is_verified", "specialities", "city", "rating", "price_per_day", "is_available", "login_account", "cancellations"]
    list_filter = ["status", "is_verified", "specialities", "city", "is_available"]
    list_editable = ["is_available", "price_per_day"]
    search_fields = ["name", "bio", "tags", "user__username"]
    list_per_page = 20
    autocomplete_fields = ["user"]
    inlines = [PackageInline, PortfolioInline]
    actions = ["approve_selected", "reject_selected", "move_to_pending", "mark_verified", "remove_verified"]
    readonly_fields = ["rating", "reviewed_by", "reviewed_at", "verified_at", "review_checklist", "portfolio_preview"]
    fieldsets = [
        ("Review", {"fields": ["status", "rejection_reason", "reviewed_by", "reviewed_at",
                               "review_checklist", "portfolio_preview"]}),
        ("Profile", {"fields": ["name", "slug", "user", "specialities", "city", "experience_years",
                                "price_per_day", "bio", "tags", "image", "is_available", "rating"]}),
        ("Money", {"classes": ["collapse"], "fields": ["commission_percent", "payout_details"]}),
        ("Travel policy", {"classes": ["collapse"], "fields": ["travel_scope", "travel_fee_state", "travel_fee_india", "stay_fee_per_day"]}),
        ("Trust", {"fields": ["is_verified", "verified_at"]}),
    ]

    def save_model(self, request, obj, form, change):
        if "is_verified" in form.changed_data:
            obj.verified_at = timezone.now() if obj.is_verified else None
        super().save_model(request, obj, form, change)

    @admin.action(description="Give the Verified badge")
    def mark_verified(self, request, queryset):
        n = queryset.filter(is_verified=False).update(is_verified=True, verified_at=timezone.now())
        self.message_user(request, f"{n} photographer(s) are now verified.", messages.SUCCESS)

    @admin.action(description="Take the Verified badge away")
    def remove_verified(self, request, queryset):
        n = queryset.filter(is_verified=True).update(is_verified=False, verified_at=None)
        self.message_user(request, f"{n} photographer(s) are no longer verified.", messages.SUCCESS)

    # --- list helpers ---
    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html('<span style="background:{};color:#fff;padding:2px 9px;border-radius:10px;font-size:12px">{}</span>',
                           STATUS_COLOURS.get(obj.status, "#6c757d"), obj.get_status_display())

    @admin.display(description="Cancelled by photographer")
    def cancellations(self, obj):
        return obj.cancellations_count()

    @admin.display(description="Login account")
    def login_account(self, obj):
        return obj.user.username if obj.user_id else "-"

    # --- review page helpers ---
    @admin.display(description="Checklist")
    def review_checklist(self, obj):
        if not obj.pk:
            return "-"
        return format_html_join("", '<div>{} {}</div>', (("\u2705" if ok else "\u274c", label) for label, ok in obj.checklist()))

    @admin.display(description="Portfolio photos")
    def portfolio_preview(self, obj):
        if not obj.pk:
            return "-"
        blocks = []
        for pf in obj.portfolios.prefetch_related("photos"):
            imgs = format_html_join("", '<a href="{}" target="_blank"><img src="{}" style="height:90px;margin:2px;border-radius:6px"></a>',
                                    ((ph.image.url, ph.image.url) for ph in pf.photos.all()[:12]))
            blocks.append(format_html("<div style='margin-bottom:8px'><strong>{}</strong><br>{}</div>", pf.title, imgs))
        return format_html_join("", "{}", ((b,) for b in blocks)) if blocks else "No photos uploaded yet."

    # --- record who reviewed and when ---
    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data and obj.status in ("approved", "rejected"):
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
            if obj.status == "approved":
                obj.rejection_reason = ""
        super().save_model(request, obj, form, change)
        if change and "status" in form.changed_data:
            if obj.status == "approved":
                emails.profile_approved(obj)
            elif obj.status == "rejected":
                emails.profile_rejected(obj)

    # --- bulk actions ---
    @admin.action(description="Approve selected photographers")
    def approve_selected(self, request, queryset):
        newly = list(queryset.exclude(status="approved").values_list("pk", flat=True))
        n = queryset.update(status="approved", rejection_reason="", reviewed_by=request.user, reviewed_at=timezone.now())
        for ph in Photographer.objects.filter(pk__in=newly).select_related("user"):
            emails.profile_approved(ph)
        self.message_user(request, f"{n} profile(s) approved. They are now visible to customers.", messages.SUCCESS)

    @admin.action(description="Move selected back to pending review")
    def move_to_pending(self, request, queryset):
        n = queryset.update(status="pending")
        self.message_user(request, f"{n} profile(s) moved to pending.", messages.SUCCESS)

    @admin.action(description="Reject selected photographers (asks for a reason)")
    def reject_selected(self, request, queryset):
        if "apply" in request.POST:
            reason = request.POST.get("reason", "").strip()
            if reason:
                newly = list(queryset.exclude(status="rejected").values_list("pk", flat=True))
                n = queryset.update(status="rejected", rejection_reason=reason,
                                    reviewed_by=request.user, reviewed_at=timezone.now())
                for ph in Photographer.objects.filter(pk__in=newly).select_related("user"):
                    emails.profile_rejected(ph)
                self.message_user(request, f"{n} profile(s) rejected. The reason is shown to the photographer.", messages.SUCCESS)
                return None
            self.message_user(request, "Please write a reason before rejecting.", messages.ERROR)
        context = {
            **self.admin_site.each_context(request),
            "title": "Reject photographer profiles",
            "queryset": queryset,
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/photopholio/reject_reason.html", context)


class RoleUserAdmin(UserAdmin):
    """Same Users page as before, plus a Role column and filter."""
    list_display = UserAdmin.list_display + ("role",)
    list_filter = UserAdmin.list_filter + ("profile__role",)

    @admin.display(description="Role")
    def role(self, obj):
        profile = getattr(obj, "profile", None)
        return profile.get_role_display() if profile else "Customer"


admin.site.unregister(User)
admin.site.register(User, RoleUserAdmin)


class PhotoInline(admin.TabularInline):
    model = Photo
    extra = 1


@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin):
    list_display = ["title", "photographer", "created_at"]
    list_filter = ["photographer", "created_at"]
    search_fields = ["title", "photographer__name"]
    inlines = [PhotoInline]


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ["preview", "portfolio", "order_id", "status", "uploaded_at"]
    list_filter = ["status", "portfolio"]
    list_editable = ["status", "order_id"]
    list_display_links = ["preview"]

    @admin.display(description="Preview")
    def preview(self, obj):
        return thumb(obj.image)


class ShootDayInline(admin.TabularInline):
    model = ShootDay
    extra = 0
    can_delete = False
    readonly_fields = ["date", "arrived_at", "started_at", "finished_at", "customer_seen_at"]
    fields = readonly_fields
    verbose_name_plural = "Shoot days (what the photographer tapped)"

    def has_add_permission(self, request, obj=None):
        return False


class ShootEventInline(admin.TabularInline):
    model = ShootEvent
    extra = 0
    can_delete = False
    readonly_fields = ["created_at", "actor_role", "actor", "action", "day", "note"]
    fields = readonly_fields
    verbose_name_plural = "Tracking history (who did what, and when)"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    inlines = [ShootDayInline, ShootEventInline]
    list_display = ["user", "photographer", "package_name", "price", "travel_fee", "event_date", "end_date", "event_city", "status",
                    "platform_fee", "payout_amount", "payout_status", "created_at"]
    list_filter = ["status", "payout_status", "dispute_status", "cancelled_by", "event_date", "photographer"]
    search_fields = ["user__username", "photographer__name"]
    readonly_fields = ["package", "package_name", "price", "day_rate", "travel_fee", "travel_zone", "accepted_at", "cancelled_by", "cancelled_at", "cancel_reason",
                       "delivery_link", "delivery_note", "delivered_at", "commission_percent", "platform_fee", "payout_amount",
                       "payout_status", "payout_reference", "payout_paid_at", "payout_note",
                       "completed_at", "customer_confirmed_at", "customer_confirmed_by_customer", "dispute_status", "dispute_kind",
                       "dispute_text", "dispute_opened_at", "dispute_outcome", "dispute_note", "dispute_resolved_at", "dispute_resolved_by"]
    actions = ["refund_in_full", "mark_completed"]
    # Status is deliberately not editable from the list: changing it by hand would skip the refund rules.
    # Use the actions below instead.

    @admin.action(description="Cancel and refund in full (no-show or dispute)")
    def refund_in_full(self, request, queryset):
        done, skipped = 0, 0
        for booking in queryset:
            try:
                booking_flow.admin_refund_in_full(booking)
                done += 1
            except booking_flow.FlowError:
                skipped += 1
        if done:
            self.message_user(request, f"{done} booking(s) cancelled with a full refund (now pending).", messages.SUCCESS)
        if skipped:
            self.message_user(request, f"{skipped} skipped: needs a paid booking that is confirmed or completed and not already refunded.", messages.WARNING)

    @admin.action(description="Mark selected bookings as completed")
    def mark_completed(self, request, queryset):
        todo = list(queryset.filter(status="confirmed").select_related("user", "photographer"))
        now = timezone.now()
        # The admin's own decision counts as the confirmation: the money becomes payable straight away.
        n = queryset.filter(pk__in=[b.pk for b in todo]).update(status="completed", completed_at=now, customer_confirmed_at=now)
        for b in todo:
            b.status, b.completed_at, b.customer_confirmed_at = "completed", now, now
            tracking.log(b, "admin", "marked_completed", actor=request.user, note="Marked completed by the admin.")
            money.on_completed(b)
            emails.booking_completed(b)
        self.message_user(request, f"{n} booking(s) marked completed.", messages.SUCCESS)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["user", "booking", "amount", "payment_method", "status", "refund_status", "refund_amount", "payment_date"]
    list_filter = ["refund_status", "payment_method", "status"]
    search_fields = ["user__username"]
    readonly_fields = ["refund_amount", "refund_note", "refunded_at"]
    actions = ["mark_refund_processed"]

    @admin.action(description="Mark selected refunds as processed")
    def mark_refund_processed(self, request, queryset):
        n = 0
        for payment in queryset.filter(refund_status="pending").select_related("booking__user", "booking__photographer"):
            booking_flow.process_refund(payment)
            n += 1
        self.message_user(request, f"{n} refund(s) marked as processed.", messages.SUCCESS)


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ["user", "booking", "rating", "short_comment", "is_hidden", "has_reply", "review_date"]
    list_filter = ["is_hidden", "rating"]
    search_fields = ["user__username", "comment", "booking__photographer__name"]
    actions = ["hide_selected", "show_selected"]
    readonly_fields = ["user", "booking", "rating", "comment", "review_date", "reply", "replied_at"]
    fields = ["user", "booking", "rating", "comment", "review_date", "reply", "replied_at", "is_hidden"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Review")
    def short_comment(self, obj):
        return (obj.comment[:60] + "...") if len(obj.comment) > 60 else obj.comment

    @admin.display(description="Replied", boolean=True)
    def has_reply(self, obj):
        return bool(obj.reply)

    @admin.action(description="Hide selected reviews (also removes them from the rating)")
    def hide_selected(self, request, queryset):
        n = reviews.set_hidden(queryset, True)
        self.message_user(request, f"{n} review(s) hidden.", messages.SUCCESS)

    @admin.action(description="Show selected reviews again")
    def show_selected(self, request, queryset):
        n = reviews.set_hidden(queryset, False)
        self.message_user(request, f"{n} review(s) visible again.", messages.SUCCESS)


@admin.register(Favourite)
class FavouriteAdmin(admin.ModelAdmin):
    list_display = ["user", "photographer", "created_at"]
    search_fields = ["user__username", "photographer__name"]

    def has_add_permission(self, request):
        return False


@admin.register(ContactUs)
class ContactUsAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "subject", "created_at"]
    search_fields = ["name", "email", "subject"]
    date_hierarchy = "created_at"


# --- Analytics page: /admin/analytics/ -------------------------------------------

def analytics_view(request):
    if not request.user.has_perm("photopholio.view_booking"):
        raise PermissionDenied
    context = {**admin.site.each_context(request), "title": "Analytics", "data": analytics.dashboard_data()}
    return TemplateResponse(request, "admin/photopholio/analytics.html", context)


# --- Payouts page: /admin/payouts/ ------------------------------------------------
# Where the admin pays photographers (by UPI / bank transfer, outside the site) and records the reference.

def _can_manage_payouts(request):
    return request.user.has_perm("photopholio.change_booking")


def payouts_view(request):
    if not _can_manage_payouts(request):
        raise PermissionDenied
    bookings = Booking.objects.select_related("photographer", "user")
    ready = list(bookings.filter(payout_status="ready").order_by("photographer__name", "event_date"))
    by_photographer = {}
    for b in ready:
        group = by_photographer.setdefault(b.photographer_id, {"photographer": b.photographer, "bookings": [], "total": 0})
        group["bookings"].append(b)
        group["total"] += b.payout_amount
    context = {
        **admin.site.each_context(request), "title": "Payouts",
        "groups": list(by_photographer.values()),
        "ready_total": sum(b.payout_amount for b in ready),
        "recover": list(bookings.filter(payout_status="recover").order_by("-event_date")),
        "held": money.totals(Booking.objects.all()).get("held", {"n": 0, "money": 0, "fee": 0}),
        "recent": list(bookings.filter(payout_status="paid").order_by("-payout_paid_at")[:25]),
        "percent": money.default_percent(),
    }
    return TemplateResponse(request, "admin/photopholio/payouts.html", context)


def payout_pay_view(request, pk):
    if not _can_manage_payouts(request):
        raise PermissionDenied
    if request.method != "POST":
        return redirect("admin:photopholio_payouts")
    booking = get_object_or_404(Booking.objects.select_related("photographer", "user"), pk=pk)
    try:
        money.mark_paid(booking, request.POST.get("reference", ""))
        messages.success(request, f"Marked as paid: Rs.{booking.payout_amount} to {booking.photographer.name}. They were emailed.")
    except booking_flow.FlowError as e:
        messages.error(request, str(e))
    return redirect("admin:photopholio_payouts")


def payout_recovered_view(request, pk):
    if not _can_manage_payouts(request):
        raise PermissionDenied
    if request.method != "POST":
        return redirect("admin:photopholio_payouts")
    booking = get_object_or_404(Booking, pk=pk)
    try:
        money.mark_recovered(booking, request.POST.get("note", ""))
        messages.success(request, "Marked as recovered.")
    except booking_flow.FlowError as e:
        messages.error(request, str(e))
    return redirect("admin:photopholio_payouts")


def disputes_view(request):
    if not _can_manage_payouts(request):
        raise PermissionDenied
    bookings = Booking.objects.select_related("photographer", "user")
    open_ones = list(bookings.filter(dispute_status="open").prefetch_related("shoot_days", "shoot_events").order_by("dispute_opened_at"))
    for b in open_ones:
        b.timeline = list(b.shoot_events.all())
        b.days_view = b.day_states()
    waiting = list(bookings.filter(status="completed", completed_at__isnull=False, customer_confirmed_at__isnull=True)
                   .exclude(dispute_status="open").order_by("completed_at"))
    recent = list(bookings.filter(dispute_status="resolved").exclude(dispute_outcome="closed").order_by("-dispute_resolved_at")[:15])
    return TemplateResponse(request, "admin/photopholio/disputes.html", {
        **admin.site.each_context(request), "title": "Reported problems", "open": open_ones, "waiting": waiting, "recent": recent})


def dispute_resolve_view(request, pk):
    if not _can_manage_payouts(request):
        raise PermissionDenied
    if request.method != "POST":
        return redirect("admin:photopholio_disputes")
    get_object_or_404(Booking, pk=pk)
    try:
        booking = tracking.resolve_dispute(pk, request.user, request.POST.get("outcome", ""), request.POST.get("note", ""))
        messages.success(request, f"Decision saved for booking #{pk}: {booking.get_dispute_outcome_display()}. Both people were emailed.")
    except booking_flow.FlowError as e:
        messages.error(request, str(e))
    return redirect("admin:photopholio_disputes")


def _csv_safe(value):
    """A cell that starts with = + - or @ would run as a formula in Excel, so it gets a quote in front."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def payouts_csv_view(request):
    if not _can_manage_payouts(request):
        raise PermissionDenied
    import csv
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="photopholio-payouts.csv"'
    out = csv.writer(response)
    out.writerow(["Booking", "Photographer", "Customer", "Event", "Days", "Customer paid", "Service price", "Travel",
                  "Commission %", "Commission", "Photographer gets", "Status", "Transfer reference", "Paid on"])
    for b in Booking.objects.exclude(payout_status="").select_related("photographer", "user").order_by("-event_date"):
        out.writerow([_csv_safe(v) for v in [
            b.pk, b.photographer.name, b.user.username, b.date_label, b.days, b.amount, b.service_price, b.travel_fee,
            b.commission_percent, b.platform_fee, b.payout_amount, b.get_payout_status_display(), b.payout_reference,
            b.payout_paid_at.strftime("%Y-%m-%d") if b.payout_paid_at else ""]])
    return response


_original_get_urls = admin.site.get_urls


def _get_urls_with_analytics():
    wrap = admin.site.admin_view
    return [
        path("analytics/", wrap(analytics_view), name="photopholio_analytics"),
        path("payouts/", wrap(payouts_view), name="photopholio_payouts"),
        path("payouts/export/", wrap(payouts_csv_view), name="photopholio_payouts_csv"),
        path("disputes/", wrap(disputes_view), name="photopholio_disputes"),
        path("disputes/<int:pk>/resolve/", wrap(dispute_resolve_view), name="photopholio_dispute_resolve"),
        path("payouts/<int:pk>/pay/", wrap(payout_pay_view), name="photopholio_payout_pay"),
        path("payouts/<int:pk>/recovered/", wrap(payout_recovered_view), name="photopholio_payout_recovered"),
    ] + _original_get_urls()


admin.site.get_urls = _get_urls_with_analytics


# --- Login lock ---------------------------------------------------------------------

class ThrottledAdminLoginForm(ThrottledLoginMixin, AdminAuthenticationForm):
    """/admin/login/ gets the same lock as the site login."""


admin.site.login_form = ThrottledAdminLoginForm


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    """Read-only list of failed logins, with a way to unlock someone."""
    list_display = ["key", "ip_address", "created_at"]
    search_fields = ["key", "ip_address"]
    actions = ["unlock"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.action(description="Unlock the selected logins (forget their failed attempts)")
    def unlock(self, request, queryset):
        keys = list(queryset.values_list("key", flat=True).distinct())
        n = LoginAttempt.objects.filter(key__in=keys).delete()[0]
        self.message_user(request, f"Unlocked {len(keys)} login(s); {n} failed attempt(s) cleared.", messages.SUCCESS)


@admin.register(BlockedDate)
class BlockedDateAdmin(admin.ModelAdmin):
    list_display = ["photographer", "date", "reason"]
    list_filter = ["photographer"]
    search_fields = ["photographer__name", "reason"]
    date_hierarchy = "date"


@admin.register(RescheduleRequest)
class RescheduleRequestAdmin(admin.ModelAdmin):
    list_display = ["booking", "old_date", "new_date", "status", "created_at", "decided_at"]
    list_filter = ["status"]
    search_fields = ["booking__user__username", "booking__photographer__name"]
    # A record of what was asked and answered: changing it by hand would skip the availability rules.
    readonly_fields = ["booking", "old_date", "new_date", "reason", "status", "decline_reason", "created_at", "decided_at"]

    def has_add_permission(self, request):
        return False
