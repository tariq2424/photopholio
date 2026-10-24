"""Fill the database with demo data (fictional photographers, generated portfolio images).

Usage:  python manage.py seed_demo          (skips if data already exists)
        python manage.py seed_demo --reset  (wipes photographers first)
"""
import random
from datetime import datetime, time, timedelta
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFilter

from photopholio import money
from photopholio.packages import create_sample_packages
from photopholio.models import (Booking, City, Country, Feedback, Payment,
                                Photo, Photographer, Portfolio, State)

PLACES = {
    "Gujarat": ["Ahmedabad", "Vadodara", "Surat"],
    "Maharashtra": ["Mumbai", "Pune"],
    "Karnataka": ["Bengaluru"],
    "Delhi": ["New Delhi"],
    "Kerala": ["Kochi"],
    "Rajasthan": ["Jaipur"],
}

# Search keywords given to every demo photographer, by style (real photographers write their own).
DEFAULT_TAGS = {
    "wedding": "candid, traditional, pre-wedding, outdoor", "portrait": "studio, natural light, headshots",
    "fashion": "studio, lookbook, outdoor", "street": "candid, documentary, outdoor",
    "event": "candid, corporate, parties", "travel": "outdoor, destination, drone",
    "product": "studio, ecommerce, catalogue", "wildlife": "outdoor, nature, birds",
}

# name, speciality, city, years, price, bio, portfolio title, photo captions
PHOTOGRAPHERS = [
    ("Aarav Mehta", "wedding", "Ahmedabad", 12, 18000,
     "Candid, story-first wedding photographer. Aarav has covered 400+ weddings across Gujarat and Rajasthan and blends traditional rituals with a documentary eye.",
     "Royal Gujarati Wedding", ["Pheras at sunrise", "Mehendi details", "The baraat arrives", "First look"]),
    ("Kavya Iyer", "portrait", "Bengaluru", 8, 9000,
     "Natural-light portrait artist who makes even camera-shy people relax. Studio and outdoor sessions, headshots and family portraits.",
     "Faces in Soft Light", ["Window light portrait", "Family on the terrace", "Studio headshot", "Golden hour"]),
    ("Rohan Desai", "fashion", "Mumbai", 10, 15000,
     "Editorial and lookbook photographer working with designers and models. Bold colour, clean lighting and fast turnaround.",
     "Monsoon Lookbook", ["Runway backstage", "Street style", "Studio editorial", "Detail shot"]),
    ("Sana Qureshi", "street", "New Delhi", 6, 6000,
     "Street and documentary photographer who finds quiet stories in crowded lanes. Great for city walks and photo-essays.",
     "Old Delhi Mornings", ["Chai stall at dawn", "Spice market", "Rooftop kites", "Lane light"]),
    ("Dev Patel", "event", "Vadodara", 9, 8000,
     "Corporate events, birthdays and concerts. Dev delivers a first batch of edited photos within 48 hours.",
     "Navratri Nights", ["Garba circle", "Dhol player", "Crowd energy", "Stage lights"]),
    ("Ishita Nair", "travel", "Kochi", 7, 7500,
     "Travel and destination photographer covering Kerala backwaters, hill stations and coastal shoots.",
     "Backwater Days", ["Houseboat sunrise", "Fishing nets", "Tea gardens", "Monsoon coast"]),
    ("Neel Shah", "product", "Surat", 5, 5500,
     "E-commerce, jewellery and textile product photography with clean backgrounds and accurate colour.",
     "Textile Close-ups", ["Silk texture", "Jewellery flat lay", "Packaging shot", "Fabric folds"]),
    ("Meera Joshi", "wildlife", "Jaipur", 14, 20000,
     "Wildlife photographer with 14 years in Ranthambore and Gir. Patient, ethical and known for tiger portraits.",
     "Ranthambore Diaries", ["Tiger at dawn", "Peacock display", "Waterhole visit", "Forest mist"]),
]

COMMENTS = [
    "Very professional and easy to work with. Photos arrived earlier than promised.",
    "Loved the candid moments. Everyone in the family asked for the contact.",
    "Great eye for light and composition. Would book again.",
    "Punctual, friendly and delivered exactly what we discussed.",
]


def make_image(seed, size=(1200, 900)):
    rnd = random.Random(seed)
    rgb = lambda lo, hi: tuple(rnd.randint(lo, hi) for _ in range(3))
    base = Image.composite(Image.new("RGB", size, rgb(20, 200)), Image.new("RGB", size, rgb(40, 220)),
                           Image.linear_gradient("L").resize(size)).convert("RGBA")
    blobs = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(blobs)
    for _ in range(16):
        r, x, y = rnd.randint(50, 170), rnd.randint(0, size[0]), rnd.randint(0, size[1])
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(*rgb(120, 255), rnd.randint(60, 140)))
    img = Image.alpha_composite(base, blobs.filter(ImageFilter.GaussianBlur(8))).convert("RGB")
    buf = BytesIO()
    img.save(buf, "JPEG", quality=80)
    return ContentFile(buf.getvalue())


