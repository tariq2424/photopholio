"""
Email over HTTPS (Brevo API) instead of SMTP.

Why: free hosting such as Render blocks the SMTP ports (25, 465, 587), so Gmail SMTP cannot work there.
An HTTPS call to the Brevo API is not blocked. Nothing else in the project changes: emails.py still calls send_mail(),
Django picks this backend when BREVO_API_KEY is set (see config/settings.py).
Only the standard library is used, so there is no new package to install.
"""
import json
import logging
import urllib.error
import urllib.request
from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)
API_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            try:
                self._send_one(message)
                sent += 1
            except Exception:
                logger.exception("Brevo could not send '%s'", message.subject)
                if not self.fail_silently:
                    raise
        return sent

    def _send_one(self, message):
        name, address = parseaddr(message.from_email or settings.DEFAULT_FROM_EMAIL)
        payload = {"sender": {"email": address}, "to": [{"email": a} for a in message.to], "subject": message.subject}
        if name:
            payload["sender"]["name"] = name
        html = next((content for content, kind in getattr(message, "alternatives", []) if kind == "text/html"), None)
        if html:
            payload["htmlContent"] = html
            payload["textContent"] = message.body
        else:
            payload["textContent"] = message.body
        request = urllib.request.Request(
            API_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"api-key": settings.BREVO_API_KEY, "content-type": "application/json", "accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Brevo answered {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
