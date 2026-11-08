from django.conf import settings


def support(request):
    """Contact details for the footer, Contact page and Help center (set in config/settings.py)."""
    phone = getattr(settings, "SUPPORT_PHONE", "")
    return {"support_phone": phone, "support_phone_link": "".join(c for c in phone if c.isdigit() or c == "+"),
            "support_email": getattr(settings, "SUPPORT_EMAIL", ""),
            "support_email_mailto": "mailto:" + getattr(settings, "SUPPORT_EMAIL", ""),
            "support_email_link": "https://mail.google.com/mail/?view=cm&fs=1&to=" + getattr(settings, "SUPPORT_EMAIL", "")}
