"""Outgoing mail, in one place.

The reset mail was the only sender and owned the SMTP settings inline; the
follow-update digest is the second, and a second copy of the connection code is
a second place for the STARTTLS or login handling to drift. Configuration is
read from the environment, and mail is OFF unless both a host and a sender are
set — see the note on SMTP_FROM in api/password_reset.py.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SITE_URL = (os.getenv("SITE_URL", "") or os.getenv("PUBLIC_BASE_URL", "")).rstrip("/")


def configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def send(to: str, subject: str, body: str, headers: dict | None = None,
         html: str | None = None) -> bool:
    """Best-effort delivery. Returns whether it actually went out.

    `body` is the plain text; `html`, when given, is added as the preferred
    alternative (multipart/alternative), so every client gets one or the
    other and nothing is lost to a client that shows only text."""
    if not configured():
        return False
    try:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to
        for k, v in (headers or {}).items():
            msg[k] = v
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
        return True
    except Exception as e:
        log.warning(f"mail to a reader failed: {type(e).__name__}: {e}")
        return False
