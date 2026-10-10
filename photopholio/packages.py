"""Sample Basic / Standard / Premium packages, built from a photographer's day rate (used by seed_demo and add_sample_packages)."""
from .models import Package

# tier: (share of the day rate, hours, edited photos, what is included)
SAMPLE = {
    "basic": (0.5, 3, 80, "Short coverage with a small set of edited photos in an online gallery."),
    "standard": (1.0, 6, 200, "Half to full day coverage, edited photos in an online gallery, one round of changes."),
    "premium": (1.6, 10, 400, "Full day coverage, a second photographer on request, a larger edited set and a printed album."),
}


def create_sample_packages(photographer):
    """Creates the three packages unless the photographer already has some. Returns how many were created."""
    if photographer.packages.exists():
        return 0
    for tier, (share, hours, photos, text) in SAMPLE.items():
        price = max(500, round(photographer.price_per_day * share / 100) * 100)
        Package.objects.create(photographer=photographer, tier=tier, price=price, hours=hours, edited_photos=photos, description=text)
    return len(SAMPLE)
