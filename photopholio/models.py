from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg
from django.utils import timezone
from django.utils.text import slugify


class Country(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "countries"

    def __str__(self):
        return self.name


class State(models.Model):
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="states")
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name"]
        unique_together = ("country", "name")

    def __str__(self):
        return f"{self.name}, {self.country.name}"


class City(models.Model):
    state = models.ForeignKey(State, on_delete=models.CASCADE, related_name="cities")
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "cities"
        unique_together = ("state", "name")

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    ROLE_CHOICES = [("customer", "Customer"), ("photographer", "Photographer")]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    # Decides which login page and which part of the site this account uses.
    # Accounts with no profile (e.g. admin-created) are treated as customers.
    role = models.CharField(max_length=12, choices=ROLE_CHOICES, default="customer")
    dob = models.DateField("date of birth", null=True, blank=True)
    address = models.TextField(blank=True)
    phone_no = models.BigIntegerField("phone number", null=True, blank=True)
    image = models.ImageField(upload_to="profiles/", blank=True)
    city = models.ForeignKey(City, on_delete=models.SET_NULL, null=True, blank=True)
    state = models.ForeignKey(State, on_delete=models.SET_NULL, null=True, blank=True)
    country = models.ForeignKey(Country, on_delete=models.SET_NULL, null=True, blank=True)

    def save(self, *args, **kwargs):
        # Keep state/country in sync with the chosen city
        if self.city_id:
            self.state = self.city.state
            self.country = self.city.state.country
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Profile of {self.user.username}"


class PhotographerQuerySet(models.QuerySet):
    def approved(self):
        """Only these are shown to customers."""
        return self.filter(status="approved")


