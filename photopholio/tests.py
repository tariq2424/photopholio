import shutil
import tempfile
from datetime import timedelta
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from django.urls import reverse
from django.utils import timezone

from . import booking_flow, policy
from .forms import RegisterForm
from .models import (Booking, City, Country, Feedback, Payment, Photo, Photographer, Portfolio,
                     State, UserProfile)


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
