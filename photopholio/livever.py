"""A short fingerprint of everything on screen that can change under a user's feet
(their bookings, shoot progress, payments, reschedules, notifications).
The page compares it with the last one it saw and refreshes itself in place when it differs."""
import hashlib

from django.db.models import Count, Max, Q

from .models import AlbumOrder, HelpRequest, Booking, Notification, Payment, RescheduleRequest, ShootDay, ShootEvent


def live_version(user):
    if not getattr(user, "is_authenticated", False):
        return ""
    mine = Booking.objects.filter(Q(user=user) | Q(photographer__user=user))
    ids = list(mine.values_list("pk", flat=True))
    parts = [list(mine.order_by("pk").values_list(
        "pk", "status", "payout_status", "dispute_status", "accepted_at", "completed_at", "customer_confirmed_at",
        "cancelled_at", "delivered_at", "price"))]
    if ids:
        parts.append(list(ShootDay.objects.filter(booking_id__in=ids).order_by("pk").values_list(
            "pk", "arrived_at", "started_at", "finished_at", "customer_seen_at")))
        parts.append(list(ShootEvent.objects.filter(booking_id__in=ids).aggregate(n=Count("id"), m=Max("id")).values()))
        parts.append(list(RescheduleRequest.objects.filter(booking_id__in=ids).order_by("pk").values_list("pk", "status")))
        parts.append(list(Payment.objects.filter(booking_id__in=ids).order_by("pk").values_list("pk", "status", "refund_amount")))
    parts.append(list(AlbumOrder.objects.filter(Q(booking__user=user) | Q(booking__photographer__user=user)).order_by("pk").values_list(
        "pk", "status", "selection_status", "design_status", "production_status", "payout_status", "payout_paid_at", "delivered_at")))
    parts.append(list(HelpRequest.objects.filter(user=user).order_by("pk").values_list("pk", "is_open", "replied_at")))
    parts.append(list(Notification.objects.filter(user=user).aggregate(
        n=Count("id"), m=Max("id"), u=Count("id", filter=Q(is_read=False))).values()))
    return hashlib.md5(repr(parts).encode()).hexdigest()[:12]


def admin_version():
    """Same idea for the admin panel: one fingerprint of everything staff act on or wait for."""
    from django.contrib.auth.models import User
    from .models import ContactUs, Feedback, Photographer
    parts = [
        list(Booking.objects.order_by("pk").values_list("pk", "status", "payout_status", "dispute_status", "accepted_at", "completed_at", "delivered_at")),
        list(Payment.objects.order_by("pk").values_list("pk", "status", "refund_status", "refund_amount")),
        list(AlbumOrder.objects.order_by("pk").values_list("pk", "status", "design_status", "production_status", "payout_status", "delivered_at")),
        list(HelpRequest.objects.order_by("pk").values_list("pk", "is_open", "replied_at")),
        list(Photographer.objects.order_by("pk").values_list("pk", "status")),
        list(RescheduleRequest.objects.order_by("pk").values_list("pk", "status")),
        [User.objects.count(), ContactUs.objects.count(), Feedback.objects.count()],
    ]
    return hashlib.md5(repr(parts).encode()).hexdigest()[:12]
