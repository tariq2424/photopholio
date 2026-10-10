"""
python manage.py send_test_email you@example.com

Tells you straight away whether the server can send email (useful on the live site).
"""
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send one test email and say whether it worked."

    def add_arguments(self, parser):
        parser.add_argument("to")

    def handle(self, *args, to, **opts):
        backend = settings.EMAIL_BACKEND.rsplit(".", 2)[-2]
        self.stdout.write(f"Email backend: {backend}   from: {settings.DEFAULT_FROM_EMAIL}   site url: {settings.SITE_URL}")
        if backend == "console":
            self.stdout.write(self.style.WARNING("No EMAIL_HOST_USER / EMAIL_HOST_PASSWORD set: the email will only be printed below."))
        try:
            send_mail("[PhotoPholio] Test email", "If you can read this, email works.", settings.DEFAULT_FROM_EMAIL, [to])
        except Exception as e:
            raise CommandError(f"Sending failed: {e!r}")
        self.stdout.write(self.style.SUCCESS("Done. (Check the inbox and the spam folder.)" if backend != "console" else "Done (printed above)."))
