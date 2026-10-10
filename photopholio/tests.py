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

from . import analytics, availability, booking_flow, emails, favourites, policy, reviews, throttle
from .forms import RegisterForm
from .models import (Booking, City, Country, Feedback, BlockedDate, LoginAttempt, Package, Payment, Photo, Photographer, Portfolio,
                     Favourite, RescheduleRequest, State, UserProfile)


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

    def test_request_accept_pay_and_double_booking(self):
        self.client.login(username="tester", password="pass12345")
        date = (timezone.localdate() + timedelta(days=5)).isoformat()
        url = reverse("photographer_detail", args=[self.ph.slug])
        self.client.post(url, {"event_date": date, "tool": ""})
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
        self.assertRedirects(resp, reverse("pro_profile"))
        self.assertEqual(User.objects.get(username="newbie").profile.role, "photographer")

    def test_register_defaults_to_customer_page(self):
        resp = self.client.post(reverse("register"), {
            "role": "customer", "first_name": "Buyer", "username": "buyer",
            "email": "b@example.com", "password1": "Str0ng-pass-99", "password2": "Str0ng-pass-99"})
        self.assertRedirects(resp, reverse("profile"))
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
        self.client.post(reverse("photographer_detail", args=[target.slug]), {"event_date": date, "tool": ""})
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
        self.client.post(reverse("photographer_detail", args=[self.ph.slug]), {"event_date": date, "tool": ""})
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
        self.client.post(reverse("photographer_detail", args=[self.ph.slug]), {"event_date": date, "tool": "Drone"})
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
                resp = self.client.post(reverse("photographer_detail", args=[self.ph.slug]), {"event_date": date, "tool": ""})
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
        r = self.client.post(reverse("photographer_detail", args=[self.ph.slug]), {"event_date": self.day(10).isoformat(), "tool": ""})
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
        data = {"event_date": (self.today + timedelta(days=days)).isoformat(), "tool": ""}
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