class Photographer(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("pending", "Pending review"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]
    MIN_PHOTOS = 4
    MIN_BIO_CHARS = 50

    SPECIALITY_CHOICES = [
        ("wedding", "Wedding"),
        ("portrait", "Portrait"),
        ("fashion", "Fashion"),
        ("street", "Street"),
        ("event", "Event"),
        ("travel", "Travel"),
        ("product", "Product"),
        ("wildlife", "Wildlife"),
    ]

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    bio = models.TextField()
    tags = models.CharField("keywords", max_length=160, blank=True,
                            help_text="Words customers may search for, separated by commas. For example: outdoor, candid, pre-wedding, drone.")
    specialities = models.CharField(max_length=30, choices=SPECIALITY_CHOICES)
    experience_years = models.PositiveIntegerField(default=1)
    rating = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(5)])
    # Extra fields (beyond the original data dictionary) needed for search & booking:
    city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="photographers")
    image = models.ImageField(upload_to="photographers/", blank=True)
    price_per_day = models.PositiveIntegerField(default=5000, help_text="Price in INR for one event day")
    is_available = models.BooleanField(default=True)

    # --- travel policy (see travel.py): the customer pays travel, the photographer sets the price ---
    TRAVEL_SCOPE_CHOICES = [
        ("city", "Only in my city"),
        ("state", "Anywhere in my state"),
        ("india", "Anywhere in India"),
    ]
    # Money: the admin pays photographers out (see money.py). The percent is empty for "use the site default".
    commission_percent = models.PositiveSmallIntegerField("commission % (override)", null=True, blank=True,
                                                          validators=[MaxValueValidator(50)],
                                                          help_text="Leave empty to use the site default.")
    payout_details = models.CharField("payout details", max_length=120, blank=True,
                                      help_text="UPI ID or bank account where earnings are sent. Only the admin sees this.")
    travel_scope = models.CharField("where I take events", max_length=5, choices=TRAVEL_SCOPE_CHOICES, default="india")
    travel_fee_state = models.PositiveIntegerField("travel charge: other city in my state", default=0,
                                                   help_text="One charge for the whole trip, in INR. 0 means no charge.")
    travel_fee_india = models.PositiveIntegerField("travel charge: another state", default=0,
                                                   help_text="One charge for the whole trip, in INR. 0 means no charge.")
    stay_fee_per_day = models.PositiveIntegerField("stay and food per day away", default=0,
                                                   help_text="Added for every day of an event outside your city. 0 if the customer arranges it.")

    # --- photographer accounts and admin approval -------------------------------
    # The login account that owns this profile (empty for profiles added by the admin).
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="photographer_profile")
    # Default is "approved" so profiles added by the admin, and all existing profiles,
    # stay visible. Self-registered photographers are created as "draft" in the view.
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="approved", db_index=True)
    rejection_reason = models.TextField(blank=True, help_text="Shown to the photographer so they can fix and resubmit.")
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="reviewed_photographers")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # Set by the admin after checking the photographer's identity / work. Customers see a badge.
    is_verified = models.BooleanField("verified", default=False, db_index=True,
                                      help_text="Shows a Verified badge on the card and profile.")
    verified_at = models.DateTimeField(null=True, blank=True)

    objects = PhotographerQuerySet.as_manager()

    class Meta:
        ordering = ["-rating", "name"]

    def published_photo_count(self):
        return Photo.objects.filter(portfolio__photographer=self, status="published").count()

    def checklist(self):
        """What an admin looks for before approving. Used on the dashboard and in the admin."""
        return [
            ("Profile photo uploaded", bool(self.image)),
            (f"Bio of at least {self.MIN_BIO_CHARS} characters", len((self.bio or "").strip()) >= self.MIN_BIO_CHARS),
            ("City added", bool(self.city_id)),
            (f"At least {self.MIN_PHOTOS} portfolio photos", self.published_photo_count() >= self.MIN_PHOTOS),
        ]

    @property
    def active_packages(self):
        """Packages customers can pick, Basic first. Uses prefetched data when a view asked for it."""
        order = Package.TIER_ORDER
        return sorted((p for p in self.packages.all() if p.is_active), key=lambda p: order[p.tier])

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(",") if t.strip()][:8]

    @property
    def starting_price(self):
        """The lowest package price, or the plain full-day price when there are no packages."""
        prices = [p.price for p in self.active_packages]
        return min(prices) if prices else self.price_per_day

    def cancellations_count(self):
        """How many bookings this photographer cancelled themselves (admin keeps an eye on this)."""
        return Booking.objects.filter(photographer=self, cancelled_by="photographer").count()

    @property
    def ready_for_review(self):
        return all(ok for _, ok in self.checklist())

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "photographer"
            slug, n = base, 2
            while Photographer.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @property
    def initials(self):
        return "".join(part[0] for part in self.name.split()[:2]).upper()

    @property
    def cover(self):
        """First published photo of this photographer (used as card image)."""
        return (
            Photo.objects.filter(portfolio__photographer=self, status="published")
            .order_by("portfolio_id", "order_id")
            .first()
        )

    def refresh_rating(self):
        # Reviews the admin has hidden do not count towards the rating either.
        avg = Feedback.objects.filter(booking__photographer=self, is_hidden=False).aggregate(a=Avg("rating"))["a"]
        self.rating = round(avg, 1) if avg else 0
        self.save(update_fields=["rating"])


class Portfolio(models.Model):
    photographer = models.ForeignKey(Photographer, on_delete=models.CASCADE, related_name="portfolios")
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.photographer.name})"


class Photo(models.Model):
    STATUS_CHOICES = [("published", "Published"), ("hidden", "Hidden")]

    portfolio = models.ForeignKey(Portfolio, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="portfolio/")
    caption = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    order_id = models.IntegerField("display order", default=1)
    # The original schema called this ORDER_STATUS; here it controls public visibility.
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="published")

    class Meta:
        ordering = ["order_id"]

    def __str__(self):
        return f"Photo {self.order_id} of {self.portfolio.title}"


