"""Team alerts by e-mail, through any SMTP server."""

import html
import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.core.config import settings

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(settings.smtp_host and settings.team_email)


def team_addresses() -> list[str]:
    return [a.strip() for a in settings.team_email.split(",") if a.strip()]


def html_to_text(body: str) -> str:
    """The plain-text part, for mail apps that don't show HTML."""
    body = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"\2: \1", body)
    body = re.sub(r"<br\s*/?>|</p>|</tr>", "\n", body)
    return html.unescape(re.sub(r"<[^>]+>", "", body)).strip()


def send(to: list[str], subject: str, body_html: str) -> bool:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.app_name, settings.email_from or settings.smtp_username))
    msg["To"] = ", ".join(to)
    msg.set_content(html_to_text(body_html))
    msg.add_alternative(body_html, subtype="html")
    try:
        if settings.smtp_port == 465:
            server = smtplib.SMTP_SSL(
                settings.smtp_host, 465, context=ssl.create_default_context(), timeout=10
            )
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
            if settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
        with server:
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("E-mail %r failed: %s", subject, exc)
        return False


def notify_team(subject: str, body_html: str) -> bool:
    if not enabled():
        return False
    return send(team_addresses(), subject, body_html)
