import re
import shutil
import tempfile
from datetime import date, timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from django.urls import reverse
from django.utils import timezone

from . import analytics, availability, booking_flow, emails, favourites, money, policy, reviews, throttle, tracking, travel
from .forms import RegisterForm
from .models import (Booking, City, Country, Feedback, BlockedDate, LoginAttempt, Package, Payment, Photo, Photographer, Portfolio,
                     Favourite, RescheduleRequest, ShootDay, ShootEvent, State, UserProfile)


def EVENT(**kw):
    """The venue fields every booking request must now carry."""
    return {"event_address": "Hall 1, Main Road, Alkapuri", "event_city": "Vadodara",
            "event_state": State.objects.order_by("id").first().pk, "event_pincode": "390001", **kw}


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
        resp = self.client.post(url, {**EVENT(), "event_date": (timezone.localdate() + timedelta(days=5)).isoformat()})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.startswith("/accounts/login/"))
        self.assertEqual(Booking.objects.count(), 0)

    def test_request_accept_pay_and_double_booking(self):
        self.client.login(username="tester", password="pass12345")
        date = (timezone.localdate() + timedelta(days=5)).isoformat()
        url = reverse("photographer_detail", args=[self.ph.slug])
        self.client.post(url, {**EVENT(), "event_date": date, "tool": ""})
        booking = Booking.objects.get()
        self.assertEqual(booking.status, "pending")
        # paying before the photographer accepts does nothing
        self.client.post(reverse("payment", args=[booking.pk]), {"payment_method": "upi"})
        booking.refresh_from_db()
        self.assertEqual(booking.status, "pending")
        self.assertEqual(booking.payments.count(), 0)
        # after acceptance the customer can pay
        from .booking_flow import accept
        accept(booking)
        self.client.post(reverse("payment", args=[booking.pk]), {"payment_method": "upi"})
        booking.refresh_from_db()
        self.assertEqual(booking.status, "confirmed")
        # the date is now taken
        other = User.objects.create_user("second", password="pass12345")
        self.client.logout()
        self.client.login(username="second", password="pass12345")
        self.client.post(url, {**EVENT(), "event_date": date, "tool": ""})
        self.assertEqual(Booking.objects.count(), 1)

    def test_past_date_rejected(self):
        self.client.login(username="tester", password="pass12345")
        url = reverse("photographer_detail", args=[self.ph.slug])
        self.client.post(url, {**EVENT(), "event_date": "2020-01-01", "tool": ""})
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


