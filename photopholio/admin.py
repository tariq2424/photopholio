from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from . import booking_flow
from .models import (Booking, City, ContactUs, Country, Feedback, Payment,
                     Photo, Photographer, Portfolio, State, UserProfile)


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
    list_display = ["name", "status_badge", "specialities", "city", "rating", "price_per_day", "is_available", "login_account", "cancellations"]
    list_filter = ["status", "specialities", "city", "is_available"]
    list_editable = ["is_available", "price_per_day"]
    search_fields = ["name", "bio", "user__username"]
    list_per_page = 20
    autocomplete_fields = ["user"]
    inlines = [PortfolioInline]
    actions = ["approve_selected", "reject_selected", "move_to_pending"]
    readonly_fields = ["rating", "reviewed_by", "reviewed_at", "review_checklist", "portfolio_preview"]
    fieldsets = [
        ("Review", {"fields": ["status", "rejection_reason", "reviewed_by", "reviewed_at",
                               "review_checklist", "portfolio_preview"]}),
        ("Profile", {"fields": ["name", "slug", "user", "specialities", "city", "experience_years",
                                "price_per_day", "bio", "image", "is_available", "rating"]}),
    ]

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

    # --- bulk actions ---
    @admin.action(description="Approve selected photographers")
    def approve_selected(self, request, queryset):
        n = queryset.update(status="approved", rejection_reason="", reviewed_by=request.user, reviewed_at=timezone.now())
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
                n = queryset.update(status="rejected", rejection_reason=reason,
                                    reviewed_by=request.user, reviewed_at=timezone.now())
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


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ["user", "photographer", "event_date", "status", "cancelled_by", "cancelled_at", "created_at"]
    list_filter = ["status", "cancelled_by", "event_date", "photographer"]
    search_fields = ["user__username", "photographer__name"]
    readonly_fields = ["accepted_at", "cancelled_by", "cancelled_at", "cancel_reason"]
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
        n = queryset.filter(status="confirmed").update(status="completed")
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
        n = queryset.filter(refund_status="pending").update(refund_status="refunded", refunded_at=timezone.now())
        self.message_user(request, f"{n} refund(s) marked as processed.", messages.SUCCESS)


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ["user", "booking", "rating", "review_date"]
    list_filter = ["rating"]


@admin.register(ContactUs)
class ContactUsAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "subject", "created_at"]
    search_fields = ["name", "email", "subject"]
    date_hierarchy = "created_at"
