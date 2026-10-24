"""
Travel charges: the single place where "who pays for the trip" is decided. India only for now.

The rule, like on most real booking sites:  the CUSTOMER pays travel, the PHOTOGRAPHER sets the price.
PhotoPholio never pays travel from its own pocket.

Each photographer has a travel policy (see Photographer.travel_*):
  - an event in their own city is always free of travel charge ("local")
  - an event in another city of their own state costs travel_fee_state once, plus stay_fee_per_day for every day away
  - an event in another state costs travel_fee_india once, plus stay_fee_per_day for every day away
  - travel_scope says how far they are willing to go at all: only their city, their state, or all of India

The customer sees the breakdown BEFORE sending the request, and the amount is copied onto the booking
when it is made (Booking.travel_fee), so changing a policy later never changes a booking that already exists.
Commission (phase 5C) is taken on the service price only, never on travel.
"""
from dataclasses import dataclass, field

LOCAL, STATE, INDIA = "local", "state", "india"

ZONE_LABELS = {
    LOCAL: "In the photographer's city",
    STATE: "Another city in the same state",
    INDIA: "Another state",
}


def _norm(text):
    return " ".join((text or "").lower().split())


@dataclass
class Quote:
    zone: str = ""
    allowed: bool = True
    reason: str = ""
    trip_fee: int = 0
    stay_fee: int = 0
    days: int = 1
    lines: list = field(default_factory=list)  # [(label, amount)] for display; travel part only

    @property
    def total(self):
        return self.trip_fee + self.stay_fee


def zone_for(photographer, event_state, event_city):
    """local / state / india, from where the event is compared with the photographer's home city."""
    home = photographer.city
    if event_state is None or event_state.pk != home.state_id:
        return INDIA
    return LOCAL if _norm(event_city) == _norm(home.name) else STATE


def quote(photographer, event_state, event_city, days=1):
    """What travel will cost for an event in `event_city`, `event_state` lasting `days` days."""
    days = max(1, int(days or 1))
    if event_state is None or not _norm(event_city):
        return Quote(days=days)  # place not chosen yet: nothing to quote
    zone = zone_for(photographer, event_state, event_city)
    first = photographer.name.split()[0] if photographer.name else "The photographer"
    home = photographer.city.name
    scope = photographer.travel_scope
    if (scope == "city" and zone != LOCAL) or (scope == "state" and zone == INDIA):
        where = f"in {home}" if scope == "city" else f"within {photographer.city.state.name}"
        return Quote(zone=zone, allowed=False, days=days,
                     reason=f"{first} only takes events {where}. Pick another photographer or another city.")
    if zone == LOCAL:
        return Quote(zone=zone, days=days)
    trip = photographer.travel_fee_state if zone == STATE else photographer.travel_fee_india
    stay = photographer.stay_fee_per_day * days
    lines = []
    if trip:
        lines.append(("Travel", trip))
    if stay:
        lines.append((f"Stay and food ({days} day{'s' if days != 1 else ''})", stay))
    return Quote(zone=zone, trip_fee=trip, stay_fee=stay, days=days, lines=lines)


def summary(photographer, package, days, event_state, event_city):
    """
    Everything the customer pays, as display lines. Used by the live price box and by the booking itself,
    so the number shown is always the number charged.
    Returns dict(lines=[(label, amount)], total, allowed, reason, zone).
    """
    rate = package.price if package else photographer.price_per_day
    name = package.get_tier_display() if package else "Full day"
    service = rate * days
    q = quote(photographer, event_state, event_city, days)
    label = f"{name}: {rate:,} x {days} days" if days > 1 else f"{name}"
    lines = [(label, service)] + q.lines
    return {"lines": lines, "service": service, "travel": q.total, "total": service + q.total,
            "allowed": q.allowed, "reason": q.reason, "zone": q.zone, "rate": rate}


def policy_lines(photographer):
    """Short sentences for the public profile, e.g. 'Free in Vadodara', 'Rs.3,000 for other cities in Gujarat'."""
    home, state = photographer.city.name, photographer.city.state.name
    out = [f"No travel charge in {home}."]
    scope = photographer.travel_scope
    stay = f" plus Rs.{photographer.stay_fee_per_day:,} per day for stay and food" if photographer.stay_fee_per_day else ""
    if scope == "city":
        out.append(f"Takes events in {home} only.")
        return out
    fee = photographer.travel_fee_state
    out.append(("Other cities in " + state + ": " + (f"Rs.{fee:,} travel" if fee else "no travel charge") + stay + "."))
    if scope == "state":
        out.append(f"Takes events within {state} only.")
        return out
    fee = photographer.travel_fee_india
    out.append("Other states in India: " + (f"Rs.{fee:,} travel" if fee else "no travel charge") + stay + ".")
    return out
