"""
Every change to a booking's state goes through here, so the customer pages,
the photographer dashboard and the admin all follow the same rules.

pending -> accepted -> confirmed -> completed
   |          |            |
   +-> declined          cancelled (by customer / photographer / admin / system)

Emails are sent after the database change has been saved, and a failing mail server never undoes it.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import emails, policy
from .models import BlockedDate, Booking, RescheduleRequest


class FlowError(Exception):
    """A rule was broken. The message is safe to show to the user."""


def expire_stale():
    """Cancel accepted bookings whose payment window has passed. Returns how many lapsed."""
    now = timezone.now()
    cutoff = now - timedelta(hours=policy.PAYMENT_WINDOW_HOURS)
    stale = list(Booking.objects.filter(status="accepted", accepted_at__lt=cutoff)
                 .select_related("user", "photographer__user"))
    if not stale:
        return 0
    Booking.objects.filter(pk__in=[b.pk for b in stale]).update(
        status="cancelled", cancelled_by="system", cancelled_at=now,
        cancel_reason=f"Payment was not made within {policy.PAYMENT_WINDOW_HOURS} hours of acceptance.")
    RescheduleRequest.objects.filter(booking__in=stale, status="pending").update(status="closed", decided_at=now)
    for booking in stale:
        booking.refresh_from_db()
        emails.booking_cancelled(booking, "system")
    return len(stale)


def _close(booking, status, by, reason=""):
    booking.status = status
    booking.cancelled_by = by
    booking.cancelled_at = timezone.now()
    booking.cancel_reason = reason
    booking.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancel_reason"])
    # A cancelled booking cannot be moved: close any open reschedule request.
    booking.reschedules.filter(status="pending").update(status="closed", decided_at=timezone.now())


def _start_refund(payment, percent, note):
    payment.refund_amount = payment.amount * percent // 100
    payment.refund_status = "pending" if payment.refund_amount else "none"
    payment.refund_note = note
    payment.save(update_fields=["refund_amount", "refund_status", "refund_note"])
    return payment.refund_amount


def accept(booking):
    if booking.status != "pending":
        raise FlowError("This request is no longer waiting for your answer.")
    with transaction.atomic():
        taken = Booking.objects.filter(photographer=booking.photographer, event_date=booking.event_date,
                                       status__in=["accepted", "confirmed"]).exclude(pk=booking.pk).exists()
        if taken:
            raise FlowError("You already have a booking on that date.")
        if BlockedDate.objects.filter(photographer=booking.photographer, date=booking.event_date).exists():
            raise FlowError("You blocked that day in your availability calendar. Remove the block first.")
        now = timezone.now()
        booking.status = "accepted"
        booking.accepted_at = now
        booking.save(update_fields=["status", "accepted_at"])
        # Other customers who asked for the same date can no longer have it.
        rivals = list(Booking.objects.filter(photographer=booking.photographer, event_date=booking.event_date,
                                             status="pending").exclude(pk=booking.pk).select_related("user"))
        Booking.objects.filter(pk__in=[r.pk for r in rivals]).update(
            status="declined", cancelled_by="system", cancelled_at=now,
            cancel_reason="The photographer accepted another booking for this date.")
    emails.booking_accepted(booking)
    for rival in rivals:
        rival.refresh_from_db()
        emails.booking_declined(rival)


def decline(booking, reason=""):
    if booking.status != "pending":
        raise FlowError("This request is no longer waiting for your answer.")
    _close(booking, "declined", "photographer", reason.strip())
    emails.booking_declined(booking)


def mark_completed(booking):
    if booking.status != "confirmed":
        raise FlowError("Only a confirmed booking can be completed.")
    if not booking.can_complete:
        raise FlowError("You can mark a shoot as completed on or after its event date.")
    booking.status = "completed"
    booking.save(update_fields=["status"])
    emails.booking_completed(booking)


def cancel_by_customer(booking, today=None):
    """Returns the refund amount (0 if nothing was paid or the policy gives nothing back)."""
    if booking.status not in ("pending", "accepted", "confirmed"):
        raise FlowError("This booking can no longer be cancelled.")
    refund = 0
    with transaction.atomic():
        paid = booking.paid_payment
        _close(booking, "cancelled", "customer", "Cancelled by the customer.")
        if paid:
            percent = policy.refund_percent(booking.event_date, today)
            days = policy.days_before(booking.event_date, today)
            refund = _start_refund(paid, percent, f"Customer cancelled {days} day(s) before the event: {percent}% refund")
    emails.booking_cancelled(booking, "customer")
    return refund


def cancel_by_photographer(booking, reason):
    if booking.status not in ("accepted", "confirmed"):
        raise FlowError("Only an accepted or confirmed booking can be cancelled. Decline pending requests instead.")
    if not (reason or "").strip():
        raise FlowError("Please write a reason. The customer will see it.")
    with transaction.atomic():
        paid = booking.paid_payment
        _close(booking, "cancelled", "photographer", reason.strip())
        if paid:
            _start_refund(paid, 100, "Photographer cancelled: full refund")
    emails.booking_cancelled(booking, "photographer")


def admin_refund_in_full(booking, reason="Cancelled by admin after a complaint (no-show or dispute)."):
    """For no-shows and disputes after the admin has looked into it."""
    paid = booking.paid_payment
    if booking.status not in ("confirmed", "completed") or not paid or paid.refund_status != "none":
        raise FlowError("Needs a paid, confirmed or completed booking that has not been refunded.")
    with transaction.atomic():
        _close(booking, "cancelled", "admin", reason)
        _start_refund(paid, 100, "Admin refund in full (no-show or dispute)")
    emails.booking_cancelled(booking, "admin")


def process_refund(payment):
    """Admin confirms the money has gone back to the customer."""
    if payment.refund_status != "pending":
        raise FlowError("This payment has no pending refund.")
    payment.refund_status = "refunded"
    payment.refunded_at = timezone.now()
    payment.save(update_fields=["refund_status", "refunded_at"])
    emails.refund_processed(payment)


# ---------------------------------------------------------------- rescheduling

def request_reschedule(booking, new_date, reason="", today=None):
    """Customer asks to move an accepted / confirmed booking to `new_date`."""
    from . import availability
    today = today or timezone.localdate()
    if booking.status not in ("accepted", "confirmed"):
        raise FlowError("Only an accepted or confirmed booking can be rescheduled.")
    if booking.pending_reschedule:
        raise FlowError("You already have a reschedule request waiting for the photographer.")
    if booking.reschedule_count >= policy.MAX_RESCHEDULES:
        raise FlowError("This booking has already been rescheduled once. You can cancel it instead.")
    if policy.days_before(booking.event_date, today) < policy.RESCHEDULE_MIN_DAYS:
        raise FlowError(f"A booking can be rescheduled only {policy.RESCHEDULE_MIN_DAYS} or more days before the event.")
    if new_date == booking.event_date:
        raise FlowError("That is the same date as your booking. Choose a different day.")
    if new_date <= today:
        raise FlowError("Choose a date in the future.")
    if not availability.is_free(booking.photographer, new_date, today):
        raise FlowError("The photographer is not free on that day. Check their calendar and pick another.")
    req = RescheduleRequest.objects.create(booking=booking, old_date=booking.event_date,
                                           new_date=new_date, reason=(reason or "").strip()[:255])
    emails.reschedule_requested(req)
    return req


def withdraw_reschedule(req):
    req.refresh_from_db()
    if req.status != "pending":
        raise FlowError("This request is no longer open.")
    req.status = "withdrawn"
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decided_at"])


def accept_reschedule(req, today=None):
    from . import availability
    today = today or timezone.localdate()
    now = timezone.now()
    with transaction.atomic():
        req = RescheduleRequest.objects.select_for_update().select_related("booking__photographer", "booking__user").get(pk=req.pk)
        booking = req.booking
        if req.status != "pending":
            raise FlowError("This request is no longer waiting for your answer.")
        if booking.status not in ("accepted", "confirmed"):
            raise FlowError("The booking is no longer active.")
        if req.new_date <= today or not availability.is_free(booking.photographer, req.new_date, today):
            raise FlowError("You are no longer free on that date. Decline the request or free the day first.")
        booking.event_date = req.new_date
        booking.save(update_fields=["event_date"])
        req.status = "accepted"
        req.decided_at = now
        req.save(update_fields=["status", "decided_at"])
        # The new day is now taken: other pending requests for it can no longer be met.
        rivals = list(Booking.objects.filter(photographer=booking.photographer, event_date=req.new_date,
                                             status="pending").exclude(pk=booking.pk).select_related("user"))
        Booking.objects.filter(pk__in=[r.pk for r in rivals]).update(
            status="declined", cancelled_by="system", cancelled_at=now,
            cancel_reason="The photographer accepted another booking for this date.")
        RescheduleRequest.objects.filter(status="pending", new_date=req.new_date,
                                         booking__photographer=booking.photographer).exclude(pk=req.pk).update(
            status="declined", decline_reason="The photographer is no longer free on that date.", decided_at=now)
    emails.reschedule_accepted(req)
    for rival in rivals:
        rival.refresh_from_db()
        emails.booking_declined(rival)
    req.refresh_from_db()
    return req


def decline_reschedule(req, reason=""):
    req.refresh_from_db()
    if req.status != "pending":
        raise FlowError("This request is no longer waiting for your answer.")
    req.status = "declined"
    req.decline_reason = (reason or "").strip()[:255]
    req.decided_at = timezone.now()
    req.save(update_fields=["status", "decline_reason", "decided_at"])
    emails.reschedule_declined(req)


# ---------------------------------------------------------------- delivering the photos

DELIVERY_SCHEMES = ("http", "https")


def set_delivery(booking, link, note=""):
    """The photographer shares a link to the finished photos. Sending a new link replaces the old one."""
    from django.core.exceptions import ValidationError
    from django.core.validators import URLValidator
    link = (link or "").strip()
    if booking.status != "completed":
        raise FlowError("You can share the photos once the booking is marked as completed.")
    if not link:
        raise FlowError("Paste the link to the photos.")
    try:
        URLValidator(schemes=DELIVERY_SCHEMES)(link)
    except ValidationError:
        raise FlowError("That does not look like a web link. It should start with https://")
    if len(link) > 500:
        raise FlowError("That link is too long.")
    booking.delivery_link = link
    booking.delivery_note = (note or "").strip()[:255]
    booking.delivered_at = timezone.now()
    booking.save(update_fields=["delivery_link", "delivery_note", "delivered_at"])
    emails.photos_delivered(booking)
