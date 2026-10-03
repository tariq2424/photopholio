"""
Find out why someone cannot log in.

    python manage.py check_login isha                 shows what the database knows about the account
    python manage.py check_login isha --test-password asks for a password and says whether it matches
    python manage.py check_login isha --reset-password  sets a new password (asks twice)

Accepts a username or an email.
"""
from getpass import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from photopholio.models import Photographer


class Command(BaseCommand):
    help = "Show an account's login details (role, active, photographer profile) and optionally test or reset its password."

    def add_arguments(self, parser):
        parser.add_argument("identifier", help="username or email")
        parser.add_argument("--test-password", action="store_true", help="type a password to see if it matches")
        parser.add_argument("--reset-password", action="store_true", help="set a new password for the account")

    def handle(self, *args, identifier, test_password, reset_password, **opts):
        User = get_user_model()
        users = list(User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier)))
        if not users:
            raise CommandError(f"No account has the username or email '{identifier}'. Check the spelling, or list all with: "
                               "python manage.py shell -c \"from django.contrib.auth.models import User; print(list(User.objects.values_list('username','email')))\"")
        if len(users) > 1:
            self.stdout.write(self.style.WARNING(f"{len(users)} accounts match. Showing all of them."))
        for user in users:
            profile = getattr(user, "profile", None)
            photographer = Photographer.objects.filter(user=user).first()
            self.stdout.write(self.style.MIGRATE_HEADING(f"\nAccount: {user.username}"))
            self.stdout.write(f"  email:        {user.email or '(none)'}")
            self.stdout.write(f"  active:       {user.is_active}" + ("" if user.is_active else "   <-- inactive accounts cannot log in"))
            self.stdout.write(f"  role:         {profile.role if profile else 'customer (no profile row)'}")
            self.stdout.write(f"  staff/admin:  staff={user.is_staff} superuser={user.is_superuser}")
            self.stdout.write(f"  last login:   {user.last_login or 'never'}")
            self.stdout.write(f"  usable password set: {user.has_usable_password()}")
            if photographer:
                self.stdout.write(f"  photographer profile: '{photographer.name}', status = {photographer.status}")
            elif profile and profile.role == "photographer":
                self.stdout.write("  photographer profile: not created yet (finish it at /photographer/profile/)")

            if test_password:
                if user.check_password(getpass("  Password to test: ")):
                    self.stdout.write(self.style.SUCCESS("  -> password MATCHES. Login works with the username or email above."))
                else:
                    self.stdout.write(self.style.ERROR("  -> password does NOT match. Use --reset-password to set a new one."))
            if reset_password:
                first = getpass("  New password: ")
                if not first or first != getpass("  New password (again): "):
                    raise CommandError("Passwords were empty or did not match. Nothing was changed.")
                user.set_password(first)
                user.save(update_fields=["password"])
                self.stdout.write(self.style.SUCCESS("  -> password changed."))
