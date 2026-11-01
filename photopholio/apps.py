from django.apps import AppConfig


class PhotopholioConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'photopholio'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from . import notifications

        def greet(sender, user, **kwargs):
            try:
                notifications.welcome(user)
            except Exception:
                notifications.logger.exception("Could not create the welcome notification")

        user_logged_in.connect(greet, dispatch_uid="photopholio_welcome")
