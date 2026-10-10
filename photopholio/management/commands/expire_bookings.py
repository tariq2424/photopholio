"""
python manage.py expire_bookings

Cancels accepted bookings whose 24 hour payment window has passed (and emails the customers).
The site also does this whenever someone opens a booking page, so this command is optional.
It is handy as a daily scheduled task on PythonAnywhere.
"""
from django.core.management.base import BaseCommand

from photopholio import booking_flow


class Command(BaseCommand):
    help = "Cancel accepted bookings that were not paid within the payment window."

    def handle(self, *args, **opts):
        n = booking_flow.expire_stale()
        self.stdout.write(self.style.SUCCESS(f"{n} booking(s) lapsed."))
