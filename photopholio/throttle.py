"""
Login lockout, like most real sites do it.

  - ATTEMPT WINDOW: wrong passwords are counted over a sliding window (the last 15 minutes).
  - MAX FAILED ATTEMPTS: that many wrong passwords inside the window lock the login.
  - LOCK DURATION: the lock lasts this long, counted from the wrong password that caused it (5 minutes).
    When the lock ends the count starts again from zero.

Why two numbers: the window decides WHEN you get locked, the lock decides HOW LONG. Someone who tries once every
3 minutes adds up to 5 attempts inside 15 minutes and gets locked, while a real person who mistyped is held up
for only 5 minutes. Trying slower than 5 times per 15 minutes (a guess every 4 minutes or slower) never locks,
which caps a slow attacker at 4 guesses per 15 minutes. The login page shows a live countdown.

The admin login (/admin/) is left exactly as it was: 5 wrong passwords within 5 minutes, locked for 5 minutes.
It simply uses DEFAULT below. Only the customer and photographer login ask for SITE.

* "Same account" means the same person whether they typed the username or the email.
* Names that do not exist are counted too, so the lock message never reveals whether an account exists.
* Attempts made while locked are not counted, so an attacker cannot keep the lock going forever.
* It lives in the database (not memory) so it works with several server processes, as on PythonAnywhere.
* The visitor's IP is stored for the admin to read. It can be faked, so it never decides a lock.

Honest limit: because the lock is per account, someone can lock a user out on purpose for a few minutes.
Real production sites add IP-level rate limiting at the web server or a service such as Cloudflare.
"""
import ipaddress
import math
from collections import namedtuple
from datetime import timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from .models import LoginAttempt

# ---- the numbers: change them here -------------------------------------------------
MAX_FAILED_ATTEMPTS = 5      # customer / photographer login
ATTEMPT_WINDOW_MINUTES = 15
LOCK_MINUTES = 5
# --------------------------------------------------------------------------------------

Policy = namedtuple("Policy", "scope max_attempts window_minutes lock_minutes")
SITE = Policy("site", MAX_FAILED_ATTEMPTS, ATTEMPT_WINDOW_MINUTES, LOCK_MINUTES)
# Used by anything that does not choose a policy, which today means the admin login. Same numbers it always had.
DEFAULT = Policy("site", 5, 5, 5)


def key_for(identifier, policy=DEFAULT):
    """One counter per account: the same key whether the username or the email was typed."""
    identifier = (identifier or "").strip().lower()
    User = get_user_model()
    user = User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier)).order_by("id").first()
    return f"{policy.scope}:{(user.username.lower() if user else identifier)}"[:120]


def lock_ends_at(attempt_times, policy=DEFAULT):
    """
    attempt_times: the times of the wrong passwords for one account, oldest first.
    Replays them with the rules and returns when the lock ends (None if it never locked).
    Attempts made during a lock are ignored, and when a lock ends the count restarts from zero.
    """
    window = timedelta(minutes=policy.window_minutes)
    lock = timedelta(minutes=policy.lock_minutes)
    recent, locked_until = [], None
    for t in attempt_times:
        if locked_until is not None:
            if t < locked_until:
                continue
            recent, locked_until = [], None  # the lock ran out: clean count
        recent = [r for r in recent if r > t - window]
        recent.append(t)
        if len(recent) >= policy.max_attempts:
            locked_until = t + lock
    return locked_until


def seconds_left(key, now=None, policy=DEFAULT):
    """0 if the login is open, otherwise the whole seconds (rounded up) until it opens again."""
    now = now or timezone.now()
    times = list(LoginAttempt.objects.filter(key=key).order_by("created_at").values_list("created_at", flat=True))
    ends = lock_ends_at(times, policy)
    if ends is None or ends <= now:
        return 0
    return math.ceil((ends - now).total_seconds())


def minutes_left(key, now=None, policy=DEFAULT):
    """0 if the login is open, otherwise how many whole minutes (rounded up) until it opens again."""
    seconds = seconds_left(key, now, policy)
    return max(1, math.ceil(seconds / 60)) if seconds else 0


def record_failure(key, ip=None):
    LoginAttempt.objects.create(key=key, ip_address=ip)
    LoginAttempt.objects.filter(created_at__lt=timezone.now() - timedelta(days=1)).delete()  # tidy up


def clear(key):
    LoginAttempt.objects.filter(key=key).delete()


def client_ip(request):
    """Best guess, only stored for the admin to read. It can be faked, so it is never used to lock anyone."""
    if request is None:
        return None
    raw = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def lock_message(minutes):
    return (f"Too many wrong attempts. For safety this login is locked for about {minutes} more minute"
            f"{'' if minutes == 1 else 's'}. Please try again after that.")


class ThrottledLoginMixin:
    """Put in front of a Django login form. The site login and the admin login both use it."""

    throttle_policy = DEFAULT

    # Filled in when the login is locked, so the page can show a live countdown.
    locked_seconds = 0
    locked_minutes = 0

    def _locked_error(self, key):
        seconds = seconds_left(key, policy=self.throttle_policy)
        if not seconds:
            return None
        self.locked_seconds = seconds
        self.locked_minutes = max(1, math.ceil(seconds / 60))
        return forms.ValidationError(lock_message(self.locked_minutes), code="locked")

    def clean(self):
        identifier = (self.cleaned_data.get("username") or "").strip()
        if not identifier or not self.cleaned_data.get("password"):
            return super().clean()
        key = key_for(identifier, self.throttle_policy)
        locked = self._locked_error(key)
        if locked:
            raise locked
        try:
            cleaned = super().clean()
        except forms.ValidationError as error:
            if error.code != "invalid_login":
                raise
            record_failure(key, client_ip(getattr(self, "request", None)))
            locked = self._locked_error(key)  # this may have been the failure that locks it
            if locked:
                raise locked
            raise
        clear(key)
        return cleaned
