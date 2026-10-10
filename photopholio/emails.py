"""
Every email the site sends lives here: the wording, who gets it, and the safety net.

Emails are a courtesy, never a requirement. If the mail server is down or the address is missing,
the failure is logged and the booking / payment / approval still goes through.
"""
import logging
import re

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def _url(name, *args):
    return settings.SITE_URL + reverse(name, args=args)


def _name(user):
    return user.first_name or user.username


def _day(d):
    return d.strftime("%A, %d %B %Y")


def _money(n):
    return f"Rs.{n}"


def _send(to, subject, heading, paragraphs, details=None, button=None):
    """button = (label, url). Returns True if the mail server accepted the message."""
    if not to:
        return False
    html = render_to_string("photopholio/emails/message.html", {
        "heading": heading, "paragraphs": paragraphs, "details": details or [],
        "button": button, "site_url": settings.SITE_URL})
    text = re.sub(r"\n\s*\n+", "\n\n", strip_tags(html.replace("</p>", "</p>\n\n").replace("</h1>", "</h1>\n\n"))).strip()
    if button:
        text += f"\n\n{button[0]}: {button[1]}"
    try:
        send_mail(f"[PhotoPholio] {subject}", text, settings.DEFAULT_FROM_EMAIL, [to], html_message=html)
        return True
    except Exception:
        logger.exception("Could not send email '%s' to %s", subject, to)
        return False


def _booking_details(b):
    rows = [("Photographer", b.photographer.name)]
    if b.package_name:
        rows.append(("Package", b.package_name))
    return rows + [("Event date", _day(b.event_date)), ("Amount", _money(b.amount))]


def _photographer_email(b):
    p = b.photographer
    return p.user.email if p.user_id else ""


# ---------------------------------------------------------------- bookings

def booking_requested(b):
    _send(b.user.email, "Booking request sent",
          "Your request is on its way",
          [f"Hi {_name(b.user)}, we have sent your request to {b.photographer.name}.",
           "You pay only after they accept it. Nothing has been charged."],
          _booking_details(b), ("View my bookings", _url("my_bookings")))
    _send(_photographer_email(b), "New booking request",
          "You have a new booking request",
          [f"{_name(b.user)} would like to book you for {_day(b.event_date)}.",
           "Accept or decline it from your dashboard. If you accept, they have 24 hours to pay."],
          [("Date", _day(b.event_date)), ("Customer", _name(b.user)), ("Amount", _money(b.amount))]
          + ([("Notes", b.tool)] if b.tool else []),
          ("Open dashboard", _url("pro_dashboard")))


def booking_accepted(b):
    from .policy import PAYMENT_WINDOW_HOURS
    _send(b.user.email, f"{b.photographer.name} accepted your request",
          "Good news, your request was accepted",
          [f"Hi {_name(b.user)}, {b.photographer.name} can do {_day(b.event_date)}.",
           f"Please pay within {PAYMENT_WINDOW_HOURS} hours to confirm the date. After that the request lapses and nothing is charged."],
          _booking_details(b), ("Pay now", _url("payment", b.pk)))


def booking_declined(b):
    _send(b.user.email, "Your request was declined",
          "Your request could not be taken",
          [f"Hi {_name(b.user)}, {b.photographer.name} is not able to take {_day(b.event_date)}.",
           "You were not charged. There are other photographers who may be free that day."],
          None, ("Find another photographer", _url("photographer_list")))


def reschedule_requested(r):
    b = r.booking
    _send(_photographer_email(b), "Reschedule request", "A customer asked to change their date",
          [f"{_name(b.user)} would like to move their booking from {_day(r.old_date)} to {_day(r.new_date)}.",
           "Accept or decline it from your dashboard. Nothing changes until you accept."],
          [("Current date", _day(r.old_date)), ("Requested date", _day(r.new_date)), ("Customer", _name(b.user))]
          + ([("Reason", r.reason)] if r.reason else []), ("Open dashboard", _url("pro_dashboard")))
    _send(b.user.email, "Reschedule request sent", "Your request is on its way",
          [f"Hi {_name(b.user)}, we asked {b.photographer.name} to move your booking to {_day(r.new_date)}.",
           f"Your booking stays on {_day(r.old_date)} until they accept."],
          None, ("View my bookings", _url("my_bookings")))


def reschedule_accepted(r):
    b = r.booking
    _send(b.user.email, "Your new date is confirmed", "Your booking was moved",
          [f"Hi {_name(b.user)}, {b.photographer.name} accepted the change.",
           f"Your booking is now on {_day(r.new_date)}. Your payment and price stay the same."],
          _booking_details(b), ("View my bookings", _url("my_bookings")))


def reschedule_declined(r):
    b = r.booking
    _send(b.user.email, "Reschedule request declined", "The date could not be changed",
          [f"Hi {_name(b.user)}, {b.photographer.name} could not move your booking to {_day(r.new_date)}.",
           f"Reason: {r.decline_reason}" if r.decline_reason else "",
           f"Your booking is still on {_day(r.old_date)}. You can send a new request for another day, or cancel under the cancellation policy."],
          None, ("View my bookings", _url("my_bookings")))


