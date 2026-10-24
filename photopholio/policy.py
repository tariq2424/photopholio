"""
Cancellation policy: the single place where the rules live.
The policy page, the cancel screen and the refund calculation all read from here,
so changing a number here changes it everywhere.
"""
from django.utils import timezone

# After a photographer accepts, the customer has this long to pay before the request lapses.
PAYMENT_WINDOW_HOURS = 24

# After the photographer marks a shoot completed, the customer has this long to confirm it or report a problem.
# Silence counts as confirmation, so a photographer's money is never stuck because a customer forgot.
CONFIRM_WINDOW_HOURS = 48

# (minimum whole days between cancelling and the event date, refund percent), highest first.
# The booking only stores a date (no time), so rules are counted in days.
REFUND_TIERS = [(2, 100), (1, 50), (0, 0)]

# Rescheduling: a customer may ask to move a booking to another day, and the photographer must agree.
# The current date must be at least this many days away (same cut-off as a full refund), and each
# booking can be moved only once, so a date cannot be shuffled around again and again.
RESCHEDULE_MIN_DAYS = REFUND_TIERS[0][0]
MAX_RESCHEDULES = 1


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
