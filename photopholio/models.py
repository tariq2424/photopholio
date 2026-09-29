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
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
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


class Photographer(models.Model):
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

    class Meta:
        ordering = ["-rating", "name"]

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
        avg = Feedback.objects.filter(booking__photographer=self).aggregate(a=Avg("rating"))["a"]
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
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bookings")
    photographer = models.ForeignKey(Photographer, on_delete=models.CASCADE, related_name="bookings")
    booking_date = models.DateField(default=timezone.localdate)
    event_date = models.DateField()
    tool = models.CharField("equipment / requirements", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} > {self.photographer.name} on {self.event_date}"

    @property
    def amount(self):
        return self.photographer.price_per_day

    @property
    def is_paid(self):
        return self.payments.filter(status="completed").exists()

    @property
    def can_review(self):
        return self.status == "completed" and not self.feedbacks.exists()


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

    class Meta:
        ordering = ["-review_date"]
        unique_together = ("user", "booking")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.booking.photographer.refresh_rating()

    def __str__(self):
        return f"{self.rating} by {self.user.username}"


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