def photos_delivered(b):
    _send(b.user.email, f"Your photos from {b.photographer.name} are ready", "Your photos are ready",
          [f"Hi {_name(b.user)}, {b.photographer.name} has shared the photos from your shoot.",
           b.delivery_note],
          _booking_details(b), ("See my photos", _url("my_bookings")))


def review_replied(fb):
    p = fb.booking.photographer
    _send(fb.user.email, f"{p.name} replied to your review", "You got a reply",
          [f"Hi {_name(fb.user)}, {p.name} answered your review:", f"\u201c{fb.reply}\u201d"],
          None, ("View the profile", _url("photographer_detail", p.slug)))


def payment_received(b, payment):
    _send(b.user.email, "Payment received, booking confirmed",
          "Your booking is confirmed",
          [f"Hi {_name(b.user)}, we have received your payment of {_money(payment.amount)}.",
           f"{b.photographer.name} is booked for {_day(b.event_date)}."],
          _booking_details(b) + [("Payment method", payment.get_payment_method_display())],
          ("View my bookings", _url("my_bookings")))
    _send(_photographer_email(b), "Booking confirmed and paid",
          "A booking was paid",
          [f"{_name(b.user)} has paid for {_day(b.event_date)}. The date is confirmed."],
          [("Date", _day(b.event_date)), ("Customer", _name(b.user)), ("Amount", _money(b.amount))],
          ("Open dashboard", _url("pro_dashboard")))


def _refund_line(payment):
    if payment is None:
        return "Nothing had been charged, so there is nothing to refund."
    if payment.refund_amount:
        return (f"A refund of {_money(payment.refund_amount)} is pending. "
                "You will get another email when it has been processed.")
    return "Under the cancellation policy this cancellation does not get a refund."


def booking_cancelled(b, by):
    """by = customer / photographer / admin / system."""
    payment = b.paid_payment
    details = _booking_details(b)
    if by == "customer":
        _send(b.user.email, "Booking cancelled", "Your booking was cancelled",
              [f"Hi {_name(b.user)}, you cancelled your booking with {b.photographer.name}.", _refund_line(payment)],
              details, ("Cancellation policy", _url("cancellation_policy")))
        _send(_photographer_email(b), "A booking was cancelled", "A customer cancelled",
              [f"{_name(b.user)} cancelled the booking for {_day(b.event_date)}. The date is free again."],
              [("Date", _day(b.event_date)), ("Customer", _name(b.user))], ("Open dashboard", _url("pro_dashboard")))
    elif by == "photographer":
        _send(b.user.email, "Your photographer cancelled", "We are sorry, your booking was cancelled",
              [f"Hi {_name(b.user)}, {b.photographer.name} had to cancel {_day(b.event_date)}.",
               f"Reason given: {b.cancel_reason}" if b.cancel_reason else "",
               _refund_line(payment)],
              details, ("Find another photographer", _url("photographer_list")))
    elif by == "admin":
        _send(b.user.email, "Booking cancelled and refunded", "Your booking was cancelled by our team",
              [f"Hi {_name(b.user)}, after looking into your complaint we cancelled your booking with {b.photographer.name}.",
               _refund_line(payment)], details, ("My bookings", _url("my_bookings")))
    else:  # system: payment window lapsed
        _send(b.user.email, "Your request lapsed", "Your request has lapsed",
              [f"Hi {_name(b.user)}, the payment for your booking with {b.photographer.name} was not made in time, "
               "so the request has been cancelled. Nothing was charged.", "You can send a new request any time."],
              details, ("Find a photographer", _url("photographer_list")))


def refund_processed(payment):
    b = payment.booking
    _send(b.user.email, "Your refund has been processed", "Your refund is done",
          [f"Hi {_name(b.user)}, we have processed your refund of {_money(payment.refund_amount)}."],
          [("Booking", f"{b.photographer.name}, {_day(b.event_date)}"), ("Refund", _money(payment.refund_amount))],
          ("My bookings", _url("my_bookings")))


def booking_completed(b):
    _send(b.user.email, "How was your shoot?", "Your shoot is complete",
          [f"Hi {_name(b.user)}, {b.photographer.name} has marked your shoot as completed.",
           "Your review helps other customers choose, and it only takes a minute."],
          None, ("Write a review", _url("feedback_create", b.pk)))


# ---------------------------------------------------------------- photographer profile review

def profile_approved(ph):
    if ph.user_id:
        _send(ph.user.email, "Your profile is live", "You are approved",
              [f"Hi {_name(ph.user)}, your PhotoPholio profile has been approved. Customers can now find and book you."],
              None, ("View my public page", _url("photographer_detail", ph.slug)))


def profile_rejected(ph):
    if ph.user_id:
        _send(ph.user.email, "Your profile needs changes", "Your profile was not approved yet",
              [f"Hi {_name(ph.user)}, we reviewed your profile and need a few changes first.",
               f"What to fix: {ph.rejection_reason}", "Update your profile, then submit it again."],
              None, ("Open dashboard", _url("pro_dashboard")))