class Booking(models.Model):
    # pending   = customer asked, waiting for the photographer
    # accepted  = photographer said yes, customer must pay within the payment window
    # confirmed = paid
    STATUS_CHOICES = [
        ("pending", "Awaiting photographer"),
        ("accepted", "Awaiting payment"),
        ("confirmed", "Confirmed"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
        ("declined", "Declined"),
    ]
    CANCELLED_BY_CHOICES = [
        ("customer", "Customer"),
        ("photographer", "Photographer"),
        ("admin", "Admin"),
        ("system", "System"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bookings")
    photographer = models.ForeignKey(Photographer, on_delete=models.CASCADE, related_name="bookings")
    booking_date = models.DateField(default=timezone.localdate)
    # The first day of the event. A multi-day event also has end_date (the last day); a one-day event has end_date == event_date.
    event_date = models.DateField("first day")
    end_date = models.DateField("last day", null=True, blank=True)
    # Where the shoot is. India only for now. Old bookings made before this existed simply have these empty.
    start_time = models.TimeField("event starts at", null=True, blank=True)
    venue_name = models.CharField(max_length=120, blank=True)
    event_address = models.TextField(blank=True)
    event_city = models.CharField(max_length=100, blank=True)
    event_state = models.ForeignKey("State", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    event_pincode = models.CharField(max_length=6, blank=True)
    tool = models.CharField("equipment / requirements", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    # What the customer chose and agreed to. Copied at booking time so that a photographer changing
    # or deleting a package later never changes a booking that already exists.
    package = models.ForeignKey("Package", on_delete=models.SET_NULL, null=True, blank=True, related_name="bookings")
    package_name = models.CharField(max_length=60, blank=True)
    price = models.PositiveIntegerField(null=True, blank=True)  # the TOTAL for all days (day_rate x days)
    day_rate = models.PositiveIntegerField(null=True, blank=True)  # price of one day when it was booked
    # Travel and stay charged to the customer for an event outside the photographer's city (see travel.py). Added to the
    # service price. A snapshot, like price: changing the photographer's travel policy later never changes it.
    travel_fee = models.PositiveIntegerField(default=0)
    travel_zone = models.CharField(max_length=6, blank=True)
    # Money split, worked out when the customer pays and settled as the booking ends (see money.py).
    # held = paid, shoot not done yet; ready = the admin can pay the photographer; paid = sent;
    # void = refunded in full, nothing to pay; recover = refunded after the photographer was already paid.
    PAYOUT_CHOICES = [("", "Not paid yet"), ("held", "Held"), ("ready", "Ready to pay"), ("paid", "Paid out"),
                      ("void", "Nothing to pay"), ("recover", "To recover")]
    commission_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    platform_fee = models.PositiveIntegerField(default=0)       # PhotoPholio's commission
    payout_amount = models.PositiveIntegerField(default=0)      # what the photographer receives
    payout_status = models.CharField(max_length=8, blank=True, choices=PAYOUT_CHOICES, default="", db_index=True)
    payout_reference = models.CharField(max_length=60, blank=True)   # UTR / transaction id of the transfer
    payout_paid_at = models.DateTimeField(null=True, blank=True)
    payout_note = models.CharField(max_length=200, blank=True)

    # Shoot tracking and confirmation (see tracking.py). The photographer marks the shoot completed; the customer then
    # confirms it (or 48 hours pass); only then does the photographer's money become payable. A reported problem stops it.
    DISPUTE_KIND_CHOICES = [("not_arrived", "The photographer did not arrive"),
                            ("quality", "The shoot did not go as agreed"),
                            ("other", "Something else")]
    DISPUTE_OUTCOME_CHOICES = [("pay", "Paid the photographer"), ("refund", "Refunded the customer"),
                               ("dismiss", "Report dismissed"),
                               ("closed", "Closed with the booking")]
    completed_at = models.DateTimeField(null=True, blank=True)            # when the photographer marked it completed
    customer_confirmed_at = models.DateTimeField(null=True, blank=True)   # when the customer confirmed (or the 48 h passed)
    customer_confirmed_by_customer = models.BooleanField(default=False)   # False when it was confirmed automatically
    dispute_status = models.CharField(max_length=8, blank=True, choices=[("", "None"), ("open", "Open"), ("resolved", "Resolved")])
    dispute_kind = models.CharField(max_length=12, blank=True, choices=DISPUTE_KIND_CHOICES)
    dispute_text = models.TextField(blank=True)
    dispute_opened_at = models.DateTimeField(null=True, blank=True)
    dispute_outcome = models.CharField(max_length=8, blank=True, choices=DISPUTE_OUTCOME_CHOICES)
    dispute_note = models.TextField(blank=True, help_text="The admin's decision and why. The customer and photographer are told.")
    dispute_resolved_at = models.DateTimeField(null=True, blank=True)
    dispute_resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    accepted_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.CharField(max_length=12, choices=CANCELLED_BY_CHOICES, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)
    # Photos are delivered as a link (Google Drive, Dropbox, ...) once the shoot is completed.
    delivery_link = models.URLField(max_length=500, blank=True)
    delivery_note = models.CharField(max_length=255, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} > {self.photographer.name} on {self.date_label}"

    def save(self, *args, **kwargs):
        # A one-day booking stores end_date = event_date, so every date check can treat bookings as ranges.
        if self.end_date is None:
            self.end_date = self.event_date
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = list(kwargs["update_fields"]) + ["end_date"]
        super().save(*args, **kwargs)

    @property
    def last_date(self):
        return self.end_date or self.event_date

    @property
    def days(self):
        return (self.last_date - self.event_date).days + 1

    @property
    def is_multi_day(self):
        return self.days > 1

    def day_list(self):
        from datetime import timedelta
        return [self.event_date + timedelta(days=i) for i in range(self.days)]

    @property
    def date_label(self):
        """'12 Dec 2026' or '12 - 15 Dec 2026' (or across months / years when needed)."""
        a, b = self.event_date, self.last_date
        if a == b:
            return f"{a.day} {a:%b %Y}"
        if (a.year, a.month) == (b.year, b.month):
            return f"{a.day} - {b.day} {b:%b %Y}"
        if a.year == b.year:
            return f"{a.day} {a:%b} - {b.day} {b:%b %Y}"
        return f"{a.day} {a:%b %Y} - {b.day} {b:%b %Y}"

    @property
    def place(self):
        """'Venue, City, State' for display. Empty for bookings made before addresses existed."""
        bits = [self.venue_name, self.event_city, self.event_state.name if self.event_state_id else ""]
        return ", ".join(b for b in bits if b)

    @property
    def payment_deadline(self):
        if self.status == "accepted" and self.accepted_at:
            from datetime import timedelta
            from .policy import PAYMENT_WINDOW_HOURS
            return self.accepted_at + timedelta(hours=PAYMENT_WINDOW_HOURS)
        return None

    @property
    def has_open_dispute(self):
        return self.dispute_status == "open"

    @property
    def confirm_deadline(self):
        """After this the shoot counts as confirmed even if the customer did nothing."""
        if self.completed_at and not self.customer_confirmed_at:
            from datetime import timedelta
            from .policy import CONFIRM_WINDOW_HOURS
            return self.completed_at + timedelta(hours=CONFIRM_WINDOW_HOURS)
        return None

    @property
    def awaiting_confirmation(self):
        return self.status == "completed" and self.completed_at is not None and not self.customer_confirmed_at

    def can_report(self, today=None):
        """The customer may report a problem from the first day of the event until the photographer has been paid."""
        today = today or timezone.localdate()
        return (self.status in ("confirmed", "completed") and self.dispute_status == ""
                and self.payout_status in ("held", "ready") and today >= self.event_date)

    def day_states(self):
        """One dict per event day for display: {date, state, arrived_at, started_at, finished_at, seen}. Never writes to the database."""
        rows = {d.date: d for d in self.shoot_days.all()}
        out = []
        for day in self.day_list():
            r = rows.get(day)
            out.append({"date": day, "state": r.state if r else "upcoming", "arrived_at": r.arrived_at if r else None,
                        "started_at": r.started_at if r else None, "finished_at": r.finished_at if r else None,
                        "seen": bool(r and r.customer_seen_at)})
        return out

    def todays_step(self, today=None):
        """The one button the photographer should see today: arrived, start, finish, or '' (nothing to do)."""
        today = today or timezone.localdate()
        if self.status != "confirmed" or today not in self.day_list():
            return ""
        row = next((d for d in self.shoot_days.all() if d.date == today), None)
        state = row.state if row else "upcoming"
        return {"upcoming": "arrived", "arrived": "start", "in_progress": "finish"}.get(state, "")

    @property
    def paid_payment(self):
        return self.payments.filter(status="completed").order_by("-payment_date").first()

    @property
    def can_complete(self):
        """A shoot can only be marked completed once its date has arrived."""
        return self.status == "confirmed" and self.last_date <= timezone.localdate()

    @property
    def amount(self):
        # Bookings made before packages existed have no snapshot (price is empty): fall back to the day rate.
        return self.service_price + self.travel_fee

    @property
    def earnings(self):
        """(percent, commission, payout) as the photographer sees it: the real figures once paid, otherwise a preview."""
        from . import money
        if self.payout_status:
            return money.Split(self.commission_percent or 0, self.platform_fee, self.payout_amount)
        return money.preview(self)

    @property
    def service_price(self):
        """The photographer's fee for the shoot itself (all days), without travel."""
        return self.price if self.price is not None else self.photographer.price_per_day

    @property
    def is_paid(self):
        return self.payments.filter(status="completed").exists()

    @property
    def can_review(self):
        return self.status == "completed" and not self.feedbacks.exists()

    @property
    def pending_reschedule(self):
        return self.reschedules.filter(status="pending").first()

    @property
    def reschedule_count(self):
        return self.reschedules.filter(status="accepted").count()

    def can_request_reschedule(self, today=None):
        """Why not? is answered by booking_flow.request_reschedule; this is the quick yes/no for buttons."""
        from .policy import MAX_RESCHEDULES, RESCHEDULE_MIN_DAYS, days_before
        return (self.status in ("accepted", "confirmed")
                and self.pending_reschedule is None
                and self.reschedule_count < MAX_RESCHEDULES
                and days_before(self.event_date, today) >= RESCHEDULE_MIN_DAYS)


class RescheduleRequest(models.Model):
    """The customer asks to move a booking to another day; the photographer says yes or no."""
    STATUS_CHOICES = [
        ("pending", "Waiting for photographer"),
        ("accepted", "Accepted"),
        ("declined", "Declined"),
        ("withdrawn", "Withdrawn by customer"),
        ("closed", "Closed (booking ended)"),
    ]
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="reschedules")
    old_date = models.DateField()
    new_date = models.DateField()
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    decline_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.booking_id}: {self.old_date} -> {self.new_date} ({self.status})"

    @property
    def new_end(self):
        """The booking keeps its length, so moving a 4-day event to the 20th means 20th to 23rd."""
        from datetime import timedelta
        return self.new_date + timedelta(days=self.booking.days - 1)

    @property
    def new_label(self):
        from datetime import timedelta
        days = self.booking.days
        if days == 1:
            return f"{self.new_date.day} {self.new_date:%b %Y}"
        b = self.new_end
        if (self.new_date.year, self.new_date.month) == (b.year, b.month):
            return f"{self.new_date.day} - {b.day} {b:%b %Y}"
        return f"{self.new_date.day} {self.new_date:%b %Y} - {b.day} {b:%b %Y}"


class Payment(models.Model):
    METHOD_CHOICES = [
        ("credit_card", "Credit Card"),
        ("debit_card", "Debit Card"),
        ("upi", "UPI"),
        ("paypal", "PayPal"),
        ("other", "Other"),
    ]
    STATUS_CHOICES = [("pending", "Pending"), ("completed", "Completed"), ("failed", "Failed")]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments")
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="payments")
    amount = models.PositiveIntegerField()
    payment_method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    payment_date = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    # Refund is tracked separately: cancelling a booking and returning the money are two different steps.
    # The money part is "pending" until an admin marks it processed (stands in for a gateway confirmation).
    REFUND_STATUS_CHOICES = [("none", "No refund"), ("pending", "Refund pending"), ("refunded", "Refunded")]
    refund_amount = models.PositiveIntegerField(default=0)
    refund_status = models.CharField(max_length=10, choices=REFUND_STATUS_CHOICES, default="none")
    refunded_at = models.DateTimeField(null=True, blank=True)
    refund_note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-payment_date"]

    def __str__(self):
        return f"Rs.{self.amount} ({self.get_status_display()})"


class Feedback(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="feedbacks")
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="feedbacks")
    rating = models.FloatField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField()
    review_date = models.DateTimeField(auto_now_add=True)
    # The photographer may answer a review once (and edit the answer). The admin may hide a review that
    # breaks the rules; a hidden review is not shown and does not count towards the rating.
    reply = models.TextField(blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    is_hidden = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-review_date"]
        unique_together = ("user", "booking")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.booking.photographer.refresh_rating()

    def __str__(self):
        return f"{self.rating} by {self.user.username}"


class Favourite(models.Model):
    """A customer's saved photographer (the wishlist)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favourites")
    photographer = models.ForeignKey(Photographer, on_delete=models.CASCADE, related_name="favourited_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("user", "photographer")

    def __str__(self):
        return f"{self.user.username} saved {self.photographer.name}"


class ContactUs(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    subject = models.CharField(max_length=200)
    message = models.TextField()
    phone = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "contact message"
        verbose_name_plural = "contact messages"

    def __str__(self):
        return f"{self.subject} from {self.name}"


class LoginAttempt(models.Model):
    """One failed login. Too many recent ones for the same account locks the login for a while (see throttle.py)."""
    key = models.CharField(max_length=254, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)  # for the admin to look at; never used to decide a lock
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.key} at {self.created_at:%d %b %H:%M}"


class BlockedDate(models.Model):
    """A day the photographer marked as unavailable (holiday, another job). Customers just see it as taken."""
    photographer = models.ForeignKey(Photographer, on_delete=models.CASCADE, related_name="blocked_dates")
    date = models.DateField()
    reason = models.CharField(max_length=120, blank=True, help_text="Only the photographer and the admin see this.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date"]
        unique_together = ("photographer", "date")

    def __str__(self):
        return f"{self.photographer.name} blocked {self.date}"


class Package(models.Model):
    """One of up to three things a photographer sells: Basic, Standard, Premium."""
    TIER_CHOICES = [("basic", "Basic"), ("standard", "Standard"), ("premium", "Premium")]
    TIER_ORDER = {"basic": 0, "standard": 1, "premium": 2}

    photographer = models.ForeignKey(Photographer, on_delete=models.CASCADE, related_name="packages")
    tier = models.CharField(max_length=10, choices=TIER_CHOICES)
    price = models.PositiveIntegerField(help_text="Price in INR")
    hours = models.PositiveSmallIntegerField(default=4, help_text="Hours of coverage")
    edited_photos = models.PositiveIntegerField(default=100, help_text="Edited photos delivered")
    description = models.CharField(max_length=300, blank=True, help_text="What is included, in a sentence or two")
    is_active = models.BooleanField(default=True, help_text="Untick to stop offering it without deleting it")

    class Meta:
        unique_together = ("photographer", "tier")
        ordering = ["price"]

    def __str__(self):
        return f"{self.photographer.name}: {self.get_tier_display()} (Rs.{self.price})"


class ShootDay(models.Model):
    """What happened on one day of a booking: the photographer's three taps. One row per day, made when first needed."""
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="shoot_days")
    date = models.DateField()
    arrived_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    customer_seen_at = models.DateTimeField(null=True, blank=True)  # the customer tapped "Yes, they are here"

    class Meta:
        ordering = ["date"]
        unique_together = ("booking", "date")

    def __str__(self):
        return f"{self.booking_id} {self.date}: {self.state}"

    @property
    def state(self):
        if self.finished_at:
            return "finished"
        if self.started_at:
            return "in_progress"
        if self.arrived_at:
            return "arrived"
        return "upcoming"


class ShootEvent(models.Model):
    """The audit trail: who did what and when, on a booking. Rows are only ever added, never edited."""
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="shoot_events")
    day = models.DateField(null=True, blank=True)
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    actor_role = models.CharField(max_length=12)  # photographer / customer / admin / system
    action = models.CharField(max_length=30)
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.booking_id} {self.actor_role}: {self.action}"
