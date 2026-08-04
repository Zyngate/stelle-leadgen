"""
Sends an immediate acknowledgment/invitation email the moment a lead is
captured - separate from whatever longer nurture sequence Mailchimp runs
afterward. Think of this as the "thanks, we got your info" email that
goes out in seconds, not the drip sequence that follows over days.

Uses plain SMTP so it works with any provider - Gmail, SendGrid,
Mailchimp Transactional (Mandrill), Amazon SES, etc - just point the
env vars at whichever one you're using. No vendor lock-in here.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


class EmailSendError(Exception):
    pass


def _get_smtp_config() -> dict:
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "FROM_EMAIL"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise EmailSendError(f"Missing SMTP env vars: {', '.join(missing)}")
    return {
        "host": os.environ["SMTP_HOST"],
        "port": int(os.environ["SMTP_PORT"]),
        "username": os.environ["SMTP_USERNAME"],
        "password": os.environ["SMTP_PASSWORD"],
        "from_email": os.environ["FROM_EMAIL"],
    }


def send_acknowledgment_email(to_email: str, first_name: str = "",
                               business_name: str = "us") -> None:
    """
    Sends a short "we got your info" email immediately after a lead
    submits the form. Keep this brief - it's a receipt, not a sales
    pitch. Mailchimp's automation picks up the actual nurture sequence
    from here.
    """
    config = _get_smtp_config()

    greeting = f"Hi {first_name}," if first_name else "Hi there,"
    subject = f"Thanks for reaching out to {business_name}"
    body_text = (
        f"{greeting}\n\n"
        f"Thanks for your interest - we've received your details and "
        f"someone from our team will follow up shortly.\n\n"
        f"Talk soon,\n{business_name}"
    )

    msg = MIMEMultipart()
    msg["From"] = config["from_email"]
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain"))

    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=10) as server:
            server.starttls()
            server.login(config["username"], config["password"])
            server.sendmail(config["from_email"], [to_email], msg.as_string())
    except Exception as e:
        raise EmailSendError(f"Failed to send acknowledgment email: {e}")
