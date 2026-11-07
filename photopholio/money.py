"""
Who gets what: commission, holding the money, and paying the photographer. The single place for the money rules.

How PhotoPholio earns: it keeps a commission (default 10%) of the photographer's SERVICE price.
The customer pays no extra fee, and travel and stay money is passed to the photographer in full.

  customer pays  = service price + travel          (e.g. 22,000 for 4 days, or 28,000 with 6,000 travel)
  commission     = percent of the service price    (2,200)
  photographer   = customer paid - commission      (19,800, or 25,800 with the travel)

Life of the money on one booking (Booking.payout_status):
  ""        not paid yet
  held      the customer paid; the shoot has not happened yet. PhotoPholio holds the money.
  ready     the shoot is completed AND confirmed by the customer (or 48 hours passed, or the admin decided), or a
            cancellation left the photographer with a share. The admin can pay now. A reported problem keeps it at "held".
  paid      the admin sent the money and noted the transfer reference.
  void      the customer was refunded in full: nothing to pay.
  recover   the photographer was already paid, then the booking was refunded: the admin must get money back.

If a cancellation refunds only part (e.g. 50%), the commission is worked out on what is kept, so PhotoPholio
and the photographer share the loss in the same proportion.

Honest limit: this is a LEDGER. The checkout is a demo, no real money moves, and the admin pays photographers by hand
(UPI / bank transfer) and records the reference here. A real launch would use a payment gateway with split payouts
(for example Razorpay Route) and a registered business; the numbers worked out here would stay the same.
"""
from collections import namedtuple

from django.conf import settings
from django.utils import timezone

Split = namedtuple("Split", "percent fee payout")


def default_percent():
    return int(getattr(settings, "PLATFORM_COMMISSION_PERCENT", 10))


def percent_for(photographer):
    own = photographer.commission_percent
    return default_percent() if own is None else own


def _split(retained, service, paid_total, percent):
    """Commission is a percent of the service part of the money that is kept."""
    if retained <= 0 or paid_total <= 0:
        return Split(percent, 0, 0)
    service_share = retained * min(service, paid_total) // paid_total
    fee = service_share * percent // 100
    return Split(percent, fee, retained - fee)


def preview(booking):
    """What the split would be if the booking is paid in full today. Shown to the photographer before accepting."""
    pct = percent_for(booking.photographer)
    return _split(booking.amount, booking.service_price, booking.amount, pct)


def on_paid(booking):
    """The customer paid. Works out the split and starts holding the money."""
    payment = booking.paid_payment
    if payment is None:
        return
    pct = percent_for(booking.photographer)
    s = _split(payment.amount - payment.refund_amount, booking.service_price, payment.amount, pct)
    booking.commission_percent, booking.platform_fee, booking.payout_amount = s.percent, s.fee, s.payout
    booking.payout_status = "held"
    booking.save(update_fields=["commission_percent", "platform_fee", "payout_amount", "payout_status"])


def on_completed(booking):
    """The photographer (or the admin) marked the shoot completed. The money becomes payable once it is confirmed."""
    if not booking.payout_status:
        on_paid(booking)  # a booking paid before this feature existed
    release_if_due(booking)


def release_if_due(booking):
    """
    held -> ready, when the shoot is completed AND confirmed (by the customer, after 48 hours of silence, or by the admin)
    AND no reported problem is open. A booking completed before confirmations existed (no completed_at) is released at once.
    Returns True if it was released now.
    """
    if booking.payout_status != "held" or booking.status != "completed" or booking.dispute_status == "open":
        return False
    if booking.completed_at is not None and not booking.customer_confirmed_at:
        return False
    booking.payout_status = "ready"
    booking.save(update_fields=["payout_status"])
    return True


def on_cancelled(booking):
    """A paid booking was cancelled and its refund has been worked out. Settles what is left."""
    payment = booking.paid_payment
    if payment is None:
        return
    pct = booking.commission_percent if booking.commission_percent is not None else percent_for(booking.photographer)
    kept = payment.amount - payment.refund_amount
    s = _split(kept, booking.service_price, payment.amount, pct)
    was_paid_out, paid_before = booking.payout_status == "paid", booking.payout_amount
    booking.commission_percent, booking.platform_fee, booking.payout_amount = s.percent, s.fee, s.payout
    if was_paid_out:
        booking.payout_status = "recover"
        booking.payout_note = f"Already paid Rs.{paid_before}; after the refund it should be Rs.{s.payout}. Recover Rs.{paid_before - s.payout}."
    else:
        booking.payout_status = "ready" if s.payout > 0 else "void"
        booking.payout_note = "Part of the money is kept after a cancellation." if s.payout > 0 else "Refunded in full."
    booking.save(update_fields=["commission_percent", "platform_fee", "payout_amount", "payout_status", "payout_note"])


