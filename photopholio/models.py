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
    specialities = models.CharField(max_length=30, choices=SPECIALITY_CHOICES)
    experience_years = models.PositiveIntegerField(default=1)
    rating = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(5)])
    # Extra fields (beyond the original data dictionary) needed for search & booking:
    city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="photographers")
    image = models.ImageField(upload_to="photographers/", blank=True)
    price_per_day = models.PositiveIntegerField(default=5000, help_text="Price in INR for one event day")
    is_available = models.BooleanField(default=True)

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
    event_date = models.DateField()
    tool = models.CharField("equipment / requirements", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    # What the customer chose and agreed to. Copied at booking time so that a photographer changing
    # or deleting a package later never changes a booking that already exists.
    package = models.ForeignKey("Package", on_delete=models.SET_NULL, null=True, blank=True, related_name="bookings")
    package_name = models.CharField(max_length=60, blank=True)
    price = models.PositiveIntegerField(null=True, blank=True)
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
        return f"{self.user.username} > {self.photographer.name} on {self.event_date}"

    @property
    def payment_deadline(self):
        if self.status == "accepted" and self.accepted_at:
            from datetime import timedelta
            from .policy import PAYMENT_WINDOW_HOURS
            return self.accepted_at + timedelta(hours=PAYMENT_WINDOW_HOURS)
        return None

    @property
    def paid_payment(self):
        return self.payments.filter(status="completed").order_by("-payment_date").first()

    @property
    def can_complete(self):
        """A shoot can only be marked completed once its date has arrived."""
        return self.status == "confirmed" and self.event_date <= timezone.localdate()

    @property
    def amount(self):
        # Bookings made before packages existed have no snapshot (price is empty): fall back to the day rate.
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