def make_image(name="t.jpg"):
    buf = BytesIO()
    Image.new("RGB", (40, 30), (120, 80, 200)).save(buf, "JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


TMP_MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class PhotographerAccountTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        country = Country.objects.create(name="India")
        self.state = State.objects.create(country=country, name="Gujarat")
        self.city = City.objects.create(state=self.state, name="Vadodara")
        self.customer = User.objects.create_user("cust", password="pass12345", first_name="Cust")
        UserProfile.objects.create(user=self.customer, role="customer")
        self.pro_user = User.objects.create_user("pro", password="pass12345", first_name="Pro")
        UserProfile.objects.create(user=self.pro_user, role="photographer")
        self.admin = User.objects.create_superuser("boss", "boss@example.com", "pass12345")

    def draft(self, **kw):
        return Photographer.objects.create(
            name="Draft Snap", bio="x" * 80, specialities="wedding", city=self.city,
            user=self.pro_user, status="draft", image=make_image("p.jpg"), **kw)

    def add_photos(self, ph, n=4):
        pf = Portfolio.objects.create(photographer=ph, title="Album")
        for i in range(n):
            Photo.objects.create(portfolio=pf, image=make_image(f"{i}.jpg"), order_id=i + 1)
        return pf

    # --- registration and roles ---
    def test_register_as_photographer(self):
        resp = self.client.post(reverse("register"), {
            "role": "photographer", "first_name": "Newbie", "username": "newbie",
            "email": "n@example.com", "password1": "Str0ng-pass-99", "password2": "Str0ng-pass-99"})
        self.assertRedirects(resp, reverse("pro_login"))
        self.assertEqual(User.objects.get(username="newbie").profile.role, "photographer")

    def test_register_defaults_to_customer_page(self):
        resp = self.client.post(reverse("register"), {
            "role": "customer", "first_name": "Buyer", "username": "buyer",
            "email": "b@example.com", "password1": "Str0ng-pass-99", "password2": "Str0ng-pass-99"})
        self.assertRedirects(resp, reverse("login"))
        self.assertEqual(User.objects.get(username="buyer").profile.role, "customer")

    def test_one_login_for_everyone_routes_by_role(self):
        for page in ("login", "pro_login"):
            r = self.client.post(reverse(page), {"username": "pro", "password": "pass12345"})
            self.assertRedirects(r, reverse("pro_dashboard"), fetch_redirect_response=False)
            self.client.logout()
            r = self.client.post(reverse(page), {"username": "cust", "password": "pass12345"})
            self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
            self.client.logout()

    def test_login_with_email_any_capitals_and_spaces(self):
        self.pro_user.email = "Pro.Person@Example.com"
        self.pro_user.save()
        for who in ("pro", "PRO", "Pro", "pro.person@example.com", "PRO.PERSON@EXAMPLE.COM", "  pro.person@example.com  "):
            r = self.client.post(reverse("login"), {"username": who, "password": "pass12345"})
            self.assertRedirects(r, reverse("pro_dashboard"), fetch_redirect_response=False, msg_prefix=f"login as {who!r}")
            self.client.logout()

    def test_wrong_password_unknown_user_and_inactive_all_fail_the_same_way(self):
        self.pro_user.email = "pro@example.com"
        self.pro_user.save()
        inactive = User.objects.create_user("sleeper", password="pass12345", email="sleep@example.com", is_active=False)
        for who, pw in (("pro", "wrong-pass"), ("pro@example.com", "wrong-pass"), ("nobody", "pass12345"), ("sleeper", "pass12345")):
            r = self.client.post(reverse("login"), {"username": who, "password": pw})
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "do not match")
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_next_page_is_honoured_and_logged_in_users_skip_the_form(self):
        r = self.client.post(reverse("login") + "?next=/contact/", {"username": "cust", "password": "pass12345"})
        self.assertRedirects(r, "/contact/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("login")), reverse("home"), fetch_redirect_response=False)
        self.client.logout()
        self.client.login(username="pro", password="pass12345")
        self.assertRedirects(self.client.get(reverse("login")), reverse("pro_dashboard"), fetch_redirect_response=False)

    def test_signup_rejects_a_reused_email_in_any_capitals(self):
        self.customer.email = "taken@example.com"
        self.customer.save()
        r = self.client.post(reverse("register"), {
            "role": "photographer", "first_name": "Copy", "username": "copycat", "email": "TAKEN@example.com",
            "password1": "Str0ng-pass-99", "password2": "Str0ng-pass-99"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "already exists")
        self.assertFalse(User.objects.filter(username="copycat").exists())

    def test_username_field_is_the_one_right_before_the_passwords(self):
        order = list(RegisterForm().fields)
        self.assertEqual(order[-3:], ["username", "password1", "password2"])

    def test_check_login_command_reports_role_and_status(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("check_login", "PRO", stdout=out)
        text = out.getvalue()
        self.assertIn("role:         photographer", text)
        self.assertIn("active:       True", text)

    def test_customer_cannot_open_photographer_area(self):
        self.client.login(username="cust", password="pass12345")
        self.assertEqual(self.client.get(reverse("pro_dashboard")).status_code, 302)
        self.assertEqual(self.client.get(reverse("pro_portfolio")).status_code, 302)

    def test_photographer_is_sent_away_from_customer_pages(self):
        self.client.login(username="pro", password="pass12345")
        self.assertRedirects(self.client.get(reverse("my_bookings")), reverse("pro_dashboard"), fetch_redirect_response=False)

    # --- profile creation and visibility ---
    def test_profile_creation_starts_as_draft_and_creates_city(self):
        self.client.login(username="pro", password="pass12345")
        resp = self.client.post(reverse("pro_profile"), {
            "name": "New Lens", "specialities": "portrait", "state": self.state.pk, "city_name": "Rajkot",
            "experience_years": 3, "price_per_day": 4000, "bio": "b" * 60, "is_available": "on"})
        self.assertRedirects(resp, reverse("pro_portfolio"))
        ph = Photographer.objects.get(user=self.pro_user)
        self.assertEqual(ph.status, "draft")
        self.assertEqual(ph.city.name, "Rajkot")

    def test_unapproved_profile_hidden_from_customers_but_not_owner(self):
        ph = self.draft()
        url = reverse("photographer_detail", args=[ph.slug])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotContains(self.client.get(reverse("photographer_list")), "Draft Snap")
        self.assertNotContains(self.client.get(reverse("home")), "Draft Snap")
        self.client.login(username="pro", password="pass12345")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.logout()
        self.client.login(username="boss", password="pass12345")
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_existing_style_profiles_stay_approved_and_visible(self):
        ph = Photographer.objects.create(name="Legacy Snap", bio="bio", specialities="event", city=self.city)
        self.assertEqual(ph.status, "approved")
        self.assertContains(self.client.get(reverse("photographer_list")), "Legacy Snap")

    # --- submitting for review ---
    def test_submit_needs_checklist_complete(self):
        ph = self.draft()
        self.client.login(username="pro", password="pass12345")
        self.client.post(reverse("pro_submit"))
        ph.refresh_from_db()
        self.assertEqual(ph.status, "draft")  # only 0 photos so far
        self.add_photos(ph, 4)
        self.client.post(reverse("pro_submit"))
        ph.refresh_from_db()
        self.assertEqual(ph.status, "pending")

    def test_photo_upload_and_ownership(self):
        ph = self.draft()
        pf = Portfolio.objects.create(photographer=ph, title="Album")
        self.client.login(username="pro", password="pass12345")
        resp = self.client.post(reverse("pro_portfolio"), {
            "action": "photo", "ph-portfolio": pf.pk, "ph-image": make_image("u.jpg"), "ph-caption": "hello"})
        self.assertEqual(resp.status_code, 302)
        photo = Photo.objects.get(portfolio=pf)
        self.assertEqual(photo.status, "published")
        # a non-image file is rejected
        bad = SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")
        self.client.post(reverse("pro_portfolio"), {"action": "photo", "ph-portfolio": pf.pk, "ph-image": bad})
        self.assertEqual(Photo.objects.filter(portfolio=pf).count(), 1)
        # another photographer cannot delete this photo
        other_user = User.objects.create_user("pro2", password="pass12345")
        UserProfile.objects.create(user=other_user, role="photographer")
        self.client.logout()
        self.client.login(username="pro2", password="pass12345")
        self.assertEqual(self.client.post(reverse("pro_photo_delete", args=[photo.pk])).status_code, 404)

    # --- admin approval ---
    def test_admin_approve_makes_profile_public(self):
        ph = self.draft()
        ph.status = "pending"; ph.save()
        self.client.login(username="boss", password="pass12345")
        self.client.post(reverse("admin:photopholio_photographer_changelist"),
                         {"action": "approve_selected", "_selected_action": [ph.pk]})
        ph.refresh_from_db()
        self.assertEqual(ph.status, "approved")
        self.assertEqual(ph.reviewed_by, self.admin)
        self.client.logout()
        self.assertContains(self.client.get(reverse("photographer_list")), "Draft Snap")

    def test_admin_reject_needs_a_reason(self):
        ph = self.draft()
        ph.status = "pending"; ph.save()
        self.client.login(username="boss", password="pass12345")
        url = reverse("admin:photopholio_photographer_changelist")
        resp = self.client.post(url, {"action": "reject_selected", "_selected_action": [ph.pk]})
        self.assertContains(resp, "Reason shown to the photographer")
        self.client.post(url, {"action": "reject_selected", "_selected_action": [ph.pk], "apply": "1", "reason": ""})
        ph.refresh_from_db()
        self.assertEqual(ph.status, "pending")
        self.client.post(url, {"action": "reject_selected", "_selected_action": [ph.pk], "apply": "1",
                               "reason": "Please add more photos."})
        ph.refresh_from_db()
        self.assertEqual((ph.status, ph.rejection_reason), ("rejected", "Please add more photos."))
        self.client.logout()
        self.client.login(username="pro", password="pass12345")
        self.assertContains(self.client.get(reverse("pro_dashboard")), "Please add more photos.")

    # --- photographers cannot book ---
    def test_photographer_account_cannot_book(self):
        target = Photographer.objects.create(name="Bookable", bio="bio", specialities="event", city=self.city)
        self.client.login(username="pro", password="pass12345")
        date = (timezone.localdate() + timedelta(days=9)).isoformat()
        self.client.post(reverse("photographer_detail", args=[target.slug]), {**EVENT(), "event_date": date, "tool": ""})
        self.assertEqual(Booking.objects.count(), 0)


class BookingFlowTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name="India")
        state = State.objects.create(country=country, name="Gujarat")
        city = City.objects.create(state=state, name="Vadodara")
        self.pro_user = User.objects.create_user("pro", password="pass12345", first_name="Pro")
        UserProfile.objects.create(user=self.pro_user, role="photographer")
        self.ph = Photographer.objects.create(name="Flow Snap", bio="bio", specialities="wedding", city=city,
                                              price_per_day=8000, user=self.pro_user)
        self.cust = User.objects.create_user("cust", password="pass12345", first_name="Cust")
        self.cust2 = User.objects.create_user("cust2", password="pass12345", first_name="Other")
        self.admin = User.objects.create_superuser("boss", "b@example.com", "pass12345")
        self.today = timezone.localdate()

    def booking(self, days=10, status="pending", user=None, paid=False):
        b = Booking.objects.create(user=user or self.cust, photographer=self.ph,
                                   event_date=self.today + timedelta(days=days), status=status)
        if paid:
            Payment.objects.create(user=b.user, booking=b, amount=8000, payment_method="upi", status="completed")
        return b

    # ---- policy numbers ----
    def test_refund_percent_tiers(self):
        d = lambda n: self.today + timedelta(days=n)
        self.assertEqual([policy.refund_percent(d(n), self.today) for n in (5, 2, 1, 0, -1)], [100, 100, 50, 0, 0])
        self.assertEqual(policy.refund_amount(8000, d(1), self.today), 4000)
        self.assertEqual([p for _, p in policy.rows()], [100, 50, 0])

    def test_policy_page_loads(self):
        self.assertContains(self.client.get(reverse("cancellation_policy")), "100%")

    # ---- accept / decline ----
    def test_accept_sets_deadline_and_declines_rivals(self):
        b1 = self.booking(10)
        b2 = self.booking(10, user=self.cust2)
        other_day = self.booking(11, user=self.cust2)
        self.client.login(username="pro", password="pass12345")
        self.client.post(reverse("pro_booking_action", args=[b1.pk, "accept"]))
        for obj in (b1, b2, other_day):
            obj.refresh_from_db()
        self.assertEqual(b1.status, "accepted")
        self.assertIsNotNone(b1.payment_deadline)
        self.assertEqual((b2.status, b2.cancelled_by), ("declined", "system"))
        self.assertEqual(other_day.status, "pending")  # a different date is untouched

    def test_cannot_accept_a_date_already_taken(self):
        self.booking(10, status="confirmed", user=self.cust2, paid=True)
        b = self.booking(10)
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.accept(b)

    def test_decline(self):
        b = self.booking()
        self.client.login(username="pro", password="pass12345")
        self.client.post(reverse("pro_booking_action", args=[b.pk, "decline"]))
        b.refresh_from_db()
        self.assertEqual((b.status, b.cancelled_by), ("declined", "photographer"))

    def test_only_the_owner_can_act_on_a_booking(self):
        b = self.booking()
        stranger = User.objects.create_user("stranger", password="pass12345")
        UserProfile.objects.create(user=stranger, role="photographer")
        self.client.login(username="stranger", password="pass12345")
        self.assertEqual(self.client.post(reverse("pro_booking_action", args=[b.pk, "accept"])).status_code, 404)

    # ---- payment window ----
    def test_unpaid_acceptance_lapses_after_24_hours(self):
        b = self.booking(status="accepted")
        Booking.objects.filter(pk=b.pk).update(accepted_at=timezone.now() - timedelta(hours=25))
        booking_flow.expire_stale()
        b.refresh_from_db()
        self.assertEqual((b.status, b.cancelled_by), ("cancelled", "system"))
        self.client.login(username="cust", password="pass12345")
        self.client.post(reverse("payment", args=[b.pk]), {"payment_method": "upi"})
        self.assertEqual(b.payments.count(), 0)

    def test_fresh_acceptance_does_not_lapse(self):
        b = self.booking(status="accepted")
        Booking.objects.filter(pk=b.pk).update(accepted_at=timezone.now() - timedelta(hours=2))
        booking_flow.expire_stale()
        b.refresh_from_db()
        self.assertEqual(b.status, "accepted")

    # ---- customer cancellation and refunds ----
    def test_cancel_before_payment_has_no_refund(self):
        b = self.booking()
        self.client.login(username="cust", password="pass12345")
        self.assertEqual(self.client.get(reverse("booking_cancel", args=[b.pk])).status_code, 200)
        self.client.post(reverse("booking_cancel", args=[b.pk]))
        b.refresh_from_db()
        self.assertEqual((b.status, b.cancelled_by), ("cancelled", "customer"))

    def test_get_request_never_cancels(self):
        b = self.booking()
        self.client.login(username="cust", password="pass12345")
        self.client.get(reverse("booking_cancel", args=[b.pk]))
        b.refresh_from_db()
        self.assertEqual(b.status, "pending")

    def test_customer_refund_follows_policy(self):
        for days, percent, status in ((5, 100, "pending"), (1, 50, "pending"), (0, 0, "none")):
            b = self.booking(days, status="confirmed", paid=True)
            booking_flow.cancel_by_customer(b, today=self.today)
            pay = b.paid_payment
            self.assertEqual(pay.refund_amount, 8000 * percent // 100, f"{days} days before")
            self.assertEqual(pay.refund_status, status)
            b.refresh_from_db()
            self.assertEqual(b.status, "cancelled")

    def test_cannot_cancel_someone_elses_booking(self):
        b = self.booking(user=self.cust2)
        self.client.login(username="cust", password="pass12345")
        self.assertEqual(self.client.post(reverse("booking_cancel", args=[b.pk])).status_code, 404)

    # ---- photographer cancellation ----
    def test_photographer_cancel_needs_reason_and_refunds_in_full(self):
        b = self.booking(0 + 1, status="confirmed", paid=True)  # even 1 day before: still 100%
        self.client.login(username="pro", password="pass12345")
        url = reverse("pro_booking_cancel", args=[b.pk])
        self.client.post(url, {"reason": ""})
        b.refresh_from_db()
        self.assertEqual(b.status, "confirmed")
        self.client.post(url, {"reason": "I am unwell"})
        b.refresh_from_db()
        self.assertEqual((b.status, b.cancelled_by, b.cancel_reason), ("cancelled", "photographer", "I am unwell"))
        pay = b.paid_payment
        self.assertEqual((pay.refund_amount, pay.refund_status), (8000, "pending"))
        self.assertEqual(self.ph.cancellations_count(), 1)

    def test_photographer_cannot_cancel_a_pending_request(self):
        b = self.booking()
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.cancel_by_photographer(b, "reason")

    # ---- completing ----
    def test_complete_only_on_or_after_event_date(self):
        future = self.booking(5, status="confirmed", paid=True)
        past = self.booking(-1, status="confirmed", paid=True)
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.mark_completed(future)
        booking_flow.mark_completed(past)
        past.refresh_from_db()
        self.assertEqual(past.status, "completed")

    # ---- admin: refunds and disputes ----
    def test_admin_marks_refund_processed(self):
        b = self.booking(5, status="confirmed", paid=True)
        booking_flow.cancel_by_customer(b, today=self.today)
        pay = b.paid_payment
        self.client.login(username="boss", password="pass12345")
        self.client.post(reverse("admin:photopholio_payment_changelist"),
                         {"action": "mark_refund_processed", "_selected_action": [pay.pk]})
        pay.refresh_from_db()
        self.assertEqual(pay.refund_status, "refunded")
        self.assertIsNotNone(pay.refunded_at)

    def test_admin_full_refund_for_no_show(self):
        b = self.booking(-2, status="confirmed", paid=True)
        self.client.login(username="boss", password="pass12345")
        self.client.post(reverse("admin:photopholio_booking_changelist"),
                         {"action": "refund_in_full", "_selected_action": [b.pk]})
        b.refresh_from_db()
        self.assertEqual((b.status, b.cancelled_by), ("cancelled", "admin"))
        self.assertEqual((b.paid_payment.refund_amount, b.paid_payment.refund_status), (8000, "pending"))

    # ---- what people see ----
    def test_pages_show_status_and_refund(self):
        b = self.booking(5, status="confirmed", paid=True)
        booking_flow.cancel_by_customer(b, today=self.today)
        self.client.login(username="cust", password="pass12345")
        page = self.client.get(reverse("my_bookings"))
        self.assertContains(page, "Refund of")
        self.assertContains(page, "Refund pending")
        self.client.logout()
        self.client.login(username="pro", password="pass12345")
        self.assertEqual(self.client.get(reverse("pro_dashboard")).status_code, 200)

    def test_same_customer_cannot_request_same_date_twice(self):
        self.booking(10)
        self.client.login(username="cust", password="pass12345")
        date = (self.today + timedelta(days=10)).isoformat()
        self.client.post(reverse("photographer_detail", args=[self.ph.slug]), {**EVENT(), "event_date": date, "tool": ""})
        self.assertEqual(Booking.objects.filter(user=self.cust).count(), 1)


class EmailNotificationTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name="India")
        state = State.objects.create(country=country, name="Gujarat")
        city = City.objects.create(state=state, name="Vadodara")
        self.pro_user = User.objects.create_user("pro", password="pass12345", first_name="Pro", email="pro@example.com")
        UserProfile.objects.create(user=self.pro_user, role="photographer")
        self.ph = Photographer.objects.create(name="Mail Snap", bio="bio", specialities="wedding", city=city,
                                              price_per_day=8000, user=self.pro_user)
        self.cust = User.objects.create_user("cust", password="pass12345", first_name="Cust", email="cust@example.com")
        self.cust2 = User.objects.create_user("cust2", password="pass12345", first_name="Other", email="cust2@example.com")
        self.admin = User.objects.create_superuser("boss", "boss@example.com", "pass12345")
        self.today = timezone.localdate()

    def booking(self, days=10, status="pending", user=None, paid=False):
        b = Booking.objects.create(user=user or self.cust, photographer=self.ph,
                                   event_date=self.today + timedelta(days=days), status=status)
        if status == "accepted":
            Booking.objects.filter(pk=b.pk).update(accepted_at=timezone.now())
            b.refresh_from_db()
        if paid:
            Payment.objects.create(user=b.user, booking=b, amount=8000, payment_method="upi", status="completed")
        return b

    def to(self):
        return sorted(m.to[0] for m in mail.outbox)

    def test_request_emails_customer_and_photographer(self):
        self.client.login(username="cust", password="pass12345")
        date = (self.today + timedelta(days=12)).isoformat()
        self.client.post(reverse("photographer_detail", args=[self.ph.slug]), {**EVENT(), "event_date": date, "tool": "Drone"})
        self.assertEqual(self.to(), ["cust@example.com", "pro@example.com"])
        pro_mail = next(m for m in mail.outbox if m.to == ["pro@example.com"])
        self.assertIn("Drone", pro_mail.body)
        self.assertIn("/photographer/dashboard/", pro_mail.body)

    def test_email_has_text_and_html_versions_with_working_link(self):
        b = self.booking()
        booking_flow.accept(b)
        m = next(x for x in mail.outbox if x.to == ["cust@example.com"])
        self.assertIn(f"Pay now: http://127.0.0.1:8000/bookings/{b.pk}/pay/", m.body)
        self.assertIn("24 hours", m.body)
        self.assertEqual(m.alternatives[0][1], "text/html")
        self.assertIn(f"/bookings/{b.pk}/pay/", m.alternatives[0][0])

    def test_accepting_declines_rivals_and_tells_them(self):
        b1 = self.booking(10)
        self.booking(10, user=self.cust2)
        booking_flow.accept(b1)
        self.assertEqual(self.to(), ["cust2@example.com", "cust@example.com"][::-1] if False else sorted(["cust@example.com", "cust2@example.com"]))
        rival_mail = next(m for m in mail.outbox if m.to == ["cust2@example.com"])
        self.assertIn("not able to take", rival_mail.body)

    def test_decline_emails_customer(self):
        booking_flow.decline(self.booking())
        self.assertEqual(self.to(), ["cust@example.com"])

    def test_payment_emails_receipt_and_tells_photographer(self):
        b = self.booking(status="accepted")
        self.client.login(username="cust", password="pass12345")
        self.client.post(reverse("payment", args=[b.pk]), {"payment_method": "upi"})
        self.assertEqual(self.to(), ["cust@example.com", "pro@example.com"])
        self.assertIn("Rs.8000", next(m for m in mail.outbox if m.to == ["cust@example.com"]).body)

    def test_customer_cancel_mentions_refund_or_no_refund(self):
        early = self.booking(10, status="confirmed", paid=True)
        booking_flow.cancel_by_customer(early)
        body = next(m for m in mail.outbox if m.to == ["cust@example.com"]).body
        self.assertIn("refund of Rs.8000 is pending", body)
        self.assertIn("date is free again", next(m for m in mail.outbox if m.to == ["pro@example.com"]).body)
        mail.outbox.clear()
        late = self.booking(0, status="confirmed", paid=True)
        booking_flow.cancel_by_customer(late)
        self.assertIn("does not get a refund", next(m for m in mail.outbox if m.to == ["cust@example.com"]).body)

    def test_photographer_cancel_email_has_reason_and_full_refund(self):
        b = self.booking(2, status="confirmed", paid=True)
        booking_flow.cancel_by_photographer(b, "Equipment failure")
        self.assertEqual(self.to(), ["cust@example.com"])
        body = mail.outbox[0].body
        self.assertIn("Equipment failure", body)
        self.assertIn("refund of Rs.8000", body)

    def test_lapsed_request_emails_the_customer(self):
        b = self.booking(status="accepted")
        Booking.objects.filter(pk=b.pk).update(accepted_at=timezone.now() - timedelta(hours=30))
        self.assertEqual(booking_flow.expire_stale(), 1)
        self.assertIn("lapsed", mail.outbox[0].body)
        self.assertEqual(booking_flow.expire_stale(), 0)  # not emailed twice

    def test_refund_processed_email_from_admin_action(self):
        b = self.booking(10, status="confirmed", paid=True)
        booking_flow.cancel_by_customer(b)
        mail.outbox.clear()
        pay = b.paid_payment
        self.client.login(username="boss", password="pass12345")
        self.client.post(reverse("admin:photopholio_payment_changelist"),
                         {"action": "mark_refund_processed", "_selected_action": [pay.pk]})
        pay.refresh_from_db()
        self.assertEqual(pay.refund_status, "refunded")
        self.assertEqual(self.to(), ["cust@example.com"])
        self.assertIn("processed your refund of Rs.8000", mail.outbox[0].body)
        mail.outbox.clear()
        self.client.post(reverse("admin:photopholio_payment_changelist"),
                         {"action": "mark_refund_processed", "_selected_action": [pay.pk]})
        self.assertEqual(len(mail.outbox), 0)  # already processed: no second email

    def test_completed_email_links_to_review(self):
        b = self.booking(-1, status="confirmed", paid=True)
        booking_flow.mark_completed(b)
        self.assertIn(f"/bookings/{b.pk}/review/", mail.outbox[0].body)

    def test_profile_approval_and_rejection_emails(self):
        self.ph.status = "pending"
        self.ph.save()
        self.client.login(username="boss", password="pass12345")
        url = reverse("admin:photopholio_photographer_changelist")
        self.client.post(url, {"action": "approve_selected", "_selected_action": [self.ph.pk]})
        self.assertEqual(self.to(), ["pro@example.com"])
        self.assertIn("approved", mail.outbox[0].body)
        mail.outbox.clear()
        self.client.post(url, {"action": "approve_selected", "_selected_action": [self.ph.pk]})
        self.assertEqual(len(mail.outbox), 0)  # already approved: nothing to announce
        self.client.post(url, {"action": "reject_selected", "_selected_action": [self.ph.pk], "apply": "1",
                               "reason": "Add four more photos."})
        self.assertIn("Add four more photos.", mail.outbox[0].body)

    def test_missing_email_address_is_skipped_quietly(self):
        self.cust.email = ""
        self.cust.save()
        booking_flow.decline(self.booking())
        self.assertEqual(len(mail.outbox), 0)

    def test_broken_mail_server_never_breaks_a_booking(self):
        self.client.login(username="cust", password="pass12345")
        date = (self.today + timedelta(days=14)).isoformat()
        with patch("photopholio.emails.send_mail", side_effect=OSError("mail server down")):
            with self.assertLogs("photopholio.emails", level="ERROR"):
                resp = self.client.post(reverse("photographer_detail", args=[self.ph.slug]), {**EVENT(), "event_date": date, "tool": ""})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Booking.objects.filter(user=self.cust).count(), 1)


class ForgotPasswordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("pro", password="old-pass-123", email="pro@example.com", first_name="Pro")
        UserProfile.objects.create(user=self.user, role="photographer")

    def reset_link(self):
        return re.search(r"http://testserver(/accounts/reset/[^\s]+)", mail.outbox[0].body).group(1)

    def test_whole_reset_journey(self):
        self.assertEqual(self.client.get(reverse("password_reset")).status_code, 200)
        r = self.client.post(reverse("password_reset"), {"email": "PRO@example.com"})
        self.assertRedirects(r, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["pro@example.com"])
        r = self.client.get(self.reset_link(), follow=True)
        self.assertContains(r, "Choose a new password")
        r = self.client.post(r.redirect_chain[-1][0], {"new_password1": "Br4nd-new-pass", "new_password2": "Br4nd-new-pass"})
        self.assertRedirects(r, reverse("password_reset_complete"))
        self.assertFalse(self.client.login(username="pro", password="old-pass-123"))
        r = self.client.post(reverse("login"), {"username": "pro@example.com", "password": "Br4nd-new-pass"})
        self.assertRedirects(r, reverse("pro_dashboard"), fetch_redirect_response=False)

    def test_unknown_and_inactive_emails_get_the_same_page_but_no_mail(self):
        User.objects.create_user("sleeper", password="x-pass-12345", email="sleep@example.com", is_active=False)
        for address in ("nobody@example.com", "sleep@example.com"):
            r = self.client.post(reverse("password_reset"), {"email": address})
            self.assertRedirects(r, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_link_works_only_once_and_bad_links_say_expired(self):
        self.client.post(reverse("password_reset"), {"email": "pro@example.com"})
        link = self.reset_link()
        r = self.client.get(link, follow=True)
        self.client.post(r.redirect_chain[-1][0], {"new_password1": "Br4nd-new-pass", "new_password2": "Br4nd-new-pass"})
        self.assertContains(self.client.get(link, follow=True), "This link has expired")
        self.assertContains(self.client.get("/accounts/reset/abc/xyz-123/"), "This link has expired")

    def test_login_page_links_to_forgot_password(self):
        self.assertContains(self.client.get(reverse("login")), reverse("password_reset"))


class AnalyticsTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name="India")
        state = State.objects.create(country=country, name="Gujarat")
        city = City.objects.create(state=state, name="Vadodara")
        self.ph = Photographer.objects.create(name="Stat Snap", bio="bio", specialities="wedding", city=city, price_per_day=8000)
        self.cust = User.objects.create_user("cust", password="pass12345")
        self.admin = User.objects.create_superuser("boss", "b@example.com", "pass12345")
        d = timezone.localdate()
        now = timezone.now()
        # paid, then cancelled by the customer: Rs.4000 refund pending
        self.b1 = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=d + timedelta(days=9),
                                         status="cancelled", cancelled_by="customer", accepted_at=now)
        self.p1 = Payment.objects.create(user=self.cust, booking=self.b1, amount=8000, payment_method="upi", status="completed",
                                         refund_amount=4000, refund_status="pending")
        # paid and confirmed
        self.b2 = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=d + timedelta(days=20),
                                         status="confirmed", accepted_at=now)
        Payment.objects.create(user=self.cust, booking=self.b2, amount=8000, payment_method="upi", status="completed")
        # declined by the photographer, and one still waiting
        Booking.objects.create(user=self.cust, photographer=self.ph, event_date=d + timedelta(days=30),
                               status="declined", cancelled_by="photographer")
        Booking.objects.create(user=self.cust, photographer=self.ph, event_date=d + timedelta(days=40), status="pending")
        Photographer.objects.create(name="Waiting Snap", bio="b", specialities="event", city=city, status="pending")

    def test_numbers(self):
        data = analytics.dashboard_data()
        k = data["kpis"]
        self.assertEqual((k["gross"], k["refunds"], k["net"]), (16000, 4000, 12000))
        self.assertEqual(k["total_bookings"], 4)
        self.assertEqual(k["avg_value"], 8000)
        self.assertEqual((k["pending_refunds"], k["pending_refund_amount"]), (1, 4000))
        self.assertEqual(k["pending_approvals"], 1)
        self.assertEqual(k["live_photographers"], 1)
        self.assertEqual(k["customers"], 1)
        self.assertEqual(k["acceptance_rate"], 67)   # 2 accepted, 1 declined by the photographer
        self.assertEqual(k["payment_rate"], 100)
        self.assertEqual(k["cancellation_rate"], 25)

    def test_chart_series_cover_six_months_and_fill_gaps(self):
        chart = analytics.dashboard_data()["chart"]
        self.assertEqual(len(chart["months"]), 6)
        self.assertEqual(chart["requests"][:-1], [0] * 5)
        self.assertEqual(chart["requests"][-1], 4)
        self.assertEqual((chart["gross"][-1], chart["refunds"][-1]), (16000, 4000))
        self.assertEqual(chart["top_names"], ["Stat Snap"])
        self.assertEqual(sum(chart["status_values"]), 4)

    def test_months_roll_over_the_year_end(self):
        data = analytics.dashboard_data(today=timezone.localdate().replace(year=2026, month=2, day=10))
        self.assertEqual(data["chart"]["months"], ["Sep 2025", "Oct 2025", "Nov 2025", "Dec 2025", "Jan 2026", "Feb 2026"])

    def test_empty_database_does_not_divide_by_zero(self):
        Payment.objects.all().delete()
        Booking.objects.all().delete()
        k = analytics.dashboard_data()["kpis"]
        self.assertEqual((k["gross"], k["avg_value"], k["acceptance_rate"], k["cancellation_rate"]), (0, 0, None, None))

    def test_admin_page(self):
        self.client.login(username="boss", password="pass12345")
        r = self.client.get(reverse("admin:photopholio_analytics"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Net revenue")
        self.assertContains(r, "12000")
        self.assertContains(r, "Waiting for your approval (1)")
        self.assertContains(r, "chart-data")
        self.assertContains(self.client.get(reverse("admin:index")), "Analytics")

    def test_only_allowed_staff_can_open_it(self):
        self.assertEqual(self.client.get(reverse("admin:photopholio_analytics")).status_code, 302)  # to admin login
        User.objects.create_user("clerk", password="pass12345", is_staff=True)
        self.client.login(username="clerk", password="pass12345")
        self.assertEqual(self.client.get(reverse("admin:photopholio_analytics")).status_code, 403)


class CommandTests(TestCase):
    def test_expire_bookings_command(self):
        from io import StringIO
        from django.core.management import call_command
        country = Country.objects.create(name="India")
        city = City.objects.create(state=State.objects.create(country=country, name="Gujarat"), name="Vadodara")
        ph = Photographer.objects.create(name="Cmd Snap", bio="b", specialities="event", city=city)
        u = User.objects.create_user("c", password="x-pass-12345", email="c@example.com")
        b = Booking.objects.create(user=u, photographer=ph, event_date=timezone.localdate() + timedelta(days=5), status="accepted",
                                   accepted_at=timezone.now() - timedelta(hours=40))
        out = StringIO()
        call_command("expire_bookings", stdout=out)
        self.assertIn("1 booking(s) lapsed", out.getvalue())
        b.refresh_from_db()
        self.assertEqual(b.status, "cancelled")

    def test_send_test_email_command(self):
        from io import StringIO
        from django.core.management import call_command
        call_command("send_test_email", "me@example.com", stdout=StringIO())
        self.assertEqual(mail.outbox[0].to, ["me@example.com"])


class LoginLockoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("lockme", password="right-pass-123", email="lock@example.com")
        self.other = User.objects.create_user("bystander", password="right-pass-123", email="by@example.com")

    def attempt(self, who="lockme", pw="wrong-pass", url=None, **extra):
        return self.client.post(url or reverse("login"), {"username": who, "password": pw}, **extra)

    def fail(self, n, **kw):
        for _ in range(n):
            self.attempt(**kw)

    def test_fifth_wrong_password_locks_even_the_right_password(self):
        for _ in range(4):
            self.assertContains(self.attempt(), "do not match")          # ordinary failures
        self.assertContains(self.attempt(), "Too many wrong attempts")    # the 5th one locks
        r = self.attempt(pw="right-pass-123")
        self.assertContains(r, "Too many wrong attempts")
        self.assertContains(r, "5 more minutes")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_correct_login_resets_the_count(self):
        self.fail(4)
        self.assertEqual(self.attempt(pw="right-pass-123").status_code, 302)
        self.client.logout()
        self.fail(4)
        self.assertEqual(self.attempt(pw="right-pass-123").status_code, 302)   # locked if the count had not reset

    def test_username_and_email_count_as_one_account(self):
        self.fail(3, who="lockme")
        self.fail(2, who="LOCK@example.com")
        self.assertContains(self.attempt(who="lockme", pw="right-pass-123"), "Too many wrong attempts")

    def test_other_accounts_are_not_affected(self):
        self.fail(6)
        self.assertEqual(self.attempt(who="bystander", pw="right-pass-123").status_code, 302)

    def test_lock_ends_after_fifteen_minutes(self):
        self.fail(5)
        LoginAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(self.attempt(pw="right-pass-123").status_code, 302)

    def test_time_left_counts_down(self):
        self.fail(5)
        LoginAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=3))
        self.assertContains(self.attempt(pw="right-pass-123"), "2 more minutes")

    def test_unknown_names_are_locked_the_same_way(self):
        self.fail(5, who="ghost")
        self.assertContains(self.attempt(who="ghost"), "Too many wrong attempts")

    def test_attempts_made_while_locked_are_not_counted(self):
        self.fail(9)
        self.assertEqual(LoginAttempt.objects.filter(key=throttle.key_for("lockme")).count(), 5)

    def test_forgot_password_still_works_while_locked(self):
        self.fail(5)
        r = self.client.post(reverse("password_reset"), {"email": "lock@example.com"})
        self.assertRedirects(r, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)

    def test_the_visitor_ip_is_stored_for_the_admin_but_never_decides_a_lock(self):
        self.fail(1, HTTP_X_FORWARDED_FOR="203.0.113.5, 10.0.0.1")
        self.assertEqual(LoginAttempt.objects.get().ip_address, "203.0.113.5")
        self.fail(1, HTTP_X_FORWARDED_FOR="not-an-ip")
        self.assertIsNone(LoginAttempt.objects.first().ip_address)
        self.client.logout()
        # a different visitor address does not get around the lock on the account
        self.fail(3)
        self.assertContains(self.attempt(pw="right-pass-123", HTTP_X_FORWARDED_FOR="198.51.100.7"), "Too many wrong attempts")

    def test_admin_login_is_locked_too(self):
        User.objects.create_superuser("boss", "boss@example.com", "admin-pass-123")
        url = "/admin/login/?next=/admin/"
        for _ in range(5):
            self.client.post(url, {"username": "boss", "password": "nope", "next": "/admin/"})
        r = self.client.post(url, {"username": "boss", "password": "admin-pass-123", "next": "/admin/"})
        self.assertContains(r, "Too many wrong attempts")

    def test_a_request_without_a_password_does_not_reset_the_count(self):
        User.objects.create_superuser("boss", "boss@example.com", "admin-pass-123")
        url = "/admin/login/?next=/admin/"
        for _ in range(4):
            self.client.post(url, {"username": "boss", "password": "nope", "next": "/admin/"})
        self.client.post(url, {"username": "boss", "password": "", "next": "/admin/"})
        self.assertEqual(LoginAttempt.objects.filter(key=throttle.key_for("boss")).count(), 4)

    def test_admin_can_see_and_unlock(self):
        self.fail(5)
        User.objects.create_superuser("chief", "chief@example.com", "admin-pass-123")
        self.client.login(username="chief", password="admin-pass-123")
        url = reverse("admin:photopholio_loginattempt_changelist")
        self.assertContains(self.client.get(url), "lockme")
        self.client.post(url, {"action": "unlock", "_selected_action": [LoginAttempt.objects.first().pk]})
        self.assertEqual(LoginAttempt.objects.count(), 0)
        self.client.logout()
        self.assertEqual(self.attempt(pw="right-pass-123").status_code, 302)


class LockCountdownTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("lockme", password="right-pass-123", email="lock@example.com")

    def attempt(self, pw="wrong-pass", who="lockme"):
        return self.client.post(reverse("login"), {"username": who, "password": pw})

    def seconds_on_page(self, response):
        return int(re.search(r'id="lock-box"[^>]*data-seconds="(\d+)"', response.content.decode()).group(1))

    def test_lock_lasts_five_minutes(self):
        self.assertEqual(throttle.LOCK_MINUTES, 5)
        for _ in range(5):
            r = self.attempt()
        self.assertEqual(self.seconds_on_page(r), 300)

    def test_locked_page_has_the_countdown_and_the_script(self):
        for _ in range(5):
            r = self.attempt()
        self.assertContains(r, 'id="lock-box"')
        self.assertContains(r, 'id="lock-time"')
        self.assertContains(r, "You can log in now")        # the text the script swaps in at zero
        self.assertContains(r, "5 more minutes")             # plain text for anyone without JavaScript
        self.assertNotContains(r, "do not match")

    def test_seconds_on_the_page_count_down_with_time(self):
        for _ in range(5):
            self.attempt()
        LoginAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=3))
        seconds = self.seconds_on_page(self.attempt(pw="right-pass-123"))
        self.assertTrue(115 <= seconds <= 120, seconds)

    def test_ordinary_failures_have_no_countdown(self):
        r = self.attempt()
        self.assertContains(r, "do not match")
        self.assertNotContains(r, 'id="lock-box"')
        self.assertNotContains(r, "setTimeout")

    def test_plain_get_of_the_login_page_has_no_countdown(self):
        self.assertNotContains(self.client.get(reverse("login")), 'id="lock-box"')

    def test_seconds_left_helper(self):
        key = throttle.key_for("lockme")
        self.assertEqual(throttle.seconds_left(key), 0)
        for _ in range(5):
            throttle.record_failure(key)
        self.assertEqual(throttle.seconds_left(key), 300)
        self.assertEqual(throttle.minutes_left(key), 5)
        LoginAttempt.objects.update(created_at=timezone.now() - timedelta(seconds=250))
        self.assertTrue(0 < throttle.seconds_left(key) <= 50)
        self.assertEqual(throttle.minutes_left(key), 1)
        LoginAttempt.objects.update(created_at=timezone.now() - timedelta(seconds=301))
        self.assertEqual((throttle.seconds_left(key), throttle.minutes_left(key)), (0, 0))

    def test_after_the_wait_a_new_login_works_without_anything_else(self):
        for _ in range(5):
            self.attempt()
        LoginAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=5, seconds=1))
        self.assertEqual(self.attempt(pw="right-pass-123").status_code, 302)


class TwoTimingsTests(TestCase):
    """Window 15 minutes (how far back wrong passwords are counted), lock 5 minutes (how long a lock lasts)."""

    def setUp(self):
        self.user = User.objects.create_user("lockme", password="right-pass-123", email="lock@example.com")
        self.key = throttle.key_for("lockme")

    def past(self, *minutes_ago):
        """Wrong passwords made that many minutes ago."""
        for m in minutes_ago:
            row = LoginAttempt.objects.create(key=self.key)
            LoginAttempt.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(minutes=m))

    def wrong(self):
        return self.client.post(reverse("login"), {"username": "lockme", "password": "wrong-pass"})

    def locked(self, response):
        return "Too many wrong attempts" in response.content.decode()

    def test_the_numbers(self):
        s = throttle.SITE
        self.assertEqual((s.max_attempts, s.window_minutes, s.lock_minutes), (5, 15, 5))

    def test_five_wrong_passwords_spread_over_fourteen_minutes_still_lock_for_five(self):
        self.past(14, 11, 8, 5)
        r = self.wrong()                                   # the 5th, all inside the 15 minute window
        self.assertTrue(self.locked(r))
        self.assertContains(r, "5 more minutes")           # lock length is 5, not 15

    def test_wrong_passwords_older_than_the_window_do_not_count(self):
        self.past(20, 19, 18, 17)                          # four old wrong passwords, outside the 15 minute window
        for _ in range(4):
            self.assertFalse(self.locked(self.wrong()))    # only the new ones are counted
        self.assertTrue(self.locked(self.wrong()))         # the 5th new one locks

    def test_a_guess_every_three_minutes_is_caught(self):
        self.past(12, 9, 6, 3)
        self.assertTrue(self.locked(self.wrong()))

    def test_a_guess_every_four_minutes_or_slower_is_never_locked(self):
        self.past(16, 12, 8, 4)                            # a guess every 4 minutes: at most 4 inside any 15 minutes
        self.assertFalse(self.locked(self.wrong()))
        LoginAttempt.objects.all().delete()
        self.past(24, 18, 12, 6)                           # a guess every 6 minutes
        self.assertFalse(self.locked(self.wrong()))

    def test_after_a_lock_ends_the_count_starts_again_from_zero(self):
        self.past(5.5, 5.4, 5.3, 5.2, 5.1)                 # a lock that ended ~10 seconds ago, still inside the window
        r = self.wrong()
        self.assertFalse(self.locked(r))                   # first mistake after the lock: an ordinary failure
        for _ in range(3):
            self.assertFalse(self.locked(self.wrong()))
        self.assertTrue(self.locked(self.wrong()))         # it takes 5 new ones to lock again

    def test_a_lock_that_has_not_run_out_stays(self):
        self.past(4, 3.9, 3.8, 3.7, 3.6)
        self.assertEqual(throttle.minutes_left(self.key), 2)
        self.assertTrue(self.locked(self.wrong()))

    def test_attempts_during_a_lock_do_not_extend_it(self):
        self.past(4, 3.9, 3.8, 3.7, 3.6)
        before = throttle.seconds_left(self.key)
        for _ in range(3):
            self.wrong()
        self.assertEqual(LoginAttempt.objects.filter(key=self.key).count(), 5)
        self.assertLessEqual(throttle.seconds_left(self.key), before)

    def test_the_right_password_works_once_the_lock_has_run_out(self):
        self.past(9, 8.9, 8.8, 8.7, 8.6)                   # old lock, inside the window, already over
        r = self.client.post(reverse("login"), {"username": "lockme", "password": "right-pass-123"})
        self.assertEqual(r.status_code, 302)


class AdminLoginUnchangedTests(TestCase):
    """The admin login keeps the numbers it always had: 5 wrong passwords within 5 minutes, locked for 5."""

    def setUp(self):
        User.objects.create_superuser("boss", "boss@example.com", "admin-pass-123")
        self.key = throttle.key_for("boss")
        self.url = "/admin/login/?next=/admin/"

    def wrong(self):
        return self.client.post(self.url, {"username": "boss", "password": "nope", "next": "/admin/"})

    def past(self, *minutes_ago):
        for m in minutes_ago:
            row = LoginAttempt.objects.create(key=self.key)
            LoginAttempt.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(minutes=m))

    def test_the_admin_uses_the_old_policy_not_the_site_one(self):
        from .admin import ThrottledAdminLoginForm
        p = ThrottledAdminLoginForm.throttle_policy
        self.assertEqual((p.max_attempts, p.window_minutes, p.lock_minutes), (5, 5, 5))

    def test_five_wrong_passwords_lock_the_admin_for_five_minutes(self):
        for _ in range(4):
            self.assertNotContains(self.wrong(), "Too many wrong attempts")
        r = self.wrong()
        self.assertContains(r, "Too many wrong attempts")
        self.assertContains(r, "5 more minutes")

    def test_the_site_window_of_15_minutes_does_not_apply_to_the_admin(self):
        self.past(10, 9, 8, 7)                             # inside 15 minutes but older than the admin's 5
        self.assertNotContains(self.wrong(), "Too many wrong attempts")

    def test_the_admin_page_is_the_plain_one_with_no_countdown(self):
        for _ in range(5):
            r = self.wrong()
        self.assertNotContains(r, 'id="lock-box"')
        self.assertNotContains(r, "setTimeout")


class AvailabilityTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name="India")
        city = City.objects.create(state=State.objects.create(country=country, name="Gujarat"), name="Vadodara")
        self.pro_user = User.objects.create_user("pro", password="pass12345", first_name="Pro")
        UserProfile.objects.create(user=self.pro_user, role="photographer")
        self.ph = Photographer.objects.create(name="Alpha Snap", bio="bio", specialities="wedding", city=city,
                                              price_per_day=8000, user=self.pro_user)
        self.ph2 = Photographer.objects.create(name="Beta Snap", bio="bio", specialities="event", city=city, price_per_day=5000)
        self.cust = User.objects.create_user("cust", password="pass12345", first_name="Cust")
        self.today = timezone.localdate()

    def day(self, n):
        return self.today + timedelta(days=n)

    def block(self, n, reason="", who=None):
        return BlockedDate.objects.create(photographer=who or self.ph, date=self.day(n), reason=reason)

    def book(self, n, status="pending", who=None):
        return Booking.objects.create(user=self.cust, photographer=who or self.ph, event_date=self.day(n), status=status)

    # ---- the rule ----
    def test_what_makes_a_day_unavailable(self):
        self.block(3)
        self.book(4, "pending"); self.book(5, "accepted"); self.book(6, "confirmed"); self.book(7, "cancelled")
        free = lambda n: availability.is_free(self.ph, self.day(n))
        self.assertTrue(free(0))            # today is still bookable
        self.assertFalse(free(-1))          # the past is not
        self.assertFalse(free(3))           # blocked
        self.assertTrue(free(4))            # a pending request does not block the day
        self.assertFalse(free(5))           # accepted
        self.assertFalse(free(6))           # paid
        self.assertTrue(free(7))            # cancelled bookings free the day again

    def test_free_on_lists_only_free_approved_photographers(self):
        self.block(3)
        self.book(3, "pending", who=self.ph2)                      # only a request: still free
        hidden = Photographer.objects.create(name="Hidden", bio="b", specialities="event", city=self.ph.city, status="pending")
        away = Photographer.objects.create(name="Away", bio="b", specialities="event", city=self.ph.city, is_available=False)
        names = set(availability.free_on(self.day(3)).values_list("name", flat=True))
        self.assertEqual(names, {"Beta Snap"})
        self.assertEqual(set(availability.free_on(self.day(4)).values_list("name", flat=True)), {"Alpha Snap", "Beta Snap"})

    # ---- the calendar data ----
    def test_calendar_layout(self):
        today = date(2026, 11, 10)
        cal = availability.months(self.ph, 3, today=today)
        self.assertEqual([m["label"] for m in cal], ["November 2026", "December 2026", "January 2027"])
        for month in cal:
            for week in month["weeks"]:
                self.assertEqual(len(week), 7)
        first_week = cal[0]["weeks"][0]
        self.assertEqual(first_week[date(2026, 11, 1).weekday()]["day"], 1)       # weeks start on Monday
        self.assertFalse(first_week[0]["in_month"] and date(2026, 11, 1).weekday() != 0)
        cells = {c["date"]: c for m in cal for w in m["weeks"] for c in w if c["in_month"]}
        self.assertEqual(cells[date(2026, 11, 9)]["state"], "past")
        self.assertEqual(cells[date(2026, 11, 10)]["state"], "free")

    def test_customers_cannot_tell_blocked_from_booked_but_the_photographer_can(self):
        blocked = self.block(5, "Family wedding")
        booked = self.book(6, "confirmed")
        cells = lambda private: {c["date"]: c for m in availability.months(self.ph, 2, private=private)
                                 for w in m["weeks"] for c in w if c["in_month"]}
        public, private = cells(False), cells(True)
        self.assertEqual((public[blocked.date]["state"], public[blocked.date]["note"]), ("booked", ""))
        self.assertEqual((public[booked.event_date]["state"], public[booked.event_date]["note"]), ("booked", ""))
        self.assertEqual(private[blocked.date]["state"], "blocked")
        self.assertIn("Family wedding", private[blocked.date]["note"])
        self.assertEqual(private[booked.event_date]["note"], "Booked: Cust")

    # ---- booking rules ----
    def test_a_blocked_day_cannot_be_requested(self):
        self.block(10, "Holiday")
        self.client.login(username="cust", password="pass12345")
        r = self.client.post(reverse("photographer_detail", args=[self.ph.slug]), {**EVENT(), "event_date": self.day(10).isoformat(), "tool": ""})
        self.assertContains(r, "not available on that date")
        self.assertNotContains(r, "Holiday")
        self.assertEqual(Booking.objects.count(), 0)

    def test_cannot_accept_a_request_for_a_day_blocked_later(self):
        b = self.book(10)
        self.block(10)
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.accept(b)
        b.refresh_from_db()
        self.assertEqual(b.status, "pending")

    # ---- what customers see ----
    def test_detail_page_shows_a_calendar_and_hides_reasons(self):
        self.block(3, "Private reason")
        self.book(4, "confirmed")
        self.client.login(username="cust", password="pass12345")
        page = self.client.get(reverse("photographer_detail", args=[self.ph.slug])).content.decode()
        self.assertIn("Availability", page)
        self.assertIn(f'data-date="{self.day(5).isoformat()}"', page)           # a free day you can click
        self.assertNotIn(f'data-date="{self.day(3).isoformat()}"', page)        # blocked: not clickable
        self.assertNotIn(f'data-date="{self.day(4).isoformat()}"', page)        # booked: not clickable
        self.assertNotIn("Private reason", page)

    def test_photographer_accounts_see_the_calendar_but_cannot_click_days(self):
        self.client.login(username="pro", password="pass12345")
        page = self.client.get(reverse("photographer_detail", args=[self.ph2.slug])).content.decode()
        self.assertIn("cal-day", page)
        self.assertNotIn("data-date=", page)

    # ---- searching by date ----
    def test_search_by_date_hides_photographers_who_are_not_free(self):
        self.block(8)
        r = self.client.get(reverse("photographer_list"), {"date": self.day(8).isoformat()})
        self.assertContains(r, "Beta Snap")
        self.assertNotContains(r, "Alpha Snap")
        self.assertContains(r, "Clear date")                                 # shown only while a date filter is active
        self.assertContains(r, f"?date={self.day(8).isoformat()}")           # the card link carries the date along

    def test_a_bad_or_past_date_is_ignored(self):
        self.block(8)
        for value in ("not-a-date", self.day(-3).isoformat(), ""):
            r = self.client.get(reverse("photographer_list"), {"date": value})
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "Alpha Snap")
            self.assertNotContains(r, "Clear date")

    def test_the_chosen_date_is_filled_into_the_booking_form(self):
        self.block(9)
        free = self.client.get(reverse("photographer_detail", args=[self.ph.slug]), {"date": self.day(8).isoformat()})
        self.assertContains(free, f'value="{self.day(8).isoformat()}"')
        taken = self.client.get(reverse("photographer_detail", args=[self.ph.slug]), {"date": self.day(9).isoformat()})
        self.assertNotContains(taken, f'value="{self.day(9).isoformat()}"')

    def test_home_search_has_a_date_field(self):
        self.assertContains(self.client.get(reverse("home")), 'name="date"')

    # ---- the photographer's own page ----
    def test_only_photographers_open_the_availability_page(self):
        self.assertEqual(self.client.get(reverse("pro_availability")).status_code, 302)
        self.client.login(username="cust", password="pass12345")
        self.assertEqual(self.client.get(reverse("pro_availability")).status_code, 302)
        self.client.login(username="pro", password="pass12345")
        self.assertEqual(self.client.get(reverse("pro_availability")).status_code, 200)

    def test_blocking_a_range(self):
        self.client.login(username="pro", password="pass12345")
        data = {"start": self.day(10).isoformat(), "end": self.day(12).isoformat(), "reason": "Family wedding"}
        self.assertRedirects(self.client.post(reverse("pro_availability"), data), reverse("pro_availability"))
        self.assertEqual(BlockedDate.objects.filter(photographer=self.ph).count(), 3)
        self.assertEqual(BlockedDate.objects.get(date=self.day(11)).reason, "Family wedding")
        r = self.client.post(reverse("pro_availability"), data, follow=True)       # same days again
        self.assertEqual(BlockedDate.objects.count(), 3)
        self.assertContains(r, "already blocked")

    def test_a_single_day_needs_only_the_start_date(self):
        self.client.login(username="pro", password="pass12345")
        self.client.post(reverse("pro_availability"), {"start": self.day(20).isoformat(), "end": "", "reason": ""})
        self.assertEqual(BlockedDate.objects.filter(date=self.day(20)).count(), 1)

    def test_bad_ranges_are_refused(self):
        self.client.login(username="pro", password="pass12345")
        url = reverse("pro_availability")
        r = self.client.post(url, {"start": self.day(-2).isoformat(), "end": ""})
        self.assertContains(r, "from today onwards")
        r = self.client.post(url, {"start": self.day(10).isoformat(), "end": self.day(8).isoformat()})
        self.assertContains(r, "cannot be before")
        r = self.client.post(url, {"start": self.day(1).isoformat(), "end": self.day(120).isoformat()})
        self.assertContains(r, "at most 90 days")
        self.assertEqual(BlockedDate.objects.count(), 0)

    def test_cannot_block_days_that_already_have_a_booking(self):
        self.book(11, "confirmed")
        self.client.login(username="pro", password="pass12345")
        r = self.client.post(reverse("pro_availability"), {"start": self.day(10).isoformat(), "end": self.day(12).isoformat()})
        self.assertContains(r, "already have a booking")
        self.assertEqual(BlockedDate.objects.count(), 0)

    def test_unblocking(self):
        mine, theirs = self.block(5), self.block(5, who=self.ph2)
        self.client.login(username="pro", password="pass12345")
        self.assertEqual(self.client.get(reverse("pro_unblock", args=[mine.pk])).status_code, 405)
        self.assertEqual(self.client.post(reverse("pro_unblock", args=[theirs.pk])).status_code, 404)
        self.client.post(reverse("pro_unblock", args=[mine.pk]))
        self.assertFalse(BlockedDate.objects.filter(pk=mine.pk).exists())
        self.assertTrue(BlockedDate.objects.filter(pk=theirs.pk).exists())

    def test_availability_page_shows_reasons_and_admin_lists_blocks(self):
        self.block(5, "Family wedding")
        self.client.login(username="pro", password="pass12345")
        self.assertContains(self.client.get(reverse("pro_availability")), "Family wedding")
        User.objects.create_superuser("boss", "b@example.com", "pass12345")
        self.client.login(username="boss", password="pass12345")
        self.assertContains(self.client.get(reverse("admin:photopholio_blockeddate_changelist")), "Alpha Snap")


class PackageTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name="India")
        city = City.objects.create(state=State.objects.create(country=country, name="Gujarat"), name="Vadodara")
        self.pro_user = User.objects.create_user("pro", password="pass12345", first_name="Pro", email="pro@example.com")
        UserProfile.objects.create(user=self.pro_user, role="photographer")
        self.ph = Photographer.objects.create(name="Alpha Snap", bio="bio", specialities="wedding", city=city,
                                              price_per_day=8000, user=self.pro_user)
        self.plain = Photographer.objects.create(name="Plain Snap", bio="bio", specialities="event", city=city, price_per_day=5000)
        self.cust = User.objects.create_user("cust", password="pass12345", first_name="Cust", email="cust@example.com")
        self.today = timezone.localdate()

    def pkg(self, tier, price, who=None, **kw):
        return Package.objects.create(photographer=who or self.ph, tier=tier, price=price, **kw)

    def three(self):
        return self.pkg("basic", 4000), self.pkg("standard", 8000), self.pkg("premium", 12000)

    def request(self, package=None, days=10, who=None, extra=None):
        self.client.login(username="cust", password="pass12345")
        data = {**EVENT(), "event_date": (self.today + timedelta(days=days)).isoformat(), "tool": ""}
        if package is not None:
            data["package"] = package.pk if hasattr(package, "pk") else package
        data.update(extra or {})
        return self.client.post(reverse("photographer_detail", args=[(who or self.ph).slug]), data)

    # ---- the model ----
    def test_active_packages_come_basic_first_and_skip_hidden_ones(self):
        self.pkg("premium", 12000); self.pkg("basic", 4000); self.pkg("standard", 8000, is_active=False)
        self.assertEqual([p.tier for p in self.ph.active_packages], ["basic", "premium"])
        self.assertEqual(self.ph.starting_price, 4000)

    def test_without_packages_the_day_rate_is_the_starting_price(self):
        self.assertEqual(self.plain.active_packages, [])
        self.assertEqual(self.plain.starting_price, 5000)

    def test_one_package_per_tier(self):
        from django.db import IntegrityError, transaction
        self.pkg("basic", 4000)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.pkg("basic", 5000)

    # ---- booking and the price snapshot ----
    def test_booking_without_packages_uses_the_day_rate(self):
        self.request(who=self.plain)
        b = Booking.objects.get()
        self.assertEqual((b.package, b.package_name, b.price, b.amount), (None, "Full day", 5000, 5000))

    def test_a_package_must_be_chosen_and_must_belong_to_this_photographer(self):
        basic, standard, premium = self.three()
        theirs = self.pkg("basic", 1000, who=self.plain)
        hidden = Package.objects.create(photographer=self.ph, tier="basic", price=1, is_active=False) if False else None
        r = self.request()
        self.assertContains(r, "Please choose a package")
        self.request(package=theirs)
        standard.is_active = False
        standard.save()
        self.request(package=standard)
        self.assertEqual(Booking.objects.count(), 0)

    def test_the_chosen_package_is_copied_onto_the_booking(self):
        basic, standard, premium = self.three()
        self.request(package=premium)
        b = Booking.objects.get()
        self.assertEqual((b.package, b.package_name, b.price, b.amount), (premium, "Premium", 12000, 12000))

    def test_changing_or_deleting_a_package_does_not_change_existing_bookings(self):
        basic, standard, premium = self.three()
        self.request(package=standard)
        b = Booking.objects.get()
        standard.price = 99999
        standard.save()
        b.refresh_from_db()
        self.assertEqual(b.amount, 8000)
        standard.delete()
        b.refresh_from_db()
        self.assertEqual((b.package, b.package_name, b.amount), (None, "Standard", 8000))
        self.ph.price_per_day = 1000
        self.ph.save()
        self.assertEqual(Booking.objects.get().amount, 8000)

    def test_old_bookings_without_a_snapshot_fall_back_to_the_day_rate(self):
        b = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.today + timedelta(days=5))
        self.assertIsNone(b.price)
        self.assertEqual(b.amount, 8000)

    def test_the_migration_freezes_the_price_of_old_bookings_only(self):
        import importlib
        from django.apps import apps
        mod = importlib.import_module("photopholio.migrations.0006_booking_package_name_booking_price_package_and_more")
        old = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.today + timedelta(days=5))
        new = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.today + timedelta(days=6), price=1234)
        mod.freeze_existing_prices(apps, None)
        old.refresh_from_db(); new.refresh_from_db()
        self.assertEqual((old.price, new.price), (8000, 1234))
        self.ph.price_per_day = 1
        self.ph.save()
        self.assertEqual(Booking.objects.get(pk=old.pk).amount, 8000)

    def test_payment_and_refund_follow_the_snapshot(self):
        basic, standard, premium = self.three()
        self.request(package=premium)
        b = Booking.objects.get()
        booking_flow.accept(b)
        self.client.post(reverse("payment", args=[b.pk]), {"payment_method": "upi"})
        pay = b.paid_payment
        self.assertEqual(pay.amount, 12000)
        premium.price = 50000
        premium.save()
        booking_flow.cancel_by_customer(b, today=self.today + timedelta(days=9))      # one day before: 50%
        pay.refresh_from_db()
        self.assertEqual(pay.refund_amount, 6000)

    # ---- what people see ----
    def test_detail_page_shows_the_packages_and_a_radio_for_each(self):
        self.pkg("basic", 4000, hours=3, edited_photos=80, description="Short and sweet")
        self.pkg("standard", 8000); self.pkg("premium", 12000, is_active=False)
        self.client.login(username="cust", password="pass12345")
        page = self.client.get(reverse("photographer_detail", args=[self.ph.slug])).content.decode()
        self.assertIn('id="packages"', page)
        self.assertIn("Short and sweet", page)
        self.assertEqual(page.count('type="radio" name="package"'), 2)    # the hidden premium is not offered
        self.assertNotIn("12000", page)
        self.assertIn("From</span> <strong class=\"fs-4\">&#8377;4000", page)

    def test_photographer_accounts_see_packages_but_cannot_choose_one(self):
        self.pkg("basic", 4000, who=self.plain)
        self.client.login(username="pro", password="pass12345")
        page = self.client.get(reverse("photographer_detail", args=[self.plain.slug])).content.decode()
        self.assertIn('id="packages"', page)
        self.assertNotIn("data-package=", page)

    def test_cards_say_from_when_there_are_packages(self):
        self.pkg("basic", 3000)
        r = self.client.get(reverse("photographer_list"))
        self.assertContains(r, "From &#8377;3000")
        self.assertContains(r, "&#8377;5000 <span>/ day</span>")           # the one without packages
        self.assertContains(self.client.get(reverse("home")), "From &#8377;3000")

    def test_price_sorting_uses_package_prices(self):
        a = Photographer.objects.create(name="Cheap Packages", bio="b", specialities="event", city=self.ph.city, price_per_day=9000)
        self.pkg("basic", 3000, who=a)
        order = lambda sort: [p.name for p in self.client.get(reverse("photographer_list"), {"sort": sort}).context["page"]]
        self.assertEqual(order("price_low")[0], "Cheap Packages")           # 3000 beats 5000 and 8000
        self.assertEqual(order("price_high")[0], "Alpha Snap")              # 8000 day rate is the highest

    def test_bookings_pages_and_emails_name_the_package(self):
        self.pkg("standard", 8000)
        self.request(package=Package.objects.get(tier="standard"))
        self.assertTrue(any("Standard" in m.body for m in mail.outbox))
        self.assertContains(self.client.get(reverse("my_bookings")), "Standard, &#8377;8000")
        self.client.logout()
        self.client.login(username="pro", password="pass12345")
        self.assertContains(self.client.get(reverse("pro_dashboard")), "Standard, &#8377;8000")

    # ---- the photographer's packages page ----
    def save_form(self, tier="basic", **over):
        data = {"tier": tier, "action": "save", f"{tier}-price": 4000, f"{tier}-hours": 3, f"{tier}-edited_photos": 80,
                f"{tier}-description": "Starter", f"{tier}-is_active": "on"}
        data.update({f"{tier}-{k}": v for k, v in over.items()})
        return self.client.post(reverse("pro_packages"), data)

    def test_only_photographers_open_the_packages_page(self):
        self.assertEqual(self.client.get(reverse("pro_packages")).status_code, 302)
        self.client.login(username="cust", password="pass12345")
        self.assertEqual(self.client.get(reverse("pro_packages")).status_code, 302)
        self.client.login(username="pro", password="pass12345")
        self.assertEqual(self.client.get(reverse("pro_packages")).status_code, 200)

    def test_add_edit_hide_and_remove_a_package(self):
        self.client.login(username="pro", password="pass12345")
        self.assertRedirects(self.save_form(), reverse("pro_packages"))
        p = Package.objects.get(photographer=self.ph, tier="basic")
        self.assertEqual((p.price, p.hours, p.edited_photos), (4000, 3, 80))
        self.save_form(price=4500)                                                  # same tier: updates, no second row
        self.assertEqual(Package.objects.filter(photographer=self.ph).count(), 1)
        self.assertEqual(Package.objects.get().price, 4500)
        self.client.post(reverse("pro_packages"), {"tier": "basic", "action": "save", "basic-price": 4500, "basic-hours": 3,
                                                   "basic-edited_photos": 80, "basic-description": ""})   # box not ticked = hidden
        self.assertFalse(Package.objects.get().is_active)
        self.client.post(reverse("pro_packages"), {"tier": "basic", "action": "remove"})
        self.assertEqual(Package.objects.count(), 0)

    def test_bad_package_values_are_refused(self):
        self.client.login(username="pro", password="pass12345")
        self.assertContains(self.save_form(price=100), "between 500 and 1,000,000")
        self.assertContains(self.save_form(hours=30), "between 1 and 24")
        self.assertContains(self.save_form(edited_photos=9999), "realistic number")
        self.assertEqual(Package.objects.count(), 0)

    def test_unknown_tier_and_other_photographers_packages(self):
        theirs = self.pkg("basic", 1000, who=self.plain)
        self.client.login(username="pro", password="pass12345")
        self.assertEqual(self.client.post(reverse("pro_packages"), {"tier": "gold", "action": "save"}).status_code, 404)
        self.save_form()                                                            # creates MY basic, leaves theirs alone
        theirs.refresh_from_db()
        self.assertEqual(theirs.price, 1000)
        self.client.post(reverse("pro_packages"), {"tier": "basic", "action": "remove"})
        self.assertTrue(Package.objects.filter(pk=theirs.pk).exists())

    # ---- sample packages and admin ----
    def test_sample_packages_command(self):
        from io import StringIO
        from django.core.management import call_command
        self.pkg("basic", 111)                                                     # Alpha already has packages: skipped
        low = Photographer.objects.create(name="Low Rate", bio="b", specialities="event", city=self.ph.city, price_per_day=600)
        call_command("add_sample_packages", stdout=StringIO())
        self.assertEqual(Package.objects.filter(photographer=self.ph).count(), 1)
        self.assertEqual({p.tier: p.price for p in self.plain.packages.all()}, {"basic": 2500, "standard": 5000, "premium": 8000})
        self.assertEqual(low.packages.get(tier="basic").price, 500)                # never below 500

    def test_admin_shows_packages(self):
        self.three()
        User.objects.create_superuser("boss", "b@example.com", "pass12345")
        self.client.login(username="boss", password="pass12345")
        self.assertContains(self.client.get(reverse("admin:photopholio_photographer_change", args=[self.ph.pk])), "packages")
        self.request(package=Package.objects.get(tier="premium"))
        self.client.login(username="boss", password="pass12345")
        self.assertContains(self.client.get(reverse("admin:photopholio_booking_changelist")), "Premium")


