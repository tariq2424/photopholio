"""Admin side of printed albums. Four steps only: Approved -> Printing -> On the way -> Delivered.

The photographer's design fee does not wait for any of this: it is released when the customer approves the design.
A late album is handled in the help centre (the customer writes, the admin answers).
"""
import secrets

from django.utils import timezone

from . import notifications
from .models import AlbumEvent

# status -> (timeline label, customer message)
STEPS = {
    "printing": ("Printing", "Your album is being printed."),
    "shipped": ("On the way", "Your album is on its way to you. Open your photos page for the tracking link."),
    "delivered": ("Delivered", "Your album is delivered. We hope you love it."),
}
ORDER = ["queued", "printing", "shipped", "delivered"]


def log(order, status, label):
    AlbumEvent.objects.create(order=order, status=status, label=label)


def _url(order):
    return f"/bookings/{order.booking_id}/gallery/"


def advance(order, status):
    """Move an album forward. Returns an error text, or None when it worked."""
    if status not in STEPS:
        return "Unknown step."
    if order.status != "paid":
        return "The album is not paid."
    if order.design_status != "approved":
        return "The customer has not approved the design yet."
    cur = ORDER.index(order.production_status) if order.production_status in ORDER else 0
    if ORDER.index(status) <= cur:
        return "That step is already done."
    if status == "shipped" and not order.tracking_url:
        return "Paste the tracking link first."
    if status == "delivered" and order.otp_given and order.otp_given != order.delivery_otp:
        return "The code does not match the customer's delivery code."
    order.production_status = status
    fields = ["production_status"]
    if status == "shipped":
        order.delivery_otp = order.delivery_otp or f"{secrets.randbelow(9000) + 1000}"
        fields.append("delivery_otp")
    if status == "delivered":
        order.delivered_at = timezone.now()
        fields.append("delivered_at")
    order.save(update_fields=fields)
    label, message = STEPS[status]
    log(order, status, label)
    notifications.notify(order.booking.user, f"Album update: {label.lower()}", message, _url(order), subject="photos")
    return None


_last_sweep = [0.0]


def sweep_late(force=False):
    """Alert once for every paid album that is past its delivery date and not delivered: the customer (with the Help center)
    and every admin. There is no timer on the server, so this runs whenever an admin page is open, when a customer opens
    the album, and from `python manage.py sweep_albums` (can be put on a daily schedule)."""
    import time
    from django.contrib.auth.models import User
    from .models import AlbumOrder
    if not force and time.time() - _last_sweep[0] < 60:
        return 0
    _last_sweep[0] = time.time()
    n = 0
    qs = AlbumOrder.objects.filter(status="paid", late_alerted=False, design_status="approved", delivered_at__isnull=True) \
        .exclude(production_status="delivered").select_related("booking__user", "booking__photographer")
    admins = list(User.objects.filter(is_staff=True, is_active=True))
    for o in qs:
        if not o.is_delayed:
            continue
        o.late_alerted = True
        o.save(update_fields=["late_alerted"])
        log(o, o.production_status or "queued", "Late")
        notifications.notify(o.booking.user, "Your album is late",
                             "It is taking longer than the 30 days we promised, and we are sorry. Ask in the Help center and we will tell you why.",
                             f"/help/?album={o.pk}&topic=late", subject="problem")
        for a in admins:
            notifications.notify(a, "Album is late", f"Album #{o.pk} ({o.booking.photographer.name}, {o.style_name}) is past its delivery date.",
                                 f"/admin/photopholio/albumorder/{o.pk}/change/", subject="problem")
        n += 1
    return n