def backdate(model, pk, **fields):
    """auto_now_add fields ignore values passed at creation, so set them with update()."""
    model.objects.filter(pk=pk).update(**fields)


class Command(BaseCommand):
    help = "Create demo photographers, portfolios, users, bookings and reviews."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing photographers first")

    def handle(self, *args, **opts):
        if opts["reset"]:
            Photographer.objects.all().delete()
        if Photographer.objects.exists():
            self.stdout.write("Photographers already exist. Use --reset to recreate.")
            return

        india, _ = Country.objects.get_or_create(name="India")
        cities = {}
        for state_name, city_names in PLACES.items():
            state, _ = State.objects.get_or_create(country=india, name=state_name)
            for cn in city_names:
                cities[cn], _ = City.objects.get_or_create(state=state, name=cn)

        # Demo customers (password is for local/demo use only)
        customers = []
        for i, (uname, full) in enumerate([("demo", "Demo User"), ("riya", "Riya Sharma"),
                                            ("karan", "Karan Verma"), ("zoya", "Zoya Khan")]):
            u, created = User.objects.get_or_create(username=uname, defaults={
                "first_name": full, "email": f"{uname}@example.com"})
            if created:
                u.set_password("demo12345")
                u.save()
            customers.append(u)

        now = timezone.now()
        today = timezone.localdate()
        for idx, (name, sp, city, years, price, bio, ptitle, captions) in enumerate(PHOTOGRAPHERS):
            rnd = random.Random(idx)
            ph = Photographer.objects.create(name=name, specialities=sp, city=cities[city],
                                             experience_years=years, price_per_day=price, bio=bio,
                                             tags=DEFAULT_TAGS.get(sp, ""))
            create_sample_packages(ph)
            # Realistic travel policies so the demo shows free local events, outstation charges and "only in my city".
            ph.travel_scope = "city" if idx % 5 == 0 else ("state" if idx % 3 == 0 else "india")
            ph.travel_fee_state = 1500 + 500 * (idx % 3)
            ph.travel_fee_india = 4000 + 1000 * (idx % 4)
            ph.stay_fee_per_day = 1000 + 500 * (idx % 3)
            ph.save(update_fields=["travel_scope", "travel_fee_state", "travel_fee_india", "stay_fee_per_day"])
            pf = Portfolio.objects.create(photographer=ph, title=ptitle,
                                          description=f"A selection from {name}'s recent {ph.get_specialities_display().lower()} work.")
            added = now - timedelta(days=rnd.randint(75, 140), hours=rnd.randint(0, 20))
            backdate(Portfolio, pf.pk, created_at=added)
            for n, cap in enumerate(captions, start=1):
                photo = Photo(portfolio=pf, caption=cap, order_id=n)
                photo.image.save(f"{ph.slug}-{n}.jpg", make_image(idx * 10 + n), save=False)
                photo.save()
                backdate(Photo, photo.pk, uploaded_at=added + timedelta(minutes=n * 7))
            # two completed, reviewed bookings per photographer
            for k in range(2):
                user = customers[(idx + k) % len(customers)]
                # events happened between roughly 2 and 3 months ago, spread out
                event = today - timedelta(days=rnd.randint(30, 100))
                booked_on = datetime.combine(event - timedelta(days=rnd.randint(12, 30)), time(rnd.randint(9, 20), rnd.randint(0, 59)))
                booked_on = timezone.make_aware(booked_on)
                b = Booking.objects.create(user=user, photographer=ph, tool="Standard kit",
                                           event_date=event, status="completed")
                backdate(Booking, b.pk, created_at=booked_on, booking_date=booked_on.date())
                pay = Payment.objects.create(user=user, booking=b, amount=price, payment_method="upi", status="completed")
                backdate(Payment, pay.pk, payment_date=booked_on + timedelta(minutes=6))
                # The money side: paid, held, then ready once completed. Older shoots were already paid out to the photographer.
                b.refresh_from_db()
                money.on_paid(b)
                money.on_completed(b)
                if (today - event).days > 60:
                    money.mark_paid(b, f"UTR{100000 + b.pk * 37}", now=timezone.make_aware(
                        datetime.combine(event + timedelta(days=rnd.randint(3, 8)), time(11, 0))))
                fb = Feedback.objects.create(user=user, booking=b, rating=rnd.choice([4, 4.5, 5, 5]),
                                             comment=COMMENTS[(idx + k) % len(COMMENTS)])
                backdate(Feedback, fb.pk, review_date=timezone.make_aware(
                    datetime.combine(event + timedelta(days=rnd.randint(1, 5)), time(rnd.randint(10, 22), rnd.randint(0, 59)))))
        self.stdout.write(self.style.SUCCESS(
            "Demo data ready. Customer login: demo / demo12345 (change or delete in production)."))
