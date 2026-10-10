"""
Numbers for the admin analytics page. Kept separate from the page so they can be tested on their own.

Definitions (shown at the bottom of the page too):
  gross      = money from completed payments
  refunds    = refund amounts, pending or processed
  net        = gross - refunds
  acceptance = of the requests a photographer answered, the share they accepted
"""
from django.contrib.auth.models import User
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .models import Booking, Payment, Photographer

MONTHS = 6


def _month_keys(today, n):
    year, month = today.year, today.month
    keys = []
    for _ in range(n):
        keys.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(keys))


def _series(rows, keys, field):
    found = {(r["m"].year, r["m"].month): r[field] or 0 for r in rows}
    return [found.get(k, 0) for k in keys]


def _pct(part, whole):
    return round(part * 100 / whole) if whole else None


def dashboard_data(today=None, months=MONTHS):
    today = today or timezone.localdate()
    keys = _month_keys(today, months)
    labels = [timezone.datetime(y, m, 1).strftime("%b %Y") for y, m in keys]

    since = timezone.make_aware(timezone.datetime(keys[0][0], keys[0][1], 1))
    requests = Booking.objects.filter(created_at__gte=since).annotate(m=TruncMonth("created_at")) \
        .values("m").annotate(n=Count("id"))
    money = Payment.objects.filter(status="completed", payment_date__gte=since).annotate(m=TruncMonth("payment_date")) \
        .values("m").annotate(gross=Sum("amount"), refunds=Sum("refund_amount"))

    gross_by_month = _series(money, keys, "gross")
    refunds_by_month = _series(money, keys, "refunds")

    paid = Payment.objects.filter(status="completed")
    totals = paid.aggregate(gross=Sum("amount"), refunds=Sum("refund_amount"), n=Count("id"))
    gross, refunds, paid_count = totals["gross"] or 0, totals["refunds"] or 0, totals["n"]

    accepted = Booking.objects.filter(accepted_at__isnull=False).count()
    declined_by_pros = Booking.objects.filter(status="declined", cancelled_by="photographer").count()
    paid_after_accept = Booking.objects.filter(accepted_at__isnull=False, payments__status="completed").distinct().count()
    total_bookings = Booking.objects.count()
    cancelled = Booking.objects.filter(status="cancelled").count()

    pending_refunds = list(Payment.objects.filter(refund_status="pending")
                           .select_related("user", "booking__photographer").order_by("payment_date"))
    pending_approvals = list(Photographer.objects.filter(status="pending").select_related("city"))
    status_counts = {r["status"]: r["n"] for r in Booking.objects.values("status").annotate(n=Count("id"))}
    status_labels = dict(Booking.STATUS_CHOICES)
    top = list(paid.values("booking__photographer__name").annotate(total=Sum("amount"), n=Count("id")).order_by("-total")[:5])
    cancellers = list(Photographer.objects.annotate(c=Count("bookings", filter=Q(bookings__cancelled_by="photographer")))
                      .filter(c__gt=0).order_by("-c", "name")[:5])

    return {
        "kpis": {
            "total_bookings": total_bookings,
            "requests_this_month": sum(1 for _ in Booking.objects.filter(created_at__year=today.year, created_at__month=today.month)),
            "gross": gross, "refunds": refunds, "net": gross - refunds,
            "avg_value": round(gross / paid_count) if paid_count else 0,
            "live_photographers": Photographer.objects.approved().count(),
            "customers": User.objects.filter(is_staff=False).exclude(profile__role="photographer").count(),
            "pending_approvals": len(pending_approvals),
            "pending_refunds": len(pending_refunds),
            "pending_refund_amount": sum(p.refund_amount for p in pending_refunds),
            "acceptance_rate": _pct(accepted, accepted + declined_by_pros),
            "payment_rate": _pct(paid_after_accept, accepted),
            "cancellation_rate": _pct(cancelled, total_bookings),
        },
        "pending_refunds": pending_refunds,
        "pending_approvals": pending_approvals,
        "top_photographers": top,
        "cancellers": cancellers,
        "chart": {
            "months": labels,
            "requests": _series(requests, keys, "n"),
            "gross": gross_by_month,
            "refunds": refunds_by_month,
            "status_labels": [status_labels[s] for s in status_labels if status_counts.get(s)],
            "status_values": [status_counts[s] for s in status_labels if status_counts.get(s)],
            "top_names": [t["booking__photographer__name"] for t in top],
            "top_values": [t["total"] for t in top],
        },
    }