class RescheduleTests(TestCase):
    def setUp(self):
        country = Country.objects.create(name="India")
        state = State.objects.create(country=country, name="Gujarat")
        city = City.objects.create(state=state, name="Vadodara")
        self.pro_user = User.objects.create_user("pro", password="pass12345", first_name="Pro", email="pro@example.com")
        UserProfile.objects.create(user=self.pro_user, role="photographer")
        self.ph = Photographer.objects.create(name="Move Snap", bio="bio", specialities="wedding", city=city,
                                              price_per_day=8000, user=self.pro_user, status="approved")
        self.cust = User.objects.create_user("cust", password="pass12345", first_name="Cust", email="c@example.com")
        self.cust2 = User.objects.create_user("cust2", password="pass12345", email="c2@example.com")
        self.today = timezone.localdate()

    def d(self, n):
        return self.today + timedelta(days=n)

    def booking(self, days=10, status="confirmed", user=None, paid=True, price=None):
        b = Booking.objects.create(user=user or self.cust, photographer=self.ph, event_date=self.d(days),
                                   status=status, price=price,
                                   accepted_at=timezone.now())
        if paid:
            Payment.objects.create(user=b.user, booking=b, amount=8000, payment_method="upi", status="completed")
        return b

    # ---- rules ----
    def test_request_ok_and_booking_date_unchanged(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20), "family event")
        b.refresh_from_db()
        self.assertEqual(r.status, "pending")
        self.assertEqual(b.event_date, self.d(10))
        self.assertEqual(r.old_date, self.d(10))
        self.assertEqual(b.pending_reschedule, r)

    def test_rules_reject(self):
        b = self.booking(days=10)
        cases = [(self.d(10), "same date"), (self.d(0), "today"), (self.d(-3), "past")]
        for day, _ in cases:
            with self.assertRaises(booking_flow.FlowError):
                booking_flow.request_reschedule(b, day)
        BlockedDate.objects.create(photographer=self.ph, date=self.d(15))
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.request_reschedule(b, self.d(15))
        self.booking(days=16, user=self.cust2)  # confirmed on 16 -> taken
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.request_reschedule(b, self.d(16))
        self.assertEqual(RescheduleRequest.objects.count(), 0)

    def test_too_close_and_wrong_status(self):
        near = self.booking(days=1)
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.request_reschedule(near, self.d(20))
        exactly = self.booking(days=policy.RESCHEDULE_MIN_DAYS, user=self.cust2)
        booking_flow.request_reschedule(exactly, self.d(25))
        for status in ("pending", "cancelled", "declined", "completed"):
            b = self.booking(days=30, status=status, paid=False)
            with self.assertRaises(booking_flow.FlowError):
                booking_flow.request_reschedule(b, self.d(40))

    def test_only_one_open_request_and_one_move(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20))
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.request_reschedule(b, self.d(21))
        booking_flow.accept_reschedule(r)
        b.refresh_from_db()
        with self.assertRaises(booking_flow.FlowError):  # the one allowed move is used
            booking_flow.request_reschedule(b, self.d(30))

    def test_declined_request_allows_another_try(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20))
        booking_flow.decline_reschedule(r, "Busy")
        r.refresh_from_db()
        self.assertEqual((r.status, r.decline_reason), ("declined", "Busy"))
        booking_flow.request_reschedule(b, self.d(22))  # counts only accepted moves

    # ---- accept ----
    def test_accept_moves_date_keeps_status_price_and_payment(self):
        b = self.booking(price=5000)
        r = booking_flow.request_reschedule(b, self.d(20))
        booking_flow.accept_reschedule(r)
        b.refresh_from_db()
        self.assertEqual((b.event_date, b.status, b.amount), (self.d(20), "confirmed", 5000))
        self.assertTrue(b.is_paid)
        self.assertEqual(b.reschedule_count, 1)
        self.assertIsNone(b.pending_reschedule)

    def test_accept_fails_if_new_day_got_taken_or_blocked(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20))
        BlockedDate.objects.create(photographer=self.ph, date=self.d(20))
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.accept_reschedule(r)
        b.refresh_from_db()
        self.assertEqual(b.event_date, self.d(10))
        BlockedDate.objects.all().delete()
        self.booking(days=20, user=self.cust2)
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.accept_reschedule(r)

    def test_accept_declines_rival_pending_booking_and_rival_reschedule(self):
        b = self.booking()
        other = self.booking(days=12, user=self.cust2)
        rival_booking = Booking.objects.create(user=self.cust2, photographer=self.ph, event_date=self.d(20), status="pending")
        r1 = booking_flow.request_reschedule(b, self.d(20))
        r2 = booking_flow.request_reschedule(other, self.d(20))
        booking_flow.accept_reschedule(r1)
        rival_booking.refresh_from_db()
        r2.refresh_from_db()
        self.assertEqual(rival_booking.status, "declined")
        self.assertEqual(r2.status, "declined")
        other.refresh_from_db()
        self.assertEqual(other.event_date, self.d(12))

    def test_cannot_answer_twice(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20))
        booking_flow.accept_reschedule(r)
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.accept_reschedule(r)
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.decline_reschedule(r)
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.withdraw_reschedule(r)

    def test_withdraw(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20))
        booking_flow.withdraw_reschedule(r)
        r.refresh_from_db()
        self.assertEqual(r.status, "withdrawn")
        booking_flow.request_reschedule(b, self.d(21))

    # ---- interaction with cancellation and refunds ----
    def test_cancel_closes_open_request(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20))
        booking_flow.cancel_by_customer(b)
        r.refresh_from_db()
        self.assertEqual(r.status, "closed")
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.accept_reschedule(r)

    def test_photographer_cancel_and_expiry_close_request(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20))
        booking_flow.cancel_by_photographer(b, "ill")
        r.refresh_from_db()
        self.assertEqual(r.status, "closed")
        acc = self.booking(days=30, status="accepted", paid=False, user=self.cust2)
        Booking.objects.filter(pk=acc.pk).update(accepted_at=timezone.now() - timedelta(hours=policy.PAYMENT_WINDOW_HOURS + 1))
        r2 = booking_flow.request_reschedule(acc, self.d(40))
        booking_flow.expire_stale()
        r2.refresh_from_db()
        self.assertEqual(r2.status, "closed")

    def test_refund_uses_the_new_date_after_move(self):
        b = self.booking(days=10)
        r = booking_flow.request_reschedule(b, self.d(20))
        booking_flow.accept_reschedule(r)
        b.refresh_from_db()
        self.assertEqual(policy.days_before(b.event_date), 20)
        self.assertEqual(booking_flow.cancel_by_customer(b), 8000)

    def test_availability_reflects_move(self):
        b = self.booking(days=10)
        r = booking_flow.request_reschedule(b, self.d(20))
        self.assertFalse(availability.is_free(self.ph, self.d(10)))
        self.assertTrue(availability.is_free(self.ph, self.d(20)))  # pending request does not block
        booking_flow.accept_reschedule(r)
        self.assertTrue(availability.is_free(self.ph, self.d(10)))
        self.assertFalse(availability.is_free(self.ph, self.d(20)))

    # ---- emails ----
    def test_emails(self):
        b = self.booking()
        mail.outbox.clear()
        r = booking_flow.request_reschedule(b, self.d(20), "trip")
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["c@example.com", "pro@example.com"])
        mail.outbox.clear()
        booking_flow.accept_reschedule(r)
        self.assertEqual([m.to[0] for m in mail.outbox], ["c@example.com"])
        self.assertIn("booking was moved", mail.outbox[0].body.lower())
        b2 = self.booking(days=12, user=self.cust2)
        r2 = booking_flow.request_reschedule(b2, self.d(30))
        mail.outbox.clear()
        booking_flow.decline_reschedule(r2, "Already booked elsewhere")
        self.assertIn("Already booked elsewhere", mail.outbox[0].body)

    # ---- pages ----
    def test_customer_page_flow(self):
        b = self.booking()
        self.client.login(username="cust", password="pass12345")
        self.assertContains(self.client.get(reverse("my_bookings")), "Change date")
        self.assertContains(self.client.get(reverse("booking_reschedule", args=[b.pk])), "Change your date")
        resp = self.client.post(reverse("booking_reschedule", args=[b.pk]), {"new_date": self.d(20).isoformat(), "reason": "x"})
        self.assertRedirects(resp, reverse("my_bookings"))
        page = self.client.get(reverse("my_bookings"))
        self.assertContains(page, "Reschedule requested")
        self.assertNotContains(page, "Change date")
        req = b.pending_reschedule
        self.client.post(reverse("reschedule_withdraw", args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual(req.status, "withdrawn")

    def test_page_shows_rule_error_and_blocks_get_when_not_allowed(self):
        b = self.booking()
        self.client.login(username="cust", password="pass12345")
        resp = self.client.post(reverse("booking_reschedule", args=[b.pk]), {"new_date": self.d(10).isoformat()})
        self.assertContains(resp, "same date")
        near = self.booking(days=0)
        self.assertRedirects(self.client.get(reverse("booking_reschedule", args=[near.pk])), reverse("my_bookings"))

    def test_other_customer_cannot_touch(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20))
        self.client.login(username="cust2", password="pass12345")
        self.assertEqual(self.client.get(reverse("booking_reschedule", args=[b.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("reschedule_withdraw", args=[r.pk])).status_code, 404)

    def test_photographer_pages(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20), "wedding moved")
        self.client.login(username="pro", password="pass12345")
        page = self.client.get(reverse("pro_dashboard"))
        self.assertContains(page, "Date change requests")
        self.assertContains(page, "wedding moved")
        self.client.post(reverse("pro_reschedule_action", args=[r.pk, "accept"]))
        b.refresh_from_db()
        self.assertEqual(b.event_date, self.d(20))
        self.assertNotContains(self.client.get(reverse("pro_dashboard")), "Date change requests")

    def test_photographer_decline_with_reason_page(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20))
        self.client.login(username="pro", password="pass12345")
        self.client.post(reverse("pro_reschedule_action", args=[r.pk, "decline"]), {"reason": "No"})
        r.refresh_from_db()
        self.assertEqual((r.status, r.decline_reason), ("declined", "No"))
        self.assertEqual(self.client.post(reverse("pro_reschedule_action", args=[r.pk, "nuke"])).status_code, 404)

    def test_other_photographer_cannot_answer(self):
        b = self.booking()
        r = booking_flow.request_reschedule(b, self.d(20))
        u = User.objects.create_user("pro2", password="pass12345")
        UserProfile.objects.create(user=u, role="photographer")
        self.client.login(username="pro2", password="pass12345")
        self.assertEqual(self.client.post(reverse("pro_reschedule_action", args=[r.pk, "accept"])).status_code, 404)

    def test_policy_page_mentions_rule_and_admin_loads(self):
        self.assertContains(self.client.get(reverse("cancellation_policy")), "Changing your date")
        User.objects.create_superuser("boss", "b@example.com", "pass12345")
        self.client.login(username="boss", password="pass12345")
        self.assertEqual(self.client.get("/admin/photopholio/reschedulerequest/").status_code, 200)


class FinalPhaseTests(TestCase):
    """Wishlist, Verified badge, review replies and hiding, photo delivery link, customer dashboard."""

    def setUp(self):
        country = Country.objects.create(name="India")
        state = State.objects.create(country=country, name="Gujarat")
        self.city = City.objects.create(state=state, name="Vadodara")
        self.pro_user = User.objects.create_user("pro", password="pass12345", first_name="Pro", email="pro@example.com")
        UserProfile.objects.create(user=self.pro_user, role="photographer")
        self.ph = Photographer.objects.create(name="Final Snap", bio="bio", specialities="wedding", city=self.city,
                                              price_per_day=8000, user=self.pro_user, status="approved")
        self.cust = User.objects.create_user("cust", password="pass12345", first_name="Cust", email="c@example.com")
        self.other = User.objects.create_user("other", password="pass12345", email="o@example.com")
        self.admin = User.objects.create_superuser("boss", "b@example.com", "pass12345")
        self.today = timezone.localdate()

    def booking(self, status="completed", days=-3, user=None, paid=True):
        b = Booking.objects.create(user=user or self.cust, photographer=self.ph,
                                   event_date=self.today + timedelta(days=days), status=status)
        if paid:
            Payment.objects.create(user=b.user, booking=b, amount=8000, payment_method="upi", status="completed")
        return b

    def review(self, rating=5, user=None, text="Lovely work"):
        b = self.booking(user=user)
        return Feedback.objects.create(user=b.user, booking=b, rating=rating, comment=text)

    # ---- wishlist ----
    def test_toggle_saves_and_unsaves(self):
        self.assertTrue(favourites.toggle(self.cust, self.ph))
        self.assertEqual(favourites.saved_ids(self.cust), {self.ph.pk})
        self.assertFalse(favourites.toggle(self.cust, self.ph))
        self.assertEqual(Favourite.objects.count(), 0)

    def test_saved_list_is_private_and_hides_pulled_profiles(self):
        favourites.toggle(self.cust, self.ph)
        self.assertEqual(favourites.saved_ids(self.other), set())
        self.assertEqual(list(favourites.saved_photographers(self.cust)), [self.ph])
        self.ph.status = "rejected"
        self.ph.save()
        self.assertEqual(list(favourites.saved_photographers(self.cust)), [])

    def test_save_pages(self):
        self.client.login(username="cust", password="pass12345")
        resp = self.client.post(reverse("favourite_toggle", args=[self.ph.slug]), {"next": "/photographers/"})
        self.assertRedirects(resp, "/photographers/", fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("my_favourites")), "Final Snap")
        self.client.post(reverse("favourite_toggle", args=[self.ph.slug]))
        self.assertContains(self.client.get(reverse("my_favourites")), "Nothing saved yet")

    def test_save_next_must_stay_on_this_site(self):
        self.client.login(username="cust", password="pass12345")
        resp = self.client.post(reverse("favourite_toggle", args=[self.ph.slug]), {"next": "https://evil.example/x"})
        self.assertEqual(resp.url, reverse("photographer_detail", args=[self.ph.slug]))

    def test_save_rules(self):
        resp = self.client.post(reverse("favourite_toggle", args=[self.ph.slug]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)
        self.client.login(username="pro", password="pass12345")
        self.client.post(reverse("favourite_toggle", args=[self.ph.slug]))
        self.assertEqual(Favourite.objects.count(), 0)
        self.assertRedirects(self.client.get(reverse("my_favourites")), reverse("pro_dashboard"))
        self.client.logout()
        self.ph.status = "pending"
        self.ph.save()
        self.client.login(username="cust", password="pass12345")
        self.client.post(reverse("favourite_toggle", args=[self.ph.slug]))
        self.assertEqual(Favourite.objects.count(), 0)

    def test_heart_shows_on_list_and_detail(self):
        self.client.login(username="cust", password="pass12345")
        self.assertContains(self.client.get(reverse("photographer_list")), "heart-form")
        self.assertContains(self.client.get(reverse("photographer_detail", args=[self.ph.slug])), "Save")
        self.client.logout()
        self.assertContains(self.client.get(reverse("photographer_list")), "heart-link")

    # ---- verified badge ----
    def test_verified_badge_and_filter(self):
        plain = Photographer.objects.create(name="Plain Snap", bio="bio", specialities="street", city=self.city,
                                            price_per_day=3000, status="approved")
        self.assertNotContains(self.client.get(reverse("photographer_detail", args=[self.ph.slug])), "verified-pill")
        self.client.login(username="boss", password="pass12345")
        self.client.post("/admin/photopholio/photographer/", {"action": "mark_verified", "_selected_action": [self.ph.pk]})
        self.ph.refresh_from_db()
        self.assertTrue(self.ph.is_verified)
        self.assertIsNotNone(self.ph.verified_at)
        self.assertContains(self.client.get(reverse("photographer_detail", args=[self.ph.slug])), "verified-pill")
        page = self.client.get(reverse("photographer_list"), {"verified": "1"})
        self.assertContains(page, "Final Snap")
        self.assertNotContains(page, "Plain Snap")
        self.assertContains(self.client.get(reverse("photographer_list")), "Plain Snap")
        self.client.post("/admin/photopholio/photographer/", {"action": "remove_verified", "_selected_action": [self.ph.pk]})
        self.ph.refresh_from_db()
        self.assertFalse(self.ph.is_verified)
        self.assertIsNone(self.ph.verified_at)

    def test_customer_cannot_verify_via_admin(self):
        self.client.login(username="cust", password="pass12345")
        self.client.post("/admin/photopholio/photographer/", {"action": "mark_verified", "_selected_action": [self.ph.pk]})
        self.ph.refresh_from_db()
        self.assertFalse(self.ph.is_verified)

    # ---- review replies ----
    def test_reply_saved_shown_once_emailed_once(self):
        fb = self.review()
        mail.outbox.clear()
        reviews.reply(fb, "  Thank you!  ")
        fb.refresh_from_db()
        self.assertEqual(fb.reply, "Thank you!")
        self.assertIsNotNone(fb.replied_at)
        self.assertEqual([m.to[0] for m in mail.outbox], ["c@example.com"])
        reviews.reply(fb, "Edited thanks")  # editing does not email again
        self.assertEqual(len(mail.outbox), 1)
        self.assertContains(self.client.get(reverse("photographer_detail", args=[self.ph.slug])), "Edited thanks")

    def test_reply_rules(self):
        fb = self.review()
        for bad in ("", "   ", "x" * (reviews.MAX_REPLY_CHARS + 1)):
            with self.assertRaises(booking_flow.FlowError):
                reviews.reply(fb, bad)
        fb.is_hidden = True
        fb.save()
        with self.assertRaises(booking_flow.FlowError):
            reviews.reply(fb, "hello")

    def test_reply_pages_and_ownership(self):
        fb = self.review()
        self.client.login(username="pro", password="pass12345")
        self.assertContains(self.client.get(reverse("pro_reviews")), "Lovely work")
        self.client.post(reverse("pro_review_reply", args=[fb.pk]), {"reply": "Thanks a lot"})
        fb.refresh_from_db()
        self.assertEqual(fb.reply, "Thanks a lot")
        self.client.logout()
        other_pro = User.objects.create_user("pro2", password="pass12345")
        UserProfile.objects.create(user=other_pro, role="photographer")
        self.client.login(username="pro2", password="pass12345")
        self.assertEqual(self.client.post(reverse("pro_review_reply", args=[fb.pk]), {"reply": "hijack"}).status_code, 404)
        self.client.logout()
        self.client.login(username="cust", password="pass12345")
        self.assertNotEqual(self.client.post(reverse("pro_review_reply", args=[fb.pk]), {"reply": "x"}).status_code, 200)
        fb.refresh_from_db()
        self.assertEqual(fb.reply, "Thanks a lot")

    # ---- hiding reviews ----
    def test_hide_removes_review_and_rating_then_show_restores(self):
        good = self.review(5)
        bad = self.review(1, user=self.other, text="Unfair rant")
        self.ph.refresh_from_db()
        self.assertEqual(self.ph.rating, 3.0)
        self.client.login(username="boss", password="pass12345")
        self.client.post("/admin/photopholio/feedback/", {"action": "hide_selected", "_selected_action": [bad.pk]})
        self.ph.refresh_from_db()
        self.assertEqual(self.ph.rating, 5.0)
        page = self.client.get(reverse("photographer_detail", args=[self.ph.slug]))
        self.assertNotContains(page, "Unfair rant")
        self.assertContains(page, "Lovely work")
        self.client.logout()
        self.client.login(username="pro", password="pass12345")
        self.assertNotContains(self.client.get(reverse("pro_reviews")), "Unfair rant")
        self.client.logout()
        self.client.login(username="boss", password="pass12345")
        self.client.post("/admin/photopholio/feedback/", {"action": "show_selected", "_selected_action": [bad.pk]})
        self.ph.refresh_from_db()
        self.assertEqual(self.ph.rating, 3.0)

    def test_hiding_every_review_gives_zero_rating(self):
        fb = self.review(4)
        reviews.set_hidden(Feedback.objects.filter(pk=fb.pk), True)
        self.ph.refresh_from_db()
        self.assertEqual(self.ph.rating, 0)

    # ---- delivery link ----
    def test_delivery_saved_and_emailed(self):
        b = self.booking()
        mail.outbox.clear()
        booking_flow.set_delivery(b, " https://drive.example.com/folder/abc ", "Password: 1234")
        b.refresh_from_db()
        self.assertEqual(b.delivery_link, "https://drive.example.com/folder/abc")
        self.assertEqual(b.delivery_note, "Password: 1234")
        self.assertIsNotNone(b.delivered_at)
        self.assertEqual([m.to[0] for m in mail.outbox], ["c@example.com"])
        booking_flow.set_delivery(b, "https://other.example.com/new")  # replaced
        b.refresh_from_db()
        self.assertEqual(b.delivery_link, "https://other.example.com/new")

    def test_delivery_rejects_bad_links_and_wrong_status(self):
        b = self.booking()
        for bad in ("", "not a link", "javascript:alert(1)", "ftp://x.example.com/a", "data:text/html,hi",
                    "https://" + "a" * 600 + ".com"):
            with self.assertRaises(booking_flow.FlowError, msg=bad):
                booking_flow.set_delivery(b, bad)
        for status in ("confirmed", "pending", "cancelled"):
            other = self.booking(status=status, days=20, paid=False)
            with self.assertRaises(booking_flow.FlowError):
                booking_flow.set_delivery(other, "https://ok.example.com")

    def test_delivery_pages(self):
        b = self.booking()
        self.client.login(username="cust", password="pass12345")
        self.assertContains(self.client.get(reverse("my_bookings")), "yet")
        self.client.logout()
        self.client.login(username="pro", password="pass12345")
        self.client.post(reverse("pro_delivery", args=[b.pk]), {"delivery_link": "https://photos.example.com/a", "delivery_note": "Enjoy"})
        self.client.logout()
        self.client.login(username="cust", password="pass12345")
        page = self.client.get(reverse("my_bookings"))
        self.assertContains(page, "https://photos.example.com/a")
        self.assertContains(page, "noopener")
        self.client.logout()
        self.client.login(username="other", password="pass12345")
        self.assertNotContains(self.client.get(reverse("my_bookings")), "photos.example.com")
        self.client.logout()
        u = User.objects.create_user("pro3", password="pass12345")
        UserProfile.objects.create(user=u, role="photographer")
        self.client.login(username="pro3", password="pass12345")
        self.assertEqual(self.client.post(reverse("pro_delivery", args=[b.pk]), {"delivery_link": "https://x.example.com"}).status_code, 404)

    # ---- customer dashboard ----
    def test_dashboard_sections_and_numbers(self):
        self.booking(status="pending", days=30, paid=False)
        self.booking(status="accepted", days=31, paid=False)
        soon = self.booking(status="confirmed", days=5)
        cancelled = self.booking(status="cancelled", days=40)
        pay = cancelled.paid_payment
        pay.refund_amount, pay.refund_status = 8000, "pending"
        pay.save()
        favourites.toggle(self.cust, self.ph)
        self.client.login(username="cust", password="pass12345")
        resp = self.client.get(reverse("my_bookings"))
        self.assertEqual(resp.status_code, 200)
        ctx = resp.context
        self.assertEqual(ctx["next_shoot"], soon)
        self.assertEqual(len(ctx["waiting"]), 1)
        self.assertEqual(len(ctx["to_pay"]), 1)
        self.assertEqual(ctx["refund_waiting"], 8000)
        self.assertEqual(ctx["spent"], 16000)  # two paid bookings (confirmed + cancelled); the refund is only pending
        self.assertEqual(ctx["saved_count"], 1)

    def test_dashboard_empty_and_refunded_total(self):
        self.client.login(username="other", password="pass12345")
        self.assertContains(self.client.get(reverse("my_bookings")), "No bookings yet")
        b = self.booking(status="cancelled", days=40, user=self.other)
        p = b.paid_payment
        p.refund_amount, p.refund_status = 8000, "refunded"
        p.save()
        self.assertEqual(self.client.get(reverse("my_bookings")).context["spent"], 0)

    def test_migration_state_is_clean(self):
        from django.core.management import call_command
        call_command("makemigrations", "--check", "--dry-run")


class MultiDayAndVenueTests(TestCase):
    """Phase 5A: where the event is, and bookings that last several days."""

    def setUp(self):
        country = Country.objects.create(name="India")
        self.gj = State.objects.create(country=country, name="Gujarat")
        city = City.objects.create(state=self.gj, name="Vadodara")
        self.pro_user = User.objects.create_user("pro", password="pass12345", first_name="Pro", email="pro@example.com")
        UserProfile.objects.create(user=self.pro_user, role="photographer")
        self.ph = Photographer.objects.create(name="Multi Snap", bio="bio", specialities="wedding", city=city,
                                              price_per_day=5500, user=self.pro_user, status="approved")
        self.cust = User.objects.create_user("cust", password="pass12345", first_name="Cust", email="cust@example.com")
        self.cust2 = User.objects.create_user("cust2", password="pass12345")
        self.today = timezone.localdate()
        self.url = reverse("photographer_detail", args=[self.ph.slug])

    def d(self, n):
        return self.today + timedelta(days=n)

    def make(self, start, end=None, status="pending", user=None):
        return Booking.objects.create(user=user or self.cust, photographer=self.ph, event_date=self.d(start),
                                      end_date=self.d(end) if end is not None else None, status=status, price=5500, day_rate=5500)

    def post(self, **kw):
        self.client.login(username="cust", password="pass12345")
        return self.client.post(self.url, {**EVENT(), "event_date": self.d(20).isoformat(), **kw})

    # ---- the venue ----
    def test_venue_is_required(self):
        self.client.login(username="cust", password="pass12345")
        r = self.client.post(self.url, {"event_date": self.d(20).isoformat()})
        self.assertEqual(Booking.objects.count(), 0)
        for field in ("event_address", "event_city", "event_pincode", "event_state"):
            self.assertIn(field, r.context["form"].errors)

    def test_pincode_and_address_are_checked(self):
        for bad in ("12345", "abcdef", "012345", "1234567"):
            self.post(event_pincode=bad)
        self.post(event_address="Hall")
        self.assertEqual(Booking.objects.count(), 0)

    def test_venue_is_saved_and_cleaned(self):
        self.post(venue_name="Grand Palace", event_city="  ahmedabad ", start_time="18:30")
        b = Booking.objects.get()
        self.assertEqual((b.event_city, b.venue_name, b.event_pincode), ("Ahmedabad", "Grand Palace", "390001"))
        self.assertEqual(b.event_state, self.gj)
        self.assertEqual(b.place, "Grand Palace, Ahmedabad, Gujarat")
        self.assertEqual(b.start_time.strftime("%H:%M"), "18:30")

    def test_photographer_sees_the_address_customer_too(self):
        self.post(venue_name="Grand Palace")
        self.client.login(username="pro", password="pass12345")
        self.assertContains(self.client.get(reverse("pro_dashboard")), "Hall 1, Main Road, Alkapuri")
        self.client.login(username="cust", password="pass12345")
        self.assertContains(self.client.get(reverse("my_bookings")), "Grand Palace")

    def test_request_email_to_photographer_has_the_venue(self):
        mail.outbox.clear()
        self.post(venue_name="Grand Palace")
        to_pro = [m for m in mail.outbox if "pro@example.com" in m.to][0]
        self.assertIn("Grand Palace", to_pro.body)
        self.assertIn("390001", to_pro.body)

    def test_old_booking_without_venue_still_displays(self):
        b = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.d(10))
        self.assertEqual(b.place, "")
        self.client.login(username="cust", password="pass12345")
        self.assertEqual(self.client.get(reverse("my_bookings")).status_code, 200)

    # ---- one day and many days ----
    def test_one_day_booking_keeps_working(self):
        self.post()
        b = Booking.objects.get()
        self.assertEqual((b.end_date, b.days, b.price, b.day_rate), (b.event_date, 1, 5500, 5500))
        self.assertFalse(b.is_multi_day)

    def test_four_day_price_is_rate_times_days(self):
        self.post(end_date=self.d(23).isoformat())
        b = Booking.objects.get()
        self.assertEqual((b.days, b.day_rate, b.price, b.amount), (4, 5500, 22000, 22000))
        self.assertEqual(len(b.day_list()), 4)

    def test_package_rate_is_multiplied_too(self):
        Package.objects.create(photographer=self.ph, tier="standard", price=9000)
        pkg = Package.objects.get()
        self.post(end_date=self.d(21).isoformat(), package=pkg.pk)
        b = Booking.objects.get()
        self.assertEqual((b.days, b.day_rate, b.price), (2, 9000, 18000))

    def test_last_day_before_first_and_too_long_are_refused(self):
        self.post(end_date=self.d(19).isoformat())
        self.post(end_date=self.d(20 + availability.MAX_BOOKING_DAYS).isoformat())
        self.assertEqual(Booking.objects.count(), 0)
        self.post(end_date=self.d(19 + availability.MAX_BOOKING_DAYS).isoformat())  # exactly the maximum
        self.assertEqual(Booking.objects.get().days, availability.MAX_BOOKING_DAYS)

    def test_ten_day_booking(self):
        self.post(end_date=self.d(29).isoformat())
        self.assertEqual(Booking.objects.get().days, 10)

    def test_date_labels(self):
        b = Booking(event_date=date(2026, 12, 12), end_date=date(2026, 12, 15))
        self.assertEqual(b.date_label, "12 - 15 Dec 2026")
        b.end_date = date(2027, 1, 2)
        self.assertEqual(b.date_label, "12 Dec 2026 - 2 Jan 2027")
        b.end_date = date(2026, 12, 12)
        self.assertEqual(b.date_label, "12 Dec 2026")
        b.end_date = date(2026, 12, 30)
        b.event_date = date(2026, 11, 28)
        self.assertEqual(b.date_label, "28 Nov - 30 Dec 2026")

    # ---- availability across the range ----
    def test_accepted_range_blocks_every_day_in_it(self):
        self.make(10, 13, status="accepted")
        for n, free in ((9, True), (10, False), (11, False), (13, False), (14, True)):
            self.assertEqual(availability.is_free(self.ph, self.d(n)), free, n)
        self.assertNotIn(self.ph, availability.free_on(self.d(12)))
        self.assertIn(self.ph, availability.free_on(self.d(14)))

    def test_pending_range_does_not_block(self):
        self.make(10, 13, status="pending")
        self.assertTrue(availability.is_free(self.ph, self.d(11)))

    def test_form_refuses_a_range_that_overlaps_a_booking(self):
        self.make(22, 25, status="confirmed", user=self.cust2)
        self.post(end_date=self.d(23).isoformat())     # 20-23 touches 22-23
        self.post(event_date=self.d(24).isoformat())   # starts inside
        self.post(event_date=self.d(18).isoformat(), end_date=self.d(26).isoformat())  # swallows it
        self.assertEqual(Booking.objects.filter(user=self.cust).count(), 0)
        self.post(event_date=self.d(26).isoformat(), end_date=self.d(27).isoformat())  # right after
        self.post(event_date=self.d(19).isoformat(), end_date=self.d(21).isoformat())  # right before
        self.assertEqual(Booking.objects.filter(user=self.cust).count(), 2)

    def test_blocked_day_inside_the_range_refuses(self):
        BlockedDate.objects.create(photographer=self.ph, date=self.d(21))
        self.post(end_date=self.d(22).isoformat())
        self.assertEqual(Booking.objects.count(), 0)

    def test_same_customer_cannot_request_overlapping_twice(self):
        self.post(end_date=self.d(22).isoformat())
        self.post(event_date=self.d(22).isoformat(), end_date=self.d(24).isoformat())
        self.assertEqual(Booking.objects.count(), 1)

    def test_row_without_end_date_counts_as_one_day(self):
        b = self.make(10, status="accepted")
        Booking.objects.filter(pk=b.pk).update(end_date=None)  # like a row from before this feature
        self.assertFalse(availability.is_free(self.ph, self.d(10)))
        self.assertTrue(availability.is_free(self.ph, self.d(11)))
        self.assertEqual(Booking.objects.get(pk=b.pk).days, 1)

    def test_calendar_marks_all_days(self):
        self.make(3, 5, status="confirmed")
        cells = [c for m in availability.months(self.ph, 2) for w in m["weeks"] for c in w if c.get("in_month")]
        booked = {c["date"] for c in cells if c["state"] == "booked"}
        self.assertEqual(booked, {self.d(3), self.d(4), self.d(5)})

    # ---- accept ----
    def test_accepting_a_range_declines_overlapping_rivals_only(self):
        mine = self.make(10, 13)
        overlap = self.make(12, 14, user=self.cust2)
        after = self.make(14, 15, user=self.cust2)
        self.client.login(username="pro", password="pass12345")
        self.client.post(reverse("pro_booking_action", args=[mine.pk, "accept"]))
        for b in (mine, overlap, after):
            b.refresh_from_db()
        self.assertEqual((mine.status, overlap.status, after.status), ("accepted", "declined", "pending"))

    def test_cannot_accept_into_an_accepted_range(self):
        self.make(10, 13, status="accepted", user=self.cust2)
        other = self.make(12, 12)
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.accept(other)

    def test_cannot_accept_over_a_blocked_day_in_the_middle(self):
        BlockedDate.objects.create(photographer=self.ph, date=self.d(11))
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.accept(self.make(10, 12))

    # ---- completing ----
    def test_complete_only_after_the_last_day(self):
        b = self.make(-1, 2, status="confirmed")
        self.assertFalse(b.can_complete)
        b.end_date = self.d(0)
        self.assertTrue(b.can_complete)

    def test_dashboard_lists_a_running_multi_day_booking_as_upcoming(self):
        self.make(-1, 2, status="confirmed")
        self.client.login(username="pro", password="pass12345")
        r = self.client.get(reverse("pro_dashboard"))
        self.assertEqual(len(r.context["upcoming"]), 1)
        self.assertEqual(len(r.context["to_complete"]), 0)

    # ---- blocking days ----
    def test_block_days_refused_over_a_multi_day_booking(self):
        self.make(10, 13, status="confirmed")
        from .forms import BlockDatesForm
        form = BlockDatesForm({"start": self.d(12).isoformat(), "end": self.d(15).isoformat()}, photographer=self.ph)
        self.assertFalse(form.is_valid())
        self.assertIn("already have a booking", str(form.errors))

    # ---- rescheduling a multi-day booking ----
    def test_reschedule_keeps_the_length_and_may_overlap_its_own_days(self):
        b = self.make(30, 33, status="confirmed")
        req = booking_flow.request_reschedule(b, self.d(32), today=self.today)   # 32-35 overlaps its own 32-33
        self.assertEqual(req.new_end, self.d(35))
        self.assertIn("-", req.new_label)
        req = booking_flow.accept_reschedule(req, today=self.today)
        b.refresh_from_db()
        self.assertEqual((b.event_date, b.end_date, b.days), (self.d(32), self.d(35), 4))
        self.assertEqual(b.price, 5500)  # the price does not change

    def test_reschedule_refused_if_any_new_day_is_taken(self):
        b = self.make(30, 33, status="confirmed")
        self.make(36, 36, status="confirmed", user=self.cust2)
        with self.assertRaises(booking_flow.FlowError):
            booking_flow.request_reschedule(b, self.d(34), today=self.today)  # 34-37 hits the 36th
        self.assertTrue(booking_flow.request_reschedule(b, self.d(37), today=self.today))

    def test_reschedule_accept_declines_overlapping_rivals_and_requests(self):
        b = self.make(30, 31, status="confirmed")
        rival = self.make(41, 41, user=self.cust2)
        req = booking_flow.request_reschedule(b, self.d(40), today=self.today)   # 40-41
        other_b = self.make(50, 51, status="confirmed", user=self.cust2)
        other_req = booking_flow.request_reschedule(other_b, self.d(41), today=self.today)  # 41-42 overlaps 40-41
        booking_flow.accept_reschedule(req, today=self.today)
        rival.refresh_from_db()
        other_req.refresh_from_db()
        self.assertEqual(rival.status, "declined")
        self.assertEqual(other_req.status, "declined")

    def test_cancel_multi_day_refund_uses_first_day(self):
        b = self.make(10, 13, status="confirmed")
        Payment.objects.create(user=b.user, booking=b, amount=22000, payment_method="upi", status="completed")
        refund = booking_flow.cancel_by_customer(b, today=self.today)
        self.assertEqual(refund, 22000)  # 10 days before the first day: full refund

    def test_migration_style_defaults(self):
        b = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.d(5))
        self.assertEqual(b.end_date, b.event_date)


