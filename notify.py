"""Failure alert emails.

Uses Gmail SMTP with an app password (not the normal account password - Gmail
blocks plain-password SMTP logins). Silently no-ops if the secrets aren't
configured, so this is safe to deploy before the email secrets exist.
"""
import logging
import os
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger("onbuy_sync")


def send_alert_email(subject, body):
    host = os.getenv("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.getenv("SMTP_PORT") or "465")
    user = os.getenv("SMTP_USER")
    app_password = os.getenv("SMTP_APP_PASSWORD")
    to_addr = os.getenv("ALERT_EMAIL_TO", user)

    if not user or not app_password:
        logger.warning("SMTP_USER/SMTP_APP_PASSWORD not set - skipping alert email: %s", subject)
        return

    msg = MIMEText(body)
    msg["Subject"] = f"[OnBuy Sync] {subject}"
    msg["From"] = user
    msg["To"] = to_addr

    try:
        with smtplib.SMTP_SSL(host, port, timeout=15) as server:
            server.login(user, app_password)
            server.sendmail(user, [to_addr], msg.as_string())
        logger.info("Alert email sent: %s", subject)
    except Exception as exc:  # an alert failure must never crash the run itself
        logger.error("Failed to send alert email: %s", exc)
