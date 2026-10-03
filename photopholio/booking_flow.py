"""
Every change to a booking's state goes through here, so the customer pages,
the photographer dashboard and the admin all follow the same rules.

pending -> accepted -> confirmed -> completed
   |          |            |
   +-> declined          cancelled (by customer / photographer / admin / system)
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import policy
from .models import Booking


class FlowError(Exception):
    """A rule was broken. The message is safe to show to the user."""


def expire_stale():
    """Cancel accepted bookings whose payment window has passed. Called before pages that show bookings."""
    now = timezone.now()
    cutoff = now - timedelta(hours=policy.PAYMENT_WINDOW_HOURS)
    return Booking.objects.filter(status="accepted", accepted_at__lt=cutoff).update(
        status="cancelled", cancelled_by="system", cancelled_at=now,
        cancel_reason=f"Payment was not made within {policy.PAYMENT_WINDOW_HOURS} hours of acceptance.")


def _close(booking, status, by, reason=""):
    booking.status = status
    booking.cancelled_by = by
    booking.cancelled_at = timezone.now()
    booking.cancel_reason = reason
    booking.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancel_reason"])


def _start_refund(payment, percent, note):
    payment.refund_amount = payment.amount * percent // 100
    payment.refund_status = "pending" if payment.refund_amount else "none"
    payment.refund_note = note
    payment.save(update_fields=["refund_amount", "refund_status", "refund_note"])
    return payment.refund_amount


@transaction.atomic
def accept(booking):
    if booking.status != "pending":
        raise FlowError("This request is no longer waiting for your answer.")
    taken = Booking.objects.filter(photographer=booking.photographer, event_date=booking.event_date,
                                   status__in=["accepted", "confirmed"]).exclude(pk=booking.pk).exists()
    if taken:
        raise FlowError("You already have a booking on that date.")
    now = timezone.now()
    booking.status = "accepted"
    booking.accepted_at = now
    booking.save(update_fields=["status", "accepted_at"])
    # Other customers who asked for the same date can no longer have it.
    Booking.objects.filter(photographer=booking.photographer, event_date=booking.event_date,
                           status="pending").exclude(pk=booking.pk).update(
        status="declined", cancelled_by="system", cancelled_at=now,
        cancel_reason="The photographer accepted another booking for this date.")


def decline(booking, reason=""):
    if booking.status != "pending":
        raise FlowError("This request is no longer waiting for your answer.")
    _close(booking, "declined", "photographer", reason.strip())


def mark_completed(booking):
    if booking.status != "confirmed":
        raise FlowError("Only a confirmed booking can be completed.")
    if not booking.can_complete:
        raise FlowError("You can mark a shoot as completed on or after its event date.")
    booking.status = "completed"
    booking.save(update_fields=["status"])


@transaction.atomic
def cancel_by_customer(booking, today=None):
    """Returns the refund amount (0 if nothing was paid or the policy gives nothing back)."""
    if booking.status not in ("pending", "accepted", "confirmed"):
        raise FlowError("This booking can no longer be cancelled.")
    paid = booking.paid_payment
    _close(booking, "cancelled", "customer", "Cancelled by the customer.")
    if not paid:
        return 0
    percent = policy.refund_percent(booking.event_date, today)
    days = policy.days_before(booking.event_date, today)
    return _start_refund(paid, percent, f"Customer cancelled {days} day(s) before the event: {percent}% refund")


@transaction.atomic
def cancel_by_photographer(booking, reason):
    if booking.status not in ("accepted", "confirmed"):
        raise FlowError("Only an accepted or confirmed booking can be cancelled. Decline pending requests instead.")
    if not (reason or "").strip():
        raise FlowError("Please write a reason. The customer will see it.")
    paid = booking.paid_payment
    _close(booking, "cancelled", "photographer", reason.strip())
    if paid:
        _start_refund(paid, 100, "Photographer cancelled: full refund")


@transaction.atomic
def admin_refund_in_full(booking, reason="Cancelled by admin after a complaint (no-show or dispute)."):
    """For no-shows and disputes after the admin has looked into it."""
    paid = booking.paid_payment
    if booking.status not in ("confirmed", "completed") or not paid or paid.refund_status != "none":
        raise FlowError("Needs a paid, confirmed or completed booking that has not been refunded.")
    _close(booking, "cancelled", "admin", reason)
    _start_refund(paid, 100, "Admin refund in full (no-show or dispute)")
