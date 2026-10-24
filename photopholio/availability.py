"""
Who is free on which day. One place for the rule, used by the calendar, the search, the booking form and "accept".

A photographer is NOT free on a day when
  - the day has already passed, or
  - they blocked it themselves (BlockedDate), or
  - they have an accepted or paid booking on it.
Pending requests do not block a day: several customers may ask for the same date and the photographer picks one.
"""
import calendar
from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone

from .models import BlockedDate, Booking, Photographer

BLOCKING_STATUSES = ("accepted", "confirmed")
MAX_BOOKING_DAYS = 15  # the longest single booking


def overlapping(start, end):
    """
    Q for bookings that touch any day from `start` to `end` (inclusive). A booking covers event_date..end_date;
    a row without end_date counts as a one-day booking.
    """
    return Q(event_date__lte=end) & (Q(end_date__gte=start) | Q(end_date__isnull=True, event_date__gte=start))


def clashes(photographer, start, end, statuses=BLOCKING_STATUSES, exclude=None):
    """Bookings of this photographer that overlap start..end with one of `statuses`."""
    qs = Booking.objects.filter(overlapping(start, end), photographer=photographer, status__in=statuses)
    return qs.exclude(pk=exclude.pk) if exclude is not None else qs


def blocked_days(photographer, start, end):
    return list(BlockedDate.objects.filter(photographer=photographer, date__range=(start, end))
                .order_by("date").values_list("date", flat=True))


def is_free(photographer, day, today=None):
    return is_range_free(photographer, day, day, today)


def is_range_free(photographer, start, end, today=None, exclude=None):
    """True when every day from start to end is free. `exclude` is a booking being moved (it must not clash with itself)."""
    today = today or timezone.localdate()
    if start < today or end < start:
        return False
    if blocked_days(photographer, start, end):
        return False
    return not clashes(photographer, start, end, exclude=exclude).exists()


def exclude_unavailable(queryset, day):
    """Keeps only the photographers in `queryset` who are free on `day`."""
    busy = Booking.objects.filter(overlapping(day, day), status__in=BLOCKING_STATUSES).values("photographer_id")
    blocked = BlockedDate.objects.filter(date=day).values("photographer_id")
    return queryset.exclude(pk__in=busy).exclude(pk__in=blocked)


def free_on(day):
    """Approved photographers who are taking bookings and are free on `day`."""
    return exclude_unavailable(Photographer.objects.approved().filter(is_available=True), day)


def _add_months(year, month, n):
    index = year * 12 + (month - 1) + n
    return index // 12, index % 12 + 1


def months(photographer, count=3, today=None, private=False):
    """
    Calendar data for the next `count` months, Monday first.
    Every day cell: {date, day, in_month, state, note} where state is free, booked, blocked or past.
    private=False is what customers get: blocked days show as "booked" and there are no notes,
    so nobody learns why a photographer is unavailable. private=True is for the photographer's own page.
    """
    today = today or timezone.localdate()
    first_year, first_month = today.year, today.month
    last_year, last_month = _add_months(first_year, first_month, count - 1)
    start = date(first_year, first_month, 1)
    end = date(last_year, last_month, calendar.monthrange(last_year, last_month)[1])

    booked = {}
    for b in (Booking.objects.filter(overlapping(start, end), photographer=photographer, status__in=BLOCKING_STATUSES)
              .select_related("user")):
        for d in b.day_list():  # a 4-day booking marks all 4 days
            booked[d] = b.user.first_name or b.user.username
    blocked = dict(BlockedDate.objects.filter(photographer=photographer, date__range=(start, end)).values_list("date", "reason"))

    cal = calendar.Calendar(firstweekday=0)
    out = []
    for i in range(count):
        year, month = _add_months(first_year, first_month, i)
        weeks = []
        for week in cal.monthdatescalendar(year, month):
            cells = []
            for d in week:
                if d.month != month:
                    cells.append({"in_month": False})
                    continue
                note = ""
                if d < today:
                    state = "past"
                elif d in booked:
                    state, note = "booked", f"Booked: {booked[d]}"
                elif d in blocked:
                    state, note = "blocked", f"Blocked: {blocked[d]}" if blocked[d] else "Blocked by you"
                else:
                    state = "free"
                if not private:
                    state = "booked" if state == "blocked" else state
                    note = ""
                cells.append({"in_month": True, "date": d, "day": d.day, "state": state, "note": note})
            weeks.append(cells)
        out.append({"label": date(year, month, 1).strftime("%B %Y"), "weeks": weeks})
    return out


def block_days(photographer, start, end, reason=""):
    """Blocks every day from start to end (inclusive). Returns how many were newly blocked."""
    created = 0
    day = start
    while day <= end:
        _, was_new = BlockedDate.objects.get_or_create(photographer=photographer, date=day, defaults={"reason": reason})
        created += was_new
        day += timedelta(days=1)
    return created
