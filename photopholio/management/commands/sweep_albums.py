from django.core.management.base import BaseCommand

from photopholio import album_ops


class Command(BaseCommand):
    help = "Send the late-album alerts (customer and admins). Safe to run any time, each album is alerted once."

    def handle(self, *args, **opts):
        self.stdout.write(f"{album_ops.sweep_late(force=True)} late album(s) alerted.")