class TravelTests(TestCase):
    """Phase 5B: the customer pays travel, the photographer sets the price. India only."""

    def setUp(self):
        country = Country.objects.create(name="India")
        self.gj = State.objects.create(country=country, name="Gujarat")
        self.mh = State.objects.create(country=country, name="Maharashtra")
        self.vad = City.objects.create(state=self.gj, name="Vadodara")
        self.pro_user = User.objects.create_user("pro", password="pass12345", first_name="Pro", email="pro@example.com")
        UserProfile.objects.create(user=self.pro_user, role="photographer")
        self.ph = Photographer.objects.create(
            name="Travel Snap", bio="bio", specialities="wedding", city=self.vad, price_per_day=5500, user=self.pro_user,
            status="approved", travel_scope="india", travel_fee_state=2000, travel_fee_india=6000, stay_fee_per_day=1500)
        self.cust = User.objects.create_user("cust", password="pass12345", first_name="Cust", email="cust@example.com")
        self.today = timezone.localdate()
        self.url = reverse("photographer_detail", args=[self.ph.slug])

    def d(self, n):
        return (self.today + timedelta(days=n)).isoformat()

    def book(self, state=None, city="Vadodara", **kw):
        self.client.login(username="cust", password="pass12345")
        data = EVENT(event_state=(state or self.gj).pk, event_city=city, event_date=self.d(20), **kw)
        return self.client.post(self.url, data)

    # ---- the rule itself
    def test_zones(self):
        z = lambda st, c: travel.zone_for(self.ph, st, c)
        self.assertEqual(z(self.gj, "Vadodara"), "local")
        self.assertEqual(z(self.gj, "  vadodara "), "local")
        self.assertEqual(z(self.gj, "Surat"), "state")
        self.assertEqual(z(self.mh, "Vadodara"), "india")  # same name, other state: not local
        self.assertEqual(z(self.mh, "Mumbai"), "india")

    def test_local_is_free(self):
        q = travel.quote(self.ph, self.gj, "Vadodara", 5)
        self.assertEqual((q.total, q.allowed, q.lines), (0, True, []))

    def test_other_city_same_state(self):
        q = travel.quote(self.ph, self.gj, "Surat", 3)
        self.assertEqual((q.trip_fee, q.stay_fee, q.total), (2000, 4500, 6500))

    def test_other_state(self):
        q = travel.quote(self.ph, self.mh, "Mumbai", 4)
        self.assertEqual((q.trip_fee, q.stay_fee, q.total), (6000, 6000, 12000))
        self.assertEqual([label for label, _ in q.lines], ["Travel", "Stay and food (4 days)"])

    def test_zero_fees_mean_no_lines(self):
        self.ph.travel_fee_state = self.ph.stay_fee_per_day = 0
        q = travel.quote(self.ph, self.gj, "Surat", 2)
        self.assertEqual((q.total, q.lines, q.allowed), (0, [], True))

    def test_scope_city_and_state(self):
        self.ph.travel_scope = "city"
        self.assertFalse(travel.quote(self.ph, self.gj, "Surat").allowed)
        self.assertTrue(travel.quote(self.ph, self.gj, "Vadodara").allowed)
        self.ph.travel_scope = "state"
        self.assertTrue(travel.quote(self.ph, self.gj, "Surat").allowed)
        q = travel.quote(self.ph, self.mh, "Mumbai")
        self.assertFalse(q.allowed)
        self.assertIn("Gujarat", q.reason)

    def test_no_place_yet_quotes_nothing(self):
        self.assertEqual(travel.quote(self.ph, None, "Surat").total, 0)
        self.assertEqual(travel.quote(self.ph, self.gj, "  ").total, 0)

    def test_summary_includes_service_and_travel(self):
        s = travel.summary(self.ph, None, 3, self.mh, "Mumbai")
        self.assertEqual((s["service"], s["travel"], s["total"]), (16500, 10500, 27000))
        self.assertEqual(s["lines"][0], ("Full day: 5,500 x 3 days", 16500))

    # ---- a booking carries the charge
    def test_local_booking_has_no_travel(self):
        self.book()
        b = Booking.objects.get()
        self.assertEqual((b.travel_fee, b.travel_zone, b.amount), (0, "local", 5500))

    def test_outstation_booking_adds_travel_to_the_amount(self):
        self.book(state=self.mh, city="mumbai", end_date=self.d(22))   # 3 days
        b = Booking.objects.get()
        self.assertEqual((b.service_price, b.travel_fee, b.amount, b.travel_zone), (16500, 10500, 27000, "india"))
        self.assertEqual(b.event_city, "Mumbai")

    def test_the_price_shown_is_the_price_charged(self):
        self.client.login(username="cust", password="pass12345")
        shown = self.client.get(reverse("booking_quote", args=[self.ph.slug]), {
            "date": self.d(20), "end_date": self.d(22), "state": self.mh.pk, "city": "Mumbai"}).json()["total"]
        self.book(state=self.mh, city="Mumbai", end_date=self.d(22))
        self.assertEqual(Booking.objects.get().amount, shown)

    def test_scope_refuses_the_request(self):
        self.ph.travel_scope = "state"
        self.ph.save()
        r = self.book(state=self.mh, city="Mumbai")
        self.assertEqual(Booking.objects.count(), 0)
        self.assertIn("event_city", r.context["form"].errors)
        self.assertIn("only takes events within Gujarat", str(r.context["form"].errors))

    def test_changing_the_policy_later_does_not_change_the_booking(self):
        self.book(state=self.mh, city="Mumbai")
        self.ph.travel_fee_india = 99999
        self.ph.save()
        b = Booking.objects.get()
        self.assertEqual(b.travel_fee, 6000 + 1500)
        self.assertEqual(b.amount, 5500 + 7500)

    def test_old_booking_without_travel_is_unchanged(self):
        b = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.today + timedelta(days=9), price=7000)
        self.assertEqual((b.travel_fee, b.amount, b.service_price), (0, 7000, 7000))

    # ---- payment, emails
    def test_payment_charges_service_plus_travel(self):
        self.book(state=self.mh, city="Mumbai")
        b = Booking.objects.get()
        booking_flow.accept(b)
        self.client.login(username="cust", password="pass12345")
        page = self.client.get(reverse("payment", args=[b.pk]))
        self.assertContains(page, "Travel and stay")
        self.assertContains(page, "13000")
        self.client.post(reverse("payment", args=[b.pk]), {"payment_method": "upi"})
        self.assertEqual(Payment.objects.get().amount, 13000)

    def test_emails_show_the_breakdown(self):
        mail.outbox.clear()
        self.book(state=self.mh, city="Mumbai")
        to_pro = next(m for m in mail.outbox if "pro@example.com" in m.to)
        self.assertIn("Travel and stay", to_pro.body)
        self.assertIn("Rs.7500", to_pro.body)
        to_cust = next(m for m in mail.outbox if "cust@example.com" in m.to)
        self.assertIn("Rs.13000", to_cust.body)

    def test_dashboards_show_travel_included(self):
        self.book(state=self.mh, city="Mumbai")
        self.assertContains(self.client.get(reverse("my_bookings")), "incl. &#8377;7500 travel")
        self.client.login(username="pro", password="pass12345")
        self.assertContains(self.client.get(reverse("pro_dashboard")), "incl. &#8377;7500 travel")

    # ---- the live price box endpoint
    def test_quote_endpoint(self):
        r = self.client.get(reverse("booking_quote", args=[self.ph.slug]),
                            {"date": self.d(20), "end_date": self.d(21), "state": self.gj.pk, "city": "Surat"})
        j = r.json()
        self.assertEqual((j["total"], j["allowed"], j["has_place"], j["days"]), (11000 + 2000 + 3000, True, True, 2))
        self.assertEqual(j["lines"][0]["amount"], 11000)

    def test_quote_endpoint_without_place_and_with_garbage(self):
        j = self.client.get(reverse("booking_quote", args=[self.ph.slug]), {"date": "nonsense", "state": "x", "package": "zzz"}).json()
        self.assertEqual((j["total"], j["has_place"], j["days"]), (5500, False, 1))

    def test_quote_endpoint_reports_refusal_and_caps_days(self):
        self.ph.travel_scope = "city"
        self.ph.save()
        j = self.client.get(reverse("booking_quote", args=[self.ph.slug]),
                            {"date": self.d(5), "end_date": self.d(500), "state": self.gj.pk, "city": "Surat"}).json()
        self.assertFalse(j["allowed"])
        self.assertEqual(j["days"], availability.MAX_BOOKING_DAYS)

    def test_quote_endpoint_ignores_another_photographers_package_and_hidden_profiles(self):
        other = Photographer.objects.create(name="Other", bio="b", specialities="wedding", city=self.vad, price_per_day=100000,
                                            status="approved")
        pkg = Package.objects.create(photographer=other, tier="premium", price=90000)
        j = self.client.get(reverse("booking_quote", args=[self.ph.slug]), {"package": pkg.pk}).json()
        self.assertEqual(j["total"], 5500)
        self.ph.status = "pending"
        self.ph.save()
        self.assertEqual(self.client.get(reverse("booking_quote", args=[self.ph.slug])).status_code, 404)

    def test_quote_uses_the_chosen_package(self):
        pkg = Package.objects.create(photographer=self.ph, tier="premium", price=12000)
        j = self.client.get(reverse("booking_quote", args=[self.ph.slug]), {"package": pkg.pk}).json()
        self.assertEqual(j["total"], 12000)

    # ---- the photographer's page
    def test_travel_page_permissions(self):
        self.assertEqual(self.client.get(reverse("pro_travel")).status_code, 302)
        self.client.login(username="cust", password="pass12345")
        self.assertEqual(self.client.get(reverse("pro_travel")).status_code, 302)

    def test_photographer_saves_travel_policy(self):
        self.client.login(username="pro", password="pass12345")
        self.assertContains(self.client.get(reverse("pro_travel")), "What customers see now")
        r = self.client.post(reverse("pro_travel"), {"travel_scope": "state", "travel_fee_state": 3000,
                                                     "travel_fee_india": 0, "stay_fee_per_day": 800})
        self.assertEqual(r.status_code, 302)
        self.ph.refresh_from_db()
        self.assertEqual((self.ph.travel_scope, self.ph.travel_fee_state, self.ph.stay_fee_per_day), ("state", 3000, 800))

    def test_travel_policy_rejects_silly_values(self):
        self.client.login(username="pro", password="pass12345")
        for bad in ({"travel_fee_state": -1}, {"travel_fee_india": 9999999}, {"stay_fee_per_day": "abc"}, {"travel_scope": "world"}):
            data = {"travel_scope": "india", "travel_fee_state": 0, "travel_fee_india": 0, "stay_fee_per_day": 0, **bad}
            self.assertEqual(self.client.post(reverse("pro_travel"), data).status_code, 200)
        self.ph.refresh_from_db()
        self.assertEqual(self.ph.travel_fee_india, 6000)

    def test_public_profile_shows_the_policy(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("No travel charge in Vadodara.", html)
        self.assertIn("Rs.6,000 travel plus Rs.1,500 per day", html)
        self.ph.travel_scope = "city"
        self.ph.save()
        self.assertIn("Takes events in Vadodara only.", self.client.get(self.url).content.decode())

    def test_admin_photographer_page_opens(self):
        admin = User.objects.create_superuser("boss", "b@example.com", "pass12345")
        self.client.login(username="boss", password="pass12345")
        self.assertEqual(self.client.get(reverse("admin:photopholio_photographer_change", args=[self.ph.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:photopholio_booking_changelist")).status_code, 200)


def _complete_and_confirm(b):
    """Finish a shoot and have the customer confirm it, so the payout becomes ready (the old one-step behaviour)."""
    from django.utils import timezone
    booking_flow.mark_completed(b)
    b.refresh_from_db()
    b.customer_confirmed_at = timezone.now()
    b.save(update_fields=["customer_confirmed_at"])
    money.release_if_due(b)
    b.refresh_from_db()


class MoneyTests(TestCase):
    """Phase 5C: commission, holding the money, payouts and what the admin sees."""

    def setUp(self):
        country = Country.objects.create(name="India")
        self.gj = State.objects.create(country=country, name="Gujarat")
        self.mh = State.objects.create(country=country, name="Maharashtra")
        city = City.objects.create(state=self.gj, name="Vadodara")
        self.pro_user = User.objects.create_user("pro", password="pass12345", first_name="Pro", email="pro@example.com")
        UserProfile.objects.create(user=self.pro_user, role="photographer")
        self.ph = Photographer.objects.create(name="Money Snap", bio="bio", specialities="wedding", city=city, price_per_day=5500,
                                              user=self.pro_user, status="approved", payout_details="money@upi")
        self.cust = User.objects.create_user("cust", password="pass12345", first_name="Cust", email="cust@example.com")
        self.admin = User.objects.create_superuser("boss", "b@example.com", "pass12345")
        self.today = timezone.localdate()

    def paid(self, service=22000, travel=0, days_ahead=20, length=1, status="confirmed", ph=None):
        b = Booking.objects.create(user=self.cust, photographer=ph or self.ph, event_date=self.today + timedelta(days=days_ahead),
                                   end_date=self.today + timedelta(days=days_ahead + length - 1), status=status,
                                   price=service, day_rate=service // length, travel_fee=travel)
        Payment.objects.create(user=self.cust, booking=b, amount=service + travel, payment_method="upi", status="completed")
        money.on_paid(b)
        b.refresh_from_db()
        return b

    # ---- the split
    def test_commission_on_the_service_price(self):
        b = self.paid(22000)  # the 4-day example
        self.assertEqual((b.commission_percent, b.platform_fee, b.payout_amount, b.payout_status), (10, 2200, 19800, "held"))

    def test_travel_is_passed_on_without_commission(self):
        b = self.paid(22000, travel=6000)
        self.assertEqual((b.platform_fee, b.payout_amount), (2200, 25800))

    def test_own_percent_and_zero_percent(self):
        self.ph.commission_percent = 15
        self.ph.save()
        self.assertEqual(self.paid(20000).platform_fee, 3000)
        self.ph.commission_percent = 0
        self.ph.save()
        b = self.paid(20000, days_ahead=40)
        self.assertEqual((b.platform_fee, b.payout_amount), (0, 20000))

    @override_settings(PLATFORM_COMMISSION_PERCENT=20)
    def test_site_default_comes_from_settings(self):
        self.assertEqual(self.paid(10000).platform_fee, 2000)

    def test_split_never_goes_negative_or_over(self):
        for retained, service, total, pct in ((0, 100, 100, 10), (100, 1000, 100, 10), (1, 1, 1, 50), (999, 333, 999, 7)):
            s = money._split(retained, service, total, pct)
            self.assertTrue(0 <= s.fee <= retained and s.fee + s.payout == retained)

    def test_preview_before_payment_and_earnings_property(self):
        b = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.today + timedelta(days=9), price=10000)
        self.assertEqual(tuple(b.earnings), (10, 1000, 9000))
        paid = self.paid(10000, days_ahead=30)
        self.assertEqual(tuple(paid.earnings), (10, 1000, 9000))

    def test_changing_the_percent_later_keeps_the_booking_split(self):
        b = self.paid(10000, days_ahead=-1)
        self.ph.commission_percent = 40
        self.ph.save()
        money.on_completed(b)
        b.refresh_from_db()
        self.assertEqual((b.platform_fee, b.payout_status), (1000, "held"))  # held until the customer confirms
        _complete_and_confirm(b)
        self.assertEqual((b.platform_fee, b.payout_status), (1000, "ready"))

    # ---- the life of the money
    def test_paying_through_the_site_starts_holding(self):
        b = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.today + timedelta(days=12), status="accepted",
                                   accepted_at=timezone.now(), price=8000, day_rate=8000, travel_fee=2000)
        self.client.login(username="cust", password="pass12345")
        self.client.post(reverse("payment", args=[b.pk]), {"payment_method": "upi"})
        b.refresh_from_db()
        self.assertEqual((b.payout_status, b.platform_fee, b.payout_amount), ("held", 800, 9200))

    def test_completing_makes_it_ready(self):
        b = self.paid(10000, days_ahead=-1)
        _complete_and_confirm(b)
        b.refresh_from_db()
        self.assertEqual(b.payout_status, "ready")

    def test_paid_before_this_feature_gets_a_split_on_completion(self):
        b = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.today - timedelta(days=1), status="confirmed", price=10000)
        Payment.objects.create(user=self.cust, booking=b, amount=10000, payment_method="upi", status="completed")
        _complete_and_confirm(b)
        b.refresh_from_db()
        self.assertEqual((b.payout_status, b.platform_fee), ("ready", 1000))

    def test_customer_cancels_with_half_refund_shares_the_loss(self):
        b = self.paid(20000, days_ahead=1)  # 1 day before: 50% refund
        booking_flow.cancel_by_customer(b, today=self.today)
        b.refresh_from_db()
        self.assertEqual((b.payout_status, b.platform_fee, b.payout_amount), ("ready", 1000, 9000))

    def test_full_refunds_leave_nothing_to_pay(self):
        b = self.paid(20000, days_ahead=10)
        booking_flow.cancel_by_customer(b, today=self.today)
        b.refresh_from_db()
        self.assertEqual((b.payout_status, b.platform_fee, b.payout_amount), ("void", 0, 0))
        b2 = self.paid(20000, days_ahead=30)
        booking_flow.cancel_by_photographer(b2, "Ill")
        b2.refresh_from_db()
        self.assertEqual((b2.payout_status, b2.payout_amount), ("void", 0))

    def test_travel_is_refunded_proportionally(self):
        b = self.paid(20000, travel=5000, days_ahead=1)  # paid 25000, half back = 12500 kept
        booking_flow.cancel_by_customer(b, today=self.today)
        b.refresh_from_db()
        # kept 12500, service share 10000, commission 1000, photographer 11500
        self.assertEqual((b.platform_fee, b.payout_amount), (1000, 11500))

    def test_admin_refund_after_completion_before_and_after_payout(self):
        b = self.paid(10000, days_ahead=-1)
        _complete_and_confirm(b)
        booking_flow.admin_refund_in_full(b)
        b.refresh_from_db()
        self.assertEqual(b.payout_status, "void")
        c = self.paid(10000, days_ahead=-3)
        _complete_and_confirm(c)
        c.refresh_from_db()
        money.mark_paid(c, "UTR123456")
        c.refresh_from_db()
        booking_flow.admin_refund_in_full(c)
        c.refresh_from_db()
        self.assertEqual(c.payout_status, "recover")
        self.assertIn("Recover Rs.9000", c.payout_note)
        money.mark_recovered(c, "got it back")
        c.refresh_from_db()
        self.assertEqual(c.payout_status, "void")

    def test_unpaid_cancellation_touches_no_money(self):
        b = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.today + timedelta(days=9), status="accepted",
                                   accepted_at=timezone.now(), price=5000)
        booking_flow.cancel_by_customer(b, today=self.today)
        b.refresh_from_db()
        self.assertEqual((b.payout_status, b.platform_fee, b.payout_amount), ("", 0, 0))

    # ---- paying the photographer
    def ready(self, **kw):
        b = self.paid(days_ahead=-1, **kw)
        _complete_and_confirm(b)
        b.refresh_from_db()
        return b

    def test_mark_paid_needs_ready_and_a_reference(self):
        held = self.paid(10000)
        with self.assertRaises(booking_flow.FlowError):
            money.mark_paid(held, "UTR111222")
        b = self.ready()
        for bad in ("", "  ", "ab", "x" * 61):
            with self.assertRaises(booking_flow.FlowError):
                money.mark_paid(b, bad)
        mail.outbox.clear()
        money.mark_paid(b, "  UTR  998877 ")
        b.refresh_from_db()
        self.assertEqual((b.payout_status, b.payout_reference), ("paid", "UTR 998877"))
        self.assertIsNotNone(b.payout_paid_at)
        sent = next(m for m in mail.outbox if "pro@example.com" in m.to)
        self.assertIn("UTR 998877", sent.body)
        with self.assertRaises(booking_flow.FlowError):
            money.mark_paid(b, "UTR000111")  # no double payment

    # ---- photographer pages
    def test_earnings_page(self):
        b = self.ready(service=10000, travel=2000)
        held = self.paid(5000, days_ahead=40)
        self.client.login(username="pro", password="pass12345")
        r = self.client.get(reverse("pro_earnings"))
        self.assertContains(r, "10%")
        self.assertEqual((r.context["totals"]["ready"]["money"], r.context["totals"]["held"]["money"]), (11000, 4500))
        self.assertNotContains(r, "Add where to send")
        money.mark_paid(b, "UTR556677")
        self.assertContains(self.client.get(reverse("pro_earnings")), "UTR556677")

    def test_earnings_page_permissions_and_missing_payout_warning(self):
        self.assertEqual(self.client.get(reverse("pro_earnings")).status_code, 302)
        self.client.login(username="cust", password="pass12345")
        self.assertEqual(self.client.get(reverse("pro_earnings")).status_code, 302)
        self.ph.payout_details = ""
        self.ph.save()
        self.client.login(username="pro", password="pass12345")
        self.assertContains(self.client.get(reverse("pro_earnings")), "Add where to send")

    def test_dashboard_shows_what_you_earn(self):
        Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.today + timedelta(days=9), price=10000)
        self.client.login(username="pro", password="pass12345")
        self.assertContains(self.client.get(reverse("pro_dashboard")), "You earn &#8377;9000 after 10% commission")

    def test_profile_form_saves_payout_details(self):
        self.client.login(username="pro", password="pass12345")
        data = {"name": "Money Snap", "specialities": "wedding", "state": self.gj.pk, "city_name": "Vadodara", "experience_years": 3,
                "price_per_day": 5500, "bio": "b" * 60, "payout_details": "new@upi", "is_available": "on"}
        self.client.post(reverse("pro_profile"), data)
        self.ph.refresh_from_db()
        self.assertEqual(self.ph.payout_details, "new@upi")

    def test_policy_page_explains_fees(self):
        self.assertContains(self.client.get(reverse("cancellation_policy")), "no extra fee for you")

    # ---- admin
    def test_payouts_page_lists_and_pays(self):
        b = self.ready(service=10000)
        self.client.login(username="boss", password="pass12345")
        page = self.client.get(reverse("admin:photopholio_payouts"))
        self.assertContains(page, "money@upi")
        self.assertEqual(page.context["ready_total"], 9000)
        r = self.client.post(reverse("admin:photopholio_payout_pay", args=[b.pk]), {"reference": "UTR424242"})
        self.assertEqual(r.status_code, 302)
        b.refresh_from_db()
        self.assertEqual(b.payout_status, "paid")
        self.assertContains(self.client.get(reverse("admin:photopholio_payouts")), "UTR424242")

    def test_payouts_pay_rejects_bad_input_and_get(self):
        b = self.ready()
        self.client.login(username="boss", password="pass12345")
        self.client.post(reverse("admin:photopholio_payout_pay", args=[b.pk]), {"reference": ""})
        self.assertEqual(self.client.get(reverse("admin:photopholio_payout_pay", args=[b.pk])).status_code, 302)
        b.refresh_from_db()
        self.assertEqual(b.payout_status, "ready")

    def test_payouts_pages_need_permission(self):
        b = self.ready()
        staff = User.objects.create_user("clerk", password="pass12345", is_staff=True)
        for who in (None, "cust", "pro", "clerk"):
            c = self.client
            c.logout()
            if who:
                c.login(username=who, password="pass12345")
            for name, args in (("photopholio_payouts", []), ("photopholio_payouts_csv", []), ("photopholio_payout_pay", [b.pk])):
                r = c.post(reverse(f"admin:{name}", args=args), {"reference": "UTR999888"})
                self.assertIn(r.status_code, (302, 403), (who, name))
                if r.status_code == 302:
                    self.assertIn("login", r["Location"])
        b.refresh_from_db()
        self.assertEqual(b.payout_status, "ready")

    def test_csv_export_and_formula_safety(self):
        evil = Photographer.objects.create(name="=SUM(1+1)", bio="b", specialities="wedding", city=self.ph.city, price_per_day=5000, status="approved")
        self.ready()
        self.paid(3000, ph=evil, days_ahead=50)
        self.client.login(username="boss", password="pass12345")
        r = self.client.get(reverse("admin:photopholio_payouts_csv"))
        self.assertEqual(r["Content-Type"], "text/csv")
        body = r.content.decode()
        self.assertIn("Photographer gets", body)
        self.assertIn("'=SUM(1+1)", body)
        self.assertNotIn(",=SUM", body)
        self.assertEqual(len(body.strip().splitlines()), 3)

    def test_completed_action_in_admin_makes_it_ready(self):
        b = self.paid(10000, days_ahead=-1)
        self.client.login(username="boss", password="pass12345")
        self.client.post(reverse("admin:photopholio_booking_changelist"), {"action": "mark_completed", "_selected_action": [b.pk]})
        b.refresh_from_db()
        self.assertEqual((b.status, b.payout_status), ("completed", "ready"))

    def test_admin_pages_open(self):
        self.client.login(username="boss", password="pass12345")
        self.assertEqual(self.client.get(reverse("admin:photopholio_photographer_change", args=[self.ph.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:photopholio_booking_changelist")).status_code, 200)

    # ---- analytics
    def test_analytics_money_cards(self):
        paid = self.ready(service=10000, travel=2000)
        money.mark_paid(paid, "UTR101010")
        self.ready(service=20000)
        self.paid(5000, days_ahead=30)
        multi = self.paid(8000, days_ahead=40, length=4)
        data = analytics.dashboard_data()
        m, e = data["money"], data["events"]
        self.assertEqual((m["paid_out"], m["ready"], m["held"]), (11000, 18000, 4500 + 7200))
        self.assertEqual((m["commission_earned"], m["commission_pending"]), (1000 + 2000, 500 + 800))
        self.assertEqual(m["travel_collected"], 2000)
        self.assertEqual((e["paid_bookings"], e["multi_day"]), (4, 1))
        self.assertEqual(e["avg_days"], round((1 + 1 + 1 + 4) / 4, 1))
        self.assertEqual(len(data["chart"]["commission"]), analytics.MONTHS)
        self.client.login(username="boss", password="pass12345")
        page = self.client.get(reverse("admin:photopholio_analytics"))
        self.assertContains(page, "PhotoPholio commission earned")
        self.assertContains(page, "Where the money goes")

    def test_analytics_with_no_money_at_all(self):
        data = analytics.dashboard_data()
        self.assertEqual((data["money"]["commission_earned"], data["events"]["avg_days"], data["events"]["multi_day_pct"]), (0, 0, None))
        self.client.login(username="boss", password="pass12345")
        self.assertEqual(self.client.get(reverse("admin:photopholio_analytics")).status_code, 200)

    def test_top_event_cities(self):
        for city in ("Surat", "Surat", "Pune"):
            Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.today + timedelta(days=9), event_city=city)
        top = analytics.dashboard_data()["events"]["top_cities"]
        self.assertEqual((top[0]["event_city"], top[0]["n"]), ("Surat", 2))


class SignupThenLoginTests(TestCase):
    """Creating an account does not log you in: signup -> login page (username filled in) -> home page (customers) or Dashboard (photographers)."""

    def signup(self, role, username="newperson"):
        return self.client.post(reverse("register"), {
            "role": role, "first_name": "New", "username": username, "email": f"{username}@example.com",
            "password1": "Str0ng-pass-99", "password2": "Str0ng-pass-99"})

    def test_signup_does_not_log_you_in(self):
        r = self.signup("customer")
        self.assertRedirects(r, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(User.objects.filter(username="newperson").exists())

    def test_login_page_after_signup_has_the_username_and_a_message(self):
        self.signup("customer")
        page = self.client.get(reverse("login"))
        self.assertContains(page, 'value="newperson"')
        self.assertContains(page, "Your account is created")
        self.assertNotContains(page, "Str0ng-pass-99")
        # only for the next visit
        self.assertNotContains(self.client.get(reverse("login")), 'value="newperson"')

    def test_customer_logs_in_and_lands_on_the_home_page(self):
        self.signup("customer")
        r = self.client.post(reverse("login"), {"username": "newperson", "password": "Str0ng-pass-99"})
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("my_bookings")).status_code, 200)

    def test_photographer_signup_goes_to_the_photographer_login_then_the_dashboard(self):
        r = self.signup("photographer", "lensperson")
        self.assertRedirects(r, reverse("pro_login"), fetch_redirect_response=False)
        page = self.client.get(reverse("pro_login"))
        self.assertContains(page, 'value="lensperson"')
        self.assertContains(page, "Please log in to set up your profile")
        r = self.client.post(reverse("pro_login"), {"username": "lensperson", "password": "Str0ng-pass-99"})
        self.assertRedirects(r, reverse("pro_dashboard"), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("pro_dashboard")), "Create my profile")

    def test_next_page_still_wins_after_signup(self):
        self.signup("customer")
        r = self.client.post(reverse("login") + "?next=/contact/", {"username": "newperson", "password": "Str0ng-pass-99"})
        self.assertRedirects(r, "/contact/", fetch_redirect_response=False)

    def test_invalid_signup_stays_on_the_form(self):
        r = self.client.post(reverse("register"), {"role": "customer", "username": "x"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("just_registered", self.client.session)

    def test_password_field_gets_the_focus_when_username_is_filled(self):
        self.signup("customer")
        html = self.client.get(reverse("login")).content.decode()
        password_tag = html[html.index('name="password"') - 120: html.index('name="password"') + 120]
        self.assertIn("autofocus", password_tag)


class TrackingTests(TestCase):
    """Phase 5D: arrived / start / finish, customer confirmation, 48h auto-confirm, reported problems."""

    def setUp(self):
        country = Country.objects.create(name="India")
        gj = State.objects.create(country=country, name="Gujarat")
        city = City.objects.create(state=gj, name="Vadodara")
        self.pro_user = User.objects.create_user("pro", password="pass12345", first_name="Pro", email="pro@example.com")
        UserProfile.objects.create(user=self.pro_user, role="photographer")
        self.ph = Photographer.objects.create(name="Track Snap", bio="bio", specialities="wedding", city=city, price_per_day=5500,
                                              user=self.pro_user, status="approved")
        self.cust = User.objects.create_user("cust", password="pass12345", first_name="Cust", email="cust@example.com")
        self.other = User.objects.create_user("other", password="pass12345", email="o@example.com")
        self.admin = User.objects.create_superuser("boss", "b@example.com", "pass12345")
        self.today = timezone.localdate()

    def shoot(self, start=0, length=1, price=10000):
        b = Booking.objects.create(user=self.cust, photographer=self.ph, event_date=self.today + timedelta(days=start),
                                   end_date=self.today + timedelta(days=start + length - 1), status="confirmed",
                                   price=price, day_rate=price // length)
        Payment.objects.create(user=self.cust, booking=b, amount=price, payment_method="upi", status="completed")
        money.on_paid(b)
        b.refresh_from_db()
        return b

    def tap(self, b, *steps, today=None):
        for s in steps:
            tracking.photographer_step(b.pk, self.pro_user, s, today=today)
        b.refresh_from_db()

    # ---- photographer taps
    def test_full_single_day_flow_completes_and_waits_for_customer(self):
        b = self.shoot()
        self.tap(b, "arrived", "start", "finish")
        self.assertEqual((b.status, b.payout_status), ("completed", "held"))
        self.assertIsNotNone(b.completed_at)
        self.assertEqual(ShootDay.objects.filter(booking=b).count(), 1)
        self.assertEqual([e.action for e in ShootEvent.objects.filter(booking=b).order_by("pk")][:3], ["arrived", "started", "finished"])
        tracking.confirm_completed(b.pk, self.cust)
        b.refresh_from_db()
        self.assertEqual((b.payout_status, b.customer_confirmed_by_customer), ("ready", True))

    def test_order_and_duplicates_are_refused(self):
        b = self.shoot()
        for step in ("start", "finish"):
            with self.assertRaises(booking_flow.FlowError):
                self.tap(b, step)
        self.tap(b, "arrived")
        with self.assertRaises(booking_flow.FlowError):
            self.tap(b, "arrived")
        with self.assertRaises(booking_flow.FlowError):
            self.tap(b, "finish")
        self.tap(b, "start")
        with self.assertRaises(booking_flow.FlowError):
            self.tap(b, "start")
        with self.assertRaises(booking_flow.FlowError):
            self.tap(b, "nonsense")

    def test_only_on_the_event_day_and_only_the_owner(self):
        future = self.shoot(start=3)
        with self.assertRaises(booking_flow.FlowError):
            self.tap(future, "arrived")
        b = self.shoot(start=0)
        with self.assertRaises(booking_flow.FlowError):
            tracking.photographer_step(b.pk, self.other, "arrived")
        b.status = "accepted"
        b.save()
        with self.assertRaises(booking_flow.FlowError):
            self.tap(b, "arrived")

    def test_multi_day_completes_only_after_the_last_day(self):
        b = self.shoot(start=-1, length=2, price=20000)
        self.tap(b, "arrived", "start", "finish", today=self.today - timedelta(days=1))
        self.assertEqual(b.status, "confirmed")
        self.tap(b, "arrived", "start", "finish")
        self.assertEqual(b.status, "completed")
        self.assertEqual(ShootDay.objects.filter(booking=b).count(), 2)

    # ---- customer
    def test_customer_arrival_check_and_permissions(self):
        b = self.shoot()
        with self.assertRaises(booking_flow.FlowError):
            tracking.confirm_arrival(b.pk, self.cust, self.today)
        self.tap(b, "arrived")
        with self.assertRaises(booking_flow.FlowError):
            tracking.confirm_arrival(b.pk, self.other, self.today)
        tracking.confirm_arrival(b.pk, self.cust, self.today)
        with self.assertRaises(booking_flow.FlowError):
            tracking.confirm_arrival(b.pk, self.cust, self.today)
        self.assertTrue(ShootDay.objects.get(booking=b).customer_seen_at)

    def test_confirm_needs_completed_and_is_once_only(self):
        b = self.shoot()
        with self.assertRaises(booking_flow.FlowError):
            tracking.confirm_completed(b.pk, self.cust)
        self.tap(b, "arrived", "start", "finish")
        with self.assertRaises(booking_flow.FlowError):
            tracking.confirm_completed(b.pk, self.other)
        tracking.confirm_completed(b.pk, self.cust)
        with self.assertRaises(booking_flow.FlowError):
            tracking.confirm_completed(b.pk, self.cust)

    def test_auto_confirm_after_48_hours_only(self):
        b = self.shoot()
        self.tap(b, "arrived", "start", "finish")
        self.assertEqual(tracking.auto_confirm_due(now=timezone.now() + timedelta(hours=47)), 0)
        self.assertEqual(tracking.auto_confirm_due(now=timezone.now() + timedelta(hours=49)), 1)
        b.refresh_from_db()
        self.assertEqual((b.payout_status, b.customer_confirmed_by_customer), ("ready", False))
        self.assertEqual(tracking.auto_confirm_due(now=timezone.now() + timedelta(hours=99)), 0)

    def test_expire_stale_runs_the_auto_confirm(self):
        b = self.shoot()
        self.tap(b, "arrived", "start", "finish")
        Booking.objects.filter(pk=b.pk).update(completed_at=timezone.now() - timedelta(hours=60))
        booking_flow.expire_stale()
        b.refresh_from_db()
        self.assertEqual(b.payout_status, "ready")

    # ---- reported problems
    def test_report_validation_and_freeze(self):
        b = self.shoot()
        for kind, text in (("bad", "x" * 20), ("not_arrived", "short"), ("not_arrived", "x" * 1001)):
            with self.assertRaises(booking_flow.FlowError):
                tracking.report_problem(b.pk, self.cust, kind, text)
        with self.assertRaises(booking_flow.FlowError):
            tracking.report_problem(b.pk, self.other, "not_arrived", "The photographer never came at all.")
        tracking.report_problem(b.pk, self.cust, "not_arrived", "The photographer never came at all.")
        b.refresh_from_db()
        self.assertEqual(b.dispute_status, "open")
        with self.assertRaises(booking_flow.FlowError):
            tracking.report_problem(b.pk, self.cust, "other", "Trying a second report here.")
        self.tap(b, "arrived", "start", "finish")  # photographer finishes anyway
        with self.assertRaises(booking_flow.FlowError):
            tracking.confirm_completed(b.pk, self.cust)
        self.assertEqual(tracking.auto_confirm_due(now=timezone.now() + timedelta(days=5)), 0)
        b.refresh_from_db()
        self.assertEqual(b.payout_status, "held")
        with self.assertRaises(booking_flow.FlowError):
            money.mark_paid(b, "UTR123456")

    def test_cannot_report_before_the_event(self):
        b = self.shoot(start=5)
        with self.assertRaises(booking_flow.FlowError):
            tracking.report_problem(b.pk, self.cust, "not_arrived", "It is not even the day yet.")

    def test_admin_decisions_need_a_note_and_an_open_report(self):
        b = self.shoot()
        with self.assertRaises(booking_flow.FlowError):
            tracking.resolve_dispute(b.pk, self.admin, "pay", "long enough note")
        tracking.report_problem(b.pk, self.cust, "quality", "The photos were far too blurry to use.")
        with self.assertRaises(booking_flow.FlowError):
            tracking.resolve_dispute(b.pk, self.admin, "pay", "")
        with self.assertRaises(booking_flow.FlowError):
            tracking.resolve_dispute(b.pk, self.admin, "bogus", "long enough note")

    def test_admin_pay_releases_the_money(self):
        b = self.shoot()
        tracking.report_problem(b.pk, self.cust, "quality", "The photos were far too blurry to use.")
        tracking.resolve_dispute(b.pk, self.admin, "pay", "Photographer showed the delivered photos.")
        b.refresh_from_db()
        self.assertEqual((b.dispute_status, b.dispute_outcome, b.status, b.payout_status), ("resolved", "pay", "completed", "ready"))
        self.assertTrue(ShootEvent.objects.filter(booking=b, action="decided_pay").exists())

    def test_admin_dismiss_releases_the_money(self):
        b = self.shoot()
        tracking.report_problem(b.pk, self.cust, "other", "Something felt off about the whole shoot.")
        tracking.resolve_dispute(b.pk, self.admin, "dismiss", "No evidence of a problem.")
        b.refresh_from_db()
        self.assertEqual((b.dispute_outcome, b.payout_status), ("dismiss", "ready"))

    def test_admin_refund_returns_the_money_and_never_pays_out(self):
        b = self.shoot()
        tracking.report_problem(b.pk, self.cust, "not_arrived", "The photographer never came at all.")
        tracking.resolve_dispute(b.pk, self.admin, "refund", "Photographer did not show up.")
        b.refresh_from_db()
        self.assertEqual((b.status, b.dispute_outcome, b.payout_status), ("cancelled", "refund", "void"))
        self.assertEqual(b.paid_payment.refund_status if b.paid_payment else "pending", b.paid_payment.refund_status if b.paid_payment else "pending")
        self.assertEqual(Payment.objects.get(booking=b).refund_status, "pending")

    def test_old_completed_booking_without_completed_at_still_pays(self):
        b = self.shoot(start=-2)
        Booking.objects.filter(pk=b.pk).update(status="completed", completed_at=None)
        b.refresh_from_db()
        self.assertTrue(money.release_if_due(b))

    # ---- pages
    def test_pages_render_and_actions_work(self):
        b = self.shoot()
        self.client.login(username="pro", password="pass12345")
        self.assertContains(self.client.get(reverse("pro_dashboard")), reverse("pro_shoot", args=[b.pk, "arrived"]))
        self.client.post(reverse("pro_shoot", args=[b.pk, "arrived"]))
        self.assertTrue(ShootDay.objects.get(booking=b).arrived_at)
        self.assertEqual(self.client.get(reverse("pro_shoot", args=[b.pk, "arrived"])).status_code, 405)
        self.assertEqual(self.client.post(reverse("pro_shoot", args=[b.pk, "fly"])).status_code, 404)
        self.client.logout()
        self.client.login(username="cust", password="pass12345")
        self.assertEqual(self.client.get(reverse("my_bookings")).status_code, 200)
        self.client.post(reverse("booking_arrival_seen", args=[b.pk, self.today.isoformat()]))
        self.assertTrue(ShootDay.objects.get(booking=b).customer_seen_at)
        self.assertEqual(self.client.get(reverse("booking_report", args=[b.pk])).status_code, 200)
        self.client.post(reverse("booking_report", args=[b.pk]), {"kind": "quality", "text": "The photos were far too blurry to use."})
        b.refresh_from_db()
        self.assertEqual(b.dispute_status, "open")
        self.assertContains(self.client.get(reverse("my_bookings")), "reported")
        self.client.logout()
        self.client.login(username="other", password="pass12345")
        self.assertEqual(self.client.get(reverse("booking_report", args=[b.pk])).status_code, 404)

    def test_admin_disputes_page_and_resolve_view(self):
        b = self.shoot()
        tracking.report_problem(b.pk, self.cust, "not_arrived", "The photographer never came at all.")
        self.client.login(username="cust", password="pass12345")
        self.assertIn(self.client.get(reverse("admin:photopholio_disputes")).status_code, (302, 403))
        self.client.logout()
        self.client.login(username="boss", password="pass12345")
        page = self.client.get(reverse("admin:photopholio_disputes"))
        self.assertContains(page, "never came")
        self.client.post(reverse("admin:photopholio_dispute_resolve", args=[b.pk]), {"outcome": "dismiss", "note": "Photos were delivered."})
        b.refresh_from_db()
        self.assertEqual(b.dispute_outcome, "dismiss")
        self.assertEqual(self.client.get(reverse("admin:photopholio_booking_change", args=[b.pk])).status_code, 200)


class BrevoBackendTests(TestCase):
    """Email over HTTPS (for hosts that block SMTP). The network call is faked."""

    def _send(self, **kw):
        from django.core.mail import EmailMultiAlternatives
        m = EmailMultiAlternatives("Hello", "plain body", "PhotoPholio <me@example.com>", ["you@example.com"])
        m.attach_alternative("<p>html body</p>", "text/html")
        from photopholio.email_backends import BrevoEmailBackend
        return BrevoEmailBackend(**kw).send_messages([m])

    @override_settings(BREVO_API_KEY="key123")
    def test_sends_the_right_request(self):
        import json
        with patch("urllib.request.urlopen") as op:
            self.assertEqual(self._send(), 1)
        req = op.call_args[0][0]
        body = json.loads(req.data)
        self.assertEqual(req.get_header("Api-key"), "key123")
        self.assertEqual((body["sender"], body["to"], body["subject"]),
                         ({"email": "me@example.com", "name": "PhotoPholio"}, [{"email": "you@example.com"}], "Hello"))
        self.assertEqual((body["htmlContent"], body["textContent"]), ("<p>html body</p>", "plain body"))

    @override_settings(BREVO_API_KEY="key123", EMAIL_BACKEND="photopholio.email_backends.BrevoEmailBackend")
    def test_failure_is_raised_or_swallowed_by_flag_and_never_breaks_a_booking_email(self):
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            with self.assertRaises(OSError):
                self._send()
            self.assertEqual(self._send(fail_silently=True), 0)
            self.assertFalse(emails._send("a@b.com", "Subj", "Head", ["x"]))  # emails.py catches it, returns False


class CloudinaryStorageTests(TestCase):
    """Photos go to Cloudinary only when CLOUDINARY_URL is set; otherwise nothing changes. No real network is used."""

    def _storage(self, env):
        import json, os, subprocess, sys
        from django.conf import settings
        code = ("import json, django; from django.conf import settings; "
                "print(json.dumps([settings.STORAGES['default']['BACKEND'], 'cloudinary_storage' in settings.INSTALLED_APPS]))")
        run_env = {k: v for k, v in os.environ.items() if k != "CLOUDINARY_URL"}
        run_env.update(env, DJANGO_SETTINGS_MODULE="config.settings")
        out = subprocess.run([sys.executable, "-c", code], cwd=settings.BASE_DIR, env=run_env, capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_local_disk_without_the_variable(self):
        self.assertEqual(self._storage({}), ["django.core.files.storage.FileSystemStorage", False])

    def test_cloudinary_with_the_variable(self):
        self.assertEqual(self._storage({"CLOUDINARY_URL": "cloudinary://key:secret@democloud"}),
                         ["cloudinary_storage.storage.MediaCloudinaryStorage", True])


class SeedResetDemoTests(TestCase):
    """--reset-demo rebuilds only the fictional photographers and never touches real accounts."""

    def test_reset_demo_keeps_real_photographers(self):
        from io import StringIO
        from django.core.management import call_command
        from photopholio.management.commands.seed_demo import PHOTOGRAPHERS
        country = Country.objects.create(name="India")
        city = City.objects.create(state=State.objects.create(country=country, name="Gujarat"), name="Vadodara")
        real_user = User.objects.create_user("realpro", password="x-pass-12345", email="r@example.com")
        real = Photographer.objects.create(name="Real Person", bio="b", specialities="event", city=city, user=real_user, status="approved")
        with override_settings(MEDIA_ROOT=TMP_MEDIA):
            call_command("seed_demo", stdout=StringIO())  # real photographer exists, so a plain seed does nothing
            self.assertEqual(Photographer.objects.count(), 1)
            call_command("seed_demo", "--reset-demo", stdout=StringIO())
            self.assertEqual(Photographer.objects.count(), 1 + len(PHOTOGRAPHERS))
            self.assertTrue(Photographer.objects.filter(pk=real.pk).exists())
            call_command("seed_demo", "--reset-demo", stdout=StringIO())  # twice: still no duplicates
            self.assertEqual(Photographer.objects.count(), 1 + len(PHOTOGRAPHERS))


class DarkModeAndSiteUrlTests(TestCase):
    def test_pages_carry_the_theme_switch_and_the_early_theme_script(self):
        page = self.client.get(reverse("home"))
        self.assertContains(page, 'id="themeToggle"')
        self.assertContains(page, "pp-theme")  # the saved choice is read before the page paints
        self.assertContains(page, 'name="color-scheme"')

    def test_dark_styles_exist_and_light_styles_are_untouched(self):
        from django.conf import settings
        css = (settings.BASE_DIR / "photopholio" / "static" / "photopholio" / "css" / "style.css").read_text()
        self.assertIn('[data-bs-theme="dark"]', css)
        self.assertTrue(css.startswith(":root{"))  # the original light variables still come first

    def test_site_url_falls_back_to_the_render_address(self):
        import json, os, subprocess, sys
        from django.conf import settings
        code = "from django.conf import settings; import json; print(json.dumps(settings.SITE_URL))"

        def run(env):
            base = {k: v for k, v in os.environ.items() if k not in ("SITE_URL", "RENDER_EXTERNAL_URL")}
            base.update(env, DJANGO_SETTINGS_MODULE="config.settings")
            out = subprocess.run([sys.executable, "-c", code], cwd=settings.BASE_DIR, env=base, capture_output=True, text=True, timeout=60)
            return json.loads(out.stdout.strip().splitlines()[-1])

        self.assertEqual(run({}), "http://127.0.0.1:8000")
        self.assertEqual(run({"RENDER_EXTERNAL_URL": "https://site.onrender.com/"}), "https://site.onrender.com")
        self.assertEqual(run({"SITE_URL": "https://mine.example/", "RENDER_EXTERNAL_URL": "https://site.onrender.com"}), "https://mine.example")
