"""Answering reviews and hiding the ones that break the rules."""
from django.utils import timezone

from . import emails
from .booking_flow import FlowError
from .models import Feedback

MAX_REPLY_CHARS = 600


def reply(feedback, text):
    """The photographer answers a review. Answering again replaces the earlier answer."""
    text = (text or "").strip()
    if feedback.is_hidden:
        raise FlowError("This review is not visible, so it cannot be answered.")
    if not text:
        raise FlowError("Write your reply first.")
    if len(text) > MAX_REPLY_CHARS:
        raise FlowError(f"Please keep the reply under {MAX_REPLY_CHARS} characters.")
    first_time = not feedback.reply
    feedback.reply = text
    feedback.replied_at = timezone.now()
    feedback.save(update_fields=["reply", "replied_at"])
    if first_time:
        emails.review_replied(feedback)


def set_hidden(queryset, hidden):
    """Admin: hide or show reviews. The photographers' ratings are recalculated. Returns how many changed."""
    changed = 0
    photographers = {}
    for fb in queryset.select_related("booking__photographer"):
        if fb.is_hidden != hidden:
            fb.is_hidden = hidden
            fb.save(update_fields=["is_hidden"])  # Feedback.save refreshes the rating
            changed += 1
        photographers[fb.booking.photographer_id] = fb.booking.photographer
    return changed