def mark_paid(booking, reference, now=None):
    """The admin sent the money to the photographer and writes down the transfer reference."""
    from .booking_flow import FlowError
    from . import emails
    reference = " ".join((reference or "").split())
    if booking.dispute_status == "open":
        raise FlowError("A problem was reported on this booking. Decide it on the Disputes page first.")
    if booking.payout_status != "ready":
        raise FlowError("Only a payout that is ready to pay can be marked as paid.")
    if not 3 <= len(reference) <= 60:
        raise FlowError("Write the transfer reference (UTR or transaction id), 3 to 60 characters.")
    booking.payout_status = "paid"
    booking.payout_reference = reference
    booking.payout_paid_at = now or timezone.now()
    booking.save(update_fields=["payout_status", "payout_reference", "payout_paid_at"])
    order = getattr(booking, "album_order", None)          # the album design fee goes out in the same transfer, with the same reference
    if order is not None:
        album_mark_paid(order, reference)
    emails.payout_sent(booking)


def mark_recovered(booking, note=""):
    """The admin got the money back from the photographer. Closes a 'recover' entry."""
    from .booking_flow import FlowError
    if booking.payout_status != "recover":
        raise FlowError("Nothing to recover on this booking.")
    booking.payout_status = "void"
    booking.payout_note = (f"Recovered on {timezone.localdate():%d %b %Y}. " + (note or "")).strip()[:200]
    booking.save(update_fields=["payout_status", "payout_note"])


def totals(bookings):
    """Sums per payout status for a queryset of bookings: {'held': (count, money), ...}."""
    from django.db.models import Count, Sum
    rows = bookings.exclude(payout_status="").values("payout_status").annotate(n=Count("id"), money=Sum("payout_amount"), fee=Sum("platform_fee"))
    return {r["payout_status"]: {"n": r["n"], "money": r["money"] or 0, "fee": r["fee"] or 0} for r in rows}


# --- Albums: a separate ledger ------------------------------------------------
def album_design_percent():
    """Photographer's share of the album margin (price minus print cost). The rest is PhotoPholio's album commission."""
    return int(getattr(settings, "ALBUM_DESIGN_SHARE_PERCENT", 60))


def album_split(price, print_cost):
    margin = max(0, price - print_cost)
    pct = album_design_percent()
    design = margin * pct // 100
    return pct, design, margin - design


def album_on_paid(booking):
    """The customer paid for the booking: the album is paid in the same checkout. Held until delivery is confirmed."""
    order = getattr(booking, "album_order", None)
    if order is None or order.status != "placed":
        return
    order.status, order.paid_at, order.payout_status = "paid", timezone.now(), "held"
    order.save(update_fields=["status", "paid_at", "payout_status"])


def album_on_cancelled(booking):
    """A booking was cancelled before the album was made: the album is cancelled and refunded in full."""
    order = getattr(booking, "album_order", None)
    if order is None or order.status == "cancelled":
        return
    was_paid = order.status == "paid"
    order.status, order.payout_status = "cancelled", "void" if was_paid else ""
    order.refund_amount = order.price if was_paid else 0
    order.save(update_fields=["status", "payout_status", "refund_amount"])


def album_release_if_due(order):
    """The photographer's design fee is released once the customer approves the design. held -> ready."""
    if order.status == "paid" and order.design_status == "approved" and order.payout_status == "held":
        order.payout_status, order.payout_ready_at = "ready", timezone.now()
        order.save(update_fields=["payout_status", "payout_ready_at"])
        return True
    return False


def album_mark_paid(order, reference):
    """Admin paid the design fee out. Only after it was released, and only once."""
    if order.payout_status != "ready":
        return False
    order.payout_status, order.payout_paid_at, order.payout_reference = "paid", timezone.now(), (reference or "")[:80]
    order.save(update_fields=["payout_status", "payout_paid_at", "payout_reference"])
    return True


def album_fee_totals(photographer):
    """Album design fees for a photographer, by stage."""
    from .models import AlbumOrder
    out = {"held": 0, "ready": 0, "paid": 0}
    for o in AlbumOrder.objects.filter(booking__photographer=photographer, status="paid", payout_status__in=list(out)):
        out[o.payout_status] += o.design_fee
    return out
