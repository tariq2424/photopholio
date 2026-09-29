from django.contrib import admin, messages
from django.utils.html import format_html

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
    list_display = ["user", "phone_no", "city", "state", "country", "photo"]
    list_filter = ["country", "state"]
    search_fields = ["user__username", "user__email"]

    @admin.display(description="Photo")
    def photo(self, obj):
        return thumb(obj.image, 40)


class PortfolioInline(admin.TabularInline):
    model = Portfolio
    extra = 0


@admin.register(Photographer)
class PhotographerAdmin(admin.ModelAdmin):
    list_display = ["name", "specialities", "city", "experience_years", "rating", "price_per_day", "is_available"]
    list_filter = ["specialities", "city", "is_available"]
    list_editable = ["is_available", "price_per_day"]
    search_fields = ["name", "bio"]
    list_per_page = 20
    readonly_fields = ["rating"]
    inlines = [PortfolioInline]


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
    list_display = ["user", "photographer", "event_date", "status", "created_at"]
    list_filter = ["status", "event_date", "photographer"]
    search_fields = ["user__username", "photographer__name"]
    list_editable = ["status"]
    actions = ["mark_completed"]

    @admin.action(description="Mark selected bookings as completed")
    def mark_completed(self, request, queryset):
        n = queryset.filter(status="confirmed").update(status="completed")
        self.message_user(request, f"{n} booking(s) marked completed.", messages.SUCCESS)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["user", "booking", "amount", "payment_method", "status", "payment_date"]
    list_filter = ["payment_method", "status"]
    search_fields = ["user__username"]


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ["user", "booking", "rating", "review_date"]
    list_filter = ["rating"]


@admin.register(ContactUs)
class ContactUsAdmin(admin.ModelAdmin):
    list_display = ["name", "email", "subject", "created_at"]
    search_fields = ["name", "email", "subject"]
    date_hierarchy = "created_at"
