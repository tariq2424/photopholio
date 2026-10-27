"""
Shoot tracking: what happens on the day(s) of a booking, and when the photographer's money may be released.

This is a demo-friendly "button" system. There is no GPS: the photographer taps Arrived, Start and Finish, and the
customer sees it. A tap is the photographer's own report, so it is evidence, never proof, and nothing is decided from it alone.

Per day of the event (a 2-day booking has 2 rows):   upcoming -> arrived -> in progress -> finished
  * the photographer can tap each step only ON that day, and only in that order
  * tapping the same step twice changes nothing (the second tap is refused)
  * finishing the LAST day completes the booking when every day was finished

After the photographer completes the booking:
  * the customer is asked to confirm. Confirming makes the photographer's money payable (money.py).
  * silence for CONFIRM_WINDOW_HOURS (48) counts as confirmation, so money never gets stuck on a forgetful customer
  * a customer can instead report a problem, which freezes the money until the admin decides

A report never decides anything by itself: nobody is called guilty and nobody is refunded until the admin reviews it.
The admin chooses to pay the photographer, refund the customer in full, or dismiss the report, and writes why.
Every step is written to ShootEvent (who, what, when), which is only ever added to.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import emails, money, policy
from .booking_flow import FlowError
from .models import Booking, ShootDay, ShootEvent

STEPS = ("arrived", "start", "finish")
MIN_REPORT_CHARS = 10
MAX_TEXT_CHARS = 1000


def log(booking, role, action, actor=None, day=None, note="", now=None):
    return ShootEvent.objects.create(booking=booking, actor=actor, actor_role=role, action=action, day=day,
                                     note=(note or "")[:500], created_at=now or timezone.now())


def days_for(booking):
    """The ShootDay row of every event day (made when first needed). Safe to call again and again."""
    existing = {d.date: d for d in booking.shoot_days.all()}
    out = []
    for day in booking.day_list():
        row = existing.get(day)
        if row is None:
            row, _ = ShootDay.objects.get_or_create(booking=booking, date=day)
        out.append(row)
    return out


def next_step(day_row):
    """Which button the photographer sees for this day, or '' when the day is done."""
    return {"upcoming": "arrived", "arrived": "start", "in_progress": "finish"}.get(day_row.state, "")


def todays_day(booking, today=None):
    today = today or timezone.localdate()
    if booking.status != "confirmed" or today not in booking.day_list():
        return None
    return next((d for d in days_for(booking) if d.date == today), None)


# ---------------------------------------------------------------- the photographer

def photographer_step(booking_id, user, step, today=None, now=None):
    """The photographer taps Arrived / Start / Finish for today. Returns the updated ShootDay."""
    from . import booking_flow
    today, now = today or timezone.localdate(), now or timezone.now()
    if step not in STEPS:
        raise FlowError("Unknown action.")
    with transaction.atomic():
        booking = (Booking.objects.select_for_update(of=("self",)).select_related("photographer__user", "user").get(pk=booking_id))
        if booking.photographer.user_id != user.pk:
            raise FlowError("This is not your booking.")
        if booking.status != "confirmed":
            raise FlowError("Only a paid, confirmed booking can be tracked.")
        if today not in booking.day_list():
            raise FlowError("You can update the shoot only on the day of the event.")
        row = next(d for d in days_for(booking) if d.date == today)
        row = ShootDay.objects.select_for_update().get(pk=row.pk)
        if step == "arrived":
            if row.arrived_at:
                raise FlowError("You already marked yourself as arrived today.")
            row.arrived_at = now
        elif step == "start":
            if not row.arrived_at:
                raise FlowError("Mark yourself as arrived first.")
            if row.started_at:
                raise FlowError("The shoot is already started.")
            row.started_at = now
        else:
            if not row.started_at:
                raise FlowError("Start the shoot first.")
            if row.finished_at:
                raise FlowError("Today's shoot is already finished.")
            row.finished_at = now
        row.save()
        log(booking, "photographer", {"arrived": "arrived", "start": "started", "finish": "finished"}[step],
            actor=user, day=today, now=now)
        everything_done = step == "finish" and all(d.finished_at for d in days_for(booking))
    if step == "arrived":
        emails.photographer_arrived(booking, row)
    if everything_done:
        booking.refresh_from_db()
        booking_flow.mark_completed(booking, actor=user)
    return row


# ---------------------------------------------------------------- the customer

def confirm_arrival(booking_id, user, day, now=None):
    """Optional. The customer says "yes, they are here". It is evidence for the admin and gates nothing."""
    now = now or timezone.now()
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking_id)
        if booking.user_id != user.pk:
            raise FlowError("This is not your booking.")
        row = ShootDay.objects.select_for_update().filter(booking=booking, date=day).first()
        if row is None or not row.arrived_at:
            raise FlowError("The photographer has not marked an arrival for that day.")
        if row.customer_seen_at:
            raise FlowError("You already confirmed this.")
        row.customer_seen_at = now
        row.save(update_fields=["customer_seen_at"])
        log(booking, "customer", "saw_arrival", actor=user, day=day, now=now)


def confirm_completed(booking_id, user, now=None):
    """The customer confirms the shoot took place. The photographer's money becomes payable."""
    now = now or timezone.now()
    with transaction.atomic():
        booking = Booking.objects.select_for_update(of=("self",)).select_related("photographer__user", "user").get(pk=booking_id)
        if booking.user_id != user.pk:
            raise FlowError("This is not your booking.")
        if booking.status != "completed" or not booking.completed_at:
            raise FlowError("The photographer has not marked this shoot as completed yet.")
        if booking.customer_confirmed_at:
            raise FlowError("You already confirmed this shoot.")
        if booking.has_open_dispute:
            raise FlowError("You have reported a problem with this booking. The admin will review it first.")
        booking.customer_confirmed_at, booking.customer_confirmed_by_customer = now, True
        booking.save(update_fields=["customer_confirmed_at", "customer_confirmed_by_customer"])
        log(booking, "customer", "confirmed_completed", actor=user, now=now)
        money.release_if_due(booking)
    emails.customer_confirmed(booking)


