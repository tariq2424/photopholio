from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Booking, City, Country, Feedback, Photographer, State


class PhotoPholioTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name="India")
        state = State.objects.create(country=country, name="Gujarat")
        self.city = City.objects.create(state=state, name="Vadodara")
        self.ph = Photographer.objects.create(name="Test Snap", bio="bio", specialities="wedding",
                                              city=self.city, price_per_day=7000)
        self.user = User.objects.create_user("tester", password="pass12345")

    def test_public_pages_load(self):
        for name in ("home", "photographer_list", "contact", "login", "register"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertEqual(self.client.get(self.ph and reverse("photographer_detail", args=[self.ph.slug])).status_code, 200)

    def test_filter_by_city(self):
        resp = self.client.get(reverse("photographer_list"), {"city": self.city.id})
        self.assertContains(resp, "Test Snap")
        resp = self.client.get(reverse("photographer_list"), {"speciality": "portrait"})
        self.assertNotContains(resp, "Test Snap")

    def test_booking_requires_login(self):
        url = reverse("photographer_detail", args=[self.ph.slug])
        resp = self.client.post(url, {"event_date": (timezone.localdate() + timedelta(days=5)).isoformat()})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.startswith("/accounts/login/"))
        self.assertEqual(Booking.objects.count(), 0)

    def test_booking_payment_and_double_booking(self):
        self.client.login(username="tester", password="pass12345")
        date = (timezone.localdate() + timedelta(days=5)).isoformat()
        url = reverse("photographer_detail", args=[self.ph.slug])
        self.client.post(url, {"event_date": date, "tool": ""})
        booking = Booking.objects.get()
        self.assertEqual(booking.status, "pending")
        self.client.post(reverse("payment", args=[booking.pk]), {"payment_method": "upi"})
        booking.refresh_from_db()
        self.assertEqual(booking.status, "confirmed")
        # same date again must be rejected
        self.client.post(url, {"event_date": date, "tool": ""})
        self.assertEqual(Booking.objects.count(), 1)

    def test_past_date_rejected(self):
        self.client.login(username="tester", password="pass12345")
        url = reverse("photographer_detail", args=[self.ph.slug])
        self.client.post(url, {"event_date": "2020-01-01", "tool": ""})
        self.assertEqual(Booking.objects.count(), 0)

    def test_feedback_updates_rating(self):
        b = Booking.objects.create(user=self.user, photographer=self.ph, event_date=timezone.localdate(), status="completed")
        self.client.login(username="tester", password="pass12345")
        self.client.post(reverse("feedback_create", args=[b.pk]), {"rating": 4, "comment": "Nice work"})
        self.ph.refresh_from_db()
        self.assertEqual(self.ph.rating, 4.0)
        self.assertEqual(Feedback.objects.count(), 1)

    def test_cannot_pay_for_someone_elses_booking(self):
        other = User.objects.create_user("other", password="pass12345")
        b = Booking.objects.create(user=other, photographer=self.ph, event_date=timezone.localdate())
        self.client.login(username="tester", password="pass12345")
        self.assertEqual(self.client.get(reverse("payment", args=[b.pk])).status_code, 404)
