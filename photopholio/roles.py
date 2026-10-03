"""Small helpers for the customer / photographer split."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def get_role(user):
    """'customer' or 'photographer'. Accounts without a profile (e.g. admin-created) count as customers."""
    if not user.is_authenticated:
        return None
    profile = getattr(user, "profile", None)
    return profile.role if profile else "customer"


def photographer_required(view):
    """Only logged-in photographer accounts may open the view."""
    @login_required(login_url="pro_login")
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if get_role(request.user) != "photographer":
            messages.error(request, "That page is only for photographer accounts.")
            return redirect("home")
        return view(request, *args, **kwargs)
    return wrapper
