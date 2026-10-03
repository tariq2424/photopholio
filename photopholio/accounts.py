"""Login helper: lets people sign in with their username OR email, ignoring capital letters."""
from django.contrib.auth import authenticate, get_user_model
from django.db.models import Q


def authenticate_identifier(request, identifier, password):
    """
    Returns the user if `identifier` (username or email, any capitalisation) and `password` match, else None.
    Still goes through Django's normal `authenticate()`, so password hashing and the "is active" check are unchanged.
    """
    identifier = (identifier or "").strip()
    if not identifier or not password:
        return None
    User = get_user_model()
    candidates = list(User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier)).order_by("id")[:10])
    candidates.sort(key=lambda u: u.username != identifier)  # an exact username match is tried first
    for candidate in candidates:
        user = authenticate(request, username=candidate.username, password=password)
        if user is not None:
            return user
    if not candidates:
        User().set_password(password)  # same work as a real attempt, so response time does not reveal which accounts exist
    return None