def report_problem(booking_id, user, kind, text, today=None, now=None):
    """The customer reports that something went wrong. Freezes the money; the admin decides."""
    today, now = today or timezone.localdate(), now or timezone.now()
    text = " ".join((text or "").split())
    if kind not in dict(Booking.DISPUTE_KIND_CHOICES):
        raise FlowError("Choose what went wrong.")
    if len(text) < MIN_REPORT_CHARS:
        raise FlowError(f"Please describe the problem in at least {MIN_REPORT_CHARS} characters. The admin will read it.")
    if len(text) > MAX_TEXT_CHARS:
        raise FlowError(f"Please keep it under {MAX_TEXT_CHARS} characters.")
    with transaction.atomic():
        booking = Booking.objects.select_for_update(of=("self",)).select_related("photographer__user", "user").get(pk=booking_id)
        if booking.user_id != user.pk:
            raise FlowError("This is not your booking.")
        if booking.dispute_status:
            raise FlowError("You have already reported a problem on this booking.")
        if not booking.can_report(today):
            raise FlowError("A problem can be reported from the first day of the event, until the photographer has been paid.")
        booking.dispute_status, booking.dispute_kind, booking.dispute_text = "open", kind, text
        booking.dispute_opened_at = now
        fields = ["dispute_status", "dispute_kind", "dispute_text", "dispute_opened_at"]
        if booking.payout_status == "ready":  # not paid out yet: take it back to held
            booking.payout_status, booking.payout_note = "held", "On hold: the customer reported a problem."
            fields += ["payout_status", "payout_note"]
        booking.save(update_fields=fields)
        log(booking, "customer", "reported_problem", actor=user, note=f"{booking.get_dispute_kind_display()}: {text}", now=now)
    emails.dispute_opened(booking)
    return booking


def auto_confirm_due(now=None):
    """Silence for CONFIRM_WINDOW_HOURS counts as confirmation. Returns how many bookings were released."""
    now = now or timezone.now()
    cutoff = now - timedelta(hours=policy.CONFIRM_WINDOW_HOURS)
    due = list(Booking.objects.filter(status="completed", completed_at__isnull=False, completed_at__lte=cutoff,
                                      customer_confirmed_at__isnull=True).exclude(dispute_status="open")
               .select_related("photographer__user", "user"))
    done = 0
    for booking in due:
        with transaction.atomic():
            fresh = Booking.objects.select_for_update().get(pk=booking.pk)
            if fresh.customer_confirmed_at or fresh.dispute_status == "open" or fresh.status != "completed":
                continue
            fresh.customer_confirmed_at, fresh.customer_confirmed_by_customer = now, False
            fresh.save(update_fields=["customer_confirmed_at", "customer_confirmed_by_customer"])
            log(fresh, "system", "auto_confirmed", note=f"No reply within {policy.CONFIRM_WINDOW_HOURS} hours.", now=now)
            money.release_if_due(fresh)
            done += 1
        emails.customer_confirmed(fresh, automatic=True)
    return done


# ---------------------------------------------------------------- the admin

def resolve_dispute(booking_id, admin_user, outcome, note, now=None):
    """The admin decides a reported problem. outcome: pay (release the money), refund (customer, in full), dismiss."""
    from . import booking_flow
    now = now or timezone.now()
    note = " ".join((note or "").split())
    if outcome not in ("pay", "refund", "dismiss"):
        raise FlowError("Choose a decision.")
    if len(note) < 5:
        raise FlowError("Write a short note explaining the decision. Both people will see it.")
    with transaction.atomic():
        booking = Booking.objects.select_for_update(of=("self",)).select_related("photographer__user", "user").get(pk=booking_id)
        if not booking.has_open_dispute:
            raise FlowError("There is no open report on this booking.")
        if outcome == "refund":
            booking_flow.admin_refund_in_full(booking, reason=f"Admin decision after a reported problem: {note}")
            booking.refresh_from_db()
        else:
            if booking.status == "confirmed":  # the shoot happened but was never marked completed
                booking.status, booking.completed_at = "completed", booking.completed_at or now
                booking.save(update_fields=["status", "completed_at"])
            if booking.status == "completed" and not booking.customer_confirmed_at:
                booking.customer_confirmed_at, booking.customer_confirmed_by_customer = now, False
                booking.save(update_fields=["customer_confirmed_at", "customer_confirmed_by_customer"])
            if not booking.payout_status and booking.paid_payment:
                money.on_paid(booking)
        booking.dispute_status, booking.dispute_outcome, booking.dispute_note = "resolved", outcome, note
        booking.dispute_resolved_at, booking.dispute_resolved_by = now, admin_user
        booking.save(update_fields=["dispute_status", "dispute_outcome", "dispute_note", "dispute_resolved_at", "dispute_resolved_by"])
        if outcome != "refund":
            money.release_if_due(booking)
        log(booking, "admin", f"decided_{outcome}", actor=admin_user, note=note, now=now)
    emails.dispute_resolved(booking)
    return booking
