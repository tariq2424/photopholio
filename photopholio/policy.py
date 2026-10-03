"""
Cancellation policy: the single place where the rules live.
The policy page, the cancel screen and the refund calculation all read from here,
so changing a number here changes it everywhere.
"""
from django.utils import timezone

# After a photographer accepts, the customer has this long to pay before the request lapses.
PAYMENT_WINDOW_HOURS = 24

# (minimum whole days between cancelling and the event date, refund percent), highest first.
# The booking only stores a date (no time), so rules are counted in days.
REFUND_TIERS = [(2, 100), (1, 50), (0, 0)]


def days_before(event_date, today=None):
    return (event_date - (today or timezone.localdate())).days


def refund_percent(event_date, today=None):
    days = days_before(event_date, today)
    for min_days, percent in REFUND_TIERS:
        if days >= min_days:
            return percent
    return 0  # event date already passed


def refund_amount(paid_amount, event_date, today=None):
    return paid_amount * refund_percent(event_date, today) // 100


def rows():
    """Human readable (when, refund percent) rows for the policy page."""
    out = []
    for i, (min_days, percent) in enumerate(REFUND_TIERS):
        if i == 0:
            label = f"{min_days} or more days before the event"
        elif min_days == 0:
            label = "On the day of the event or later"
        else:
            upper = REFUND_TIERS[i - 1][0] - 1
            plural = "" if min_days == 1 else "s"
            label = (f"{min_days} day{plural} before the event" if upper == min_days
                     else f"{min_days} to {upper} days before the event")
        out.append((label, percent))
    return out
