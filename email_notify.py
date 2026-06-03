"""Gmail SMTP helper, used by check.py to email the backorder match list.

Single responsibility: send one multipart (text + HTML) email through Gmail's
submission server. Kept separate from the wordfreq-heavy check module, like
notify.py. A send failure is logged and swallowed so it never aborts a run.
"""
from __future__ import annotations

import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def email_send(*, to: str, sender: str, subject: str, html: str, text: str) -> None:
    try:
        password = os.environ.get("GMAIL_APP_PASSWORD", "")
        if not password:
            print("email send SKIPPED: GMAIL_APP_PASSWORD not set", file=sys.stderr)
            return
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.sendmail(sender, [to], msg.as_string())
    except Exception as e:
        print(f"email send FAILED: {e}", file=sys.stderr)
