"""
In-app notifications (the bell in the top bar).

emails._send calls notify() for every message addressed to a signed-up person, so the bell and the
inbox always tell the same story. A problem here must never undo a booking, so notify() swallows errors.
"""
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse

from .models import Notification

logger = logging.getLogger(__name__)

KEEP_PER_USER = 100


def kind_for(subject):
    s = (subject or "").lower()
    if any(w in s for w in ("payment", "payout", "refund")):
        return "payment"
    if "review" in s:
        return "review"
    if "photos" in s:
        return "photos"
    if any(w in s for w in ("profile", "approved")):
        return "profile"
    if any(w in s for w in ("problem", "report", "lapsed", "declined", "cancelled")):
        return "alert"
    return "booking"


def notify(user, title, body="", url="", subject=""):
    try:
        path = (url or "")
        if path.startswith(settings.SITE_URL):
            path = path[len(settings.SITE_URL):]
        item = Notification.objects.create(user=user, kind=kind_for(subject or title), title=title[:160],
                                           body=(body or "")[:300], url=path[:300])
        old = list(Notification.objects.filter(user=user).values_list("pk", flat=True)[KEEP_PER_USER:])
        if old:
            Notification.objects.filter(pk__in=old).delete()
        return item
    except Exception:
        logger.exception("Could not save a notification for %s", getattr(user, "pk", "?"))
        return None


def unread_count(user):
    return Notification.objects.filter(user=user, is_read=False).count() if user.is_authenticated else 0


def welcome(user):
    """
    Once per person, the first time they sign in.
    A photographer who is still setting up gets the first steps. A photographer who already has a
    profile in review, live or sent back is only greeted, so nobody is told to redo finished work.
    """
    if Notification.objects.filter(user=user, kind="welcome").exists():
        return None
    from .models import Photographer
    from .roles import get_role
    name = user.first_name or user.username
    title = f"Welcome to PhotoPholio, {name}"
    if get_role(user) == "photographer":
        profile = Photographer.objects.filter(user=user).first()
        if profile is None:
            body, url = ("Start by creating your profile: a photo, a short bio and your city. Then add at least 4 photos "
                         "to every portfolio and submit it for review.", reverse("pro_profile"))
        elif profile.status == "draft":
            body, url = ("Finish your checklist: every portfolio needs at least 4 photos. Then submit your profile for review.",
                         reverse("pro_dashboard"))
        elif profile.status == "approved":
            body, url = ("Your profile is live. New requests and updates will show up here.", reverse("pro_dashboard"))
        elif profile.status == "pending":
            body, url = ("Your profile is waiting for review. We will tell you as soon as it is decided.", reverse("pro_dashboard"))
        else:
            body, url = ("Your profile needs a few changes. Open your dashboard to see what to fix.", reverse("pro_dashboard"))
    else:
        body, url = ("Browse real portfolios, check who is free on your date and send a booking request. "
                     "You pay only after they accept.", reverse("photographer_list"))
    item = notify(user, title, body, url)
    if item:
        Notification.objects.filter(pk=item.pk).update(kind="welcome")
    return item


def announce_new_photographer(ph):
    """Tell everyone else that a photographer just went live. Sent once per photographer."""
    try:
        url = reverse("photographer_detail", args=[ph.slug])
        if Notification.objects.filter(kind="new", url=url).exists():
            return 0
        where = f" in {ph.city.name}" if ph.city_id else ""
        title = f"New photographer: {ph.name}"
        body = f"{ph.name}{where} just joined PhotoPholio. Take a look at their portfolio."
        people = User.objects.filter(is_active=True)
        if ph.user_id:
            people = people.exclude(pk=ph.user_id)
        items = [Notification(user_id=pk, kind="new", title=title[:160], body=body[:300], url=url)
                 for pk in people.values_list("pk", flat=True)]
        Notification.objects.bulk_create(items, batch_size=500)
        return len(items)
    except Exception:
        logger.exception("Could not announce photographer %s", getattr(ph, "pk", "?"))
        return 0
