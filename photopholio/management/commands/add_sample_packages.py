"""
python manage.py add_sample_packages

Gives every photographer who has no packages a Basic / Standard / Premium set worked out from their day rate.
They are only a starting point: photographers (or the admin) can change or delete them.
"""
from django.core.management.base import BaseCommand

from photopholio.models import Photographer
from photopholio.packages import create_sample_packages


class Command(BaseCommand):
    help = "Create sample Basic/Standard/Premium packages for photographers that have none."

    def handle(self, *args, **opts):
        done = 0
        for photographer in Photographer.objects.all():
            if create_sample_packages(photographer):
                done += 1
                self.stdout.write(f"  {photographer.name}")
        self.stdout.write(self.style.SUCCESS(f"Packages added for {done} photographer(s)."))
