"""The wishlist: customers save photographers they like and come back to them later."""
from .models import Favourite, Photographer


def can_save(user, photographer):
    """Only customers can save, and only photographers customers can actually see."""
    from .roles import get_role
    return user.is_authenticated and get_role(user) != "photographer" and photographer.status == "approved"


def toggle(user, photographer):
    """Saves or un-saves. Returns True if the photographer is now saved."""
    existing = Favourite.objects.filter(user=user, photographer=photographer)
    if existing.exists():
        existing.delete()
        return False
    Favourite.objects.get_or_create(user=user, photographer=photographer)
    return True


def saved_ids(user):
    if not user.is_authenticated:
        return set()
    return set(Favourite.objects.filter(user=user).values_list("photographer_id", flat=True))


def saved_photographers(user):
    """Approved photographers only: a profile that was pulled after being saved quietly disappears."""
    return (Photographer.objects.approved().filter(favourited_by__user=user)
            .select_related("city__state").prefetch_related("packages").order_by("-favourited_by__created_at"))
