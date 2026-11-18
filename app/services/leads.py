"""Leads: validate what the assistant collected, store it, send it to the team, follow up."""

import html
import logging
import re
from datetime import timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import utcnow
from app.models import Conversation, Lead, Message
from app.services import mailer, sheets, whatsapp
from app.tenants import get_tenant

log = logging.getLogger(__name__)
LANGUAGES = {"fr", "en", "ar"}
CHANNEL_LABELS = {"web": "Site web", "whatsapp": "WhatsApp"}
# WhatsApp accepts free text only within 24 h of the customer's last message.
WHATSAPP_WINDOW = timedelta(hours=24) - timedelta(minutes=5)


class InvalidLead(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """Tunisian numbers (8 digits, with or without +216) and international ones in E.164."""
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits, raw = digits[2:], "+" + digits[2:]
    if len(digits) == 8 and digits[0] in "2345679":
        digits = "216" + digits
    elif not (digits.startswith("216") and len(digits) == 11) and not (
        raw.startswith("+") and 9 <= len(digits) <= 15
    ):
        raise InvalidLead("invalid phone number: ask the customer to check it (8 digits in Tunisia)")
    if digits.startswith("216") and (len(digits) != 11 or digits[3] not in "2345679"):
        raise InvalidLead("invalid Tunisian phone number: it should have 8 digits")
    if digits.startswith("216"):
        n = digits[3:]
        return f"+216 {n[:2]} {n[2:5]} {n[5:]}"
    return "+" + digits


def mask_phone(phone: str) -> str:
    """For the public demo: enough to recognise your own lead, not enough to call it."""
    if not settings.demo_mode or len(phone) < 6:
        return phone
    return phone[:7] + re.sub(r"\d", "•", phone[7:-3]) + phone[-3:]


PHONE_IN_TEXT = re.compile(r"(?<!\d)(?:\+|00)?(?:\d[\s.-]?){7,14}\d(?!\d)")


def mask_text(text: str) -> str:
    """Phone numbers inside a transcript, masked the same way on the public demo."""
    if not settings.demo_mode:
        return text

    def hide(m: re.Match) -> str:
        raw = m.group(0)
        digits = [i for i, c in enumerate(raw) if c.isdigit()]
        keep = set(digits[:2] + digits[-3:]) if len(digits) <= 8 else set(digits[:5] + digits[-3:])
        return "".join(c if not c.isdigit() or i in keep else "•" for i, c in enumerate(raw))

    return PHONE_IN_TEXT.sub(hide, text)


def whatsapp_link(lead: Lead, text: str) -> str:
    digits = re.sub(r"\D", "", lead.phone)
    return f"https://wa.me/{digits}?text={quote(text)}"


def local_time(lead: Lead) -> str:
    return lead.created_at.astimezone(ZoneInfo(settings.timezone)).strftime("%d/%m/%Y %H:%M")


def save_lead(db: Session, conv: Conversation, args: dict) -> tuple[Lead, bool]:
    """Create the conversation's lead, or update it if the customer corrected something.
    Returns (lead, created)."""
    name = (args.get("name") or "").strip()
    need = (args.get("need") or "").strip()
    if len(name) < 2:
        raise InvalidLead("missing name: ask for the customer's name")
    if len(need) < 3:
        raise InvalidLead("missing need: ask what they need")
    phone = normalize_phone(args.get("phone") or "")
    email = (args.get("email") or "").strip() or None
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        email = None
    language = args.get("language") if args.get("language") in LANGUAGES else conv.language or "fr"

    lead = conv.lead
    created = lead is None
    if created:
        lead = Lead(tenant=conv.tenant, conversation_id=conv.id, channel=conv.channel)
        db.add(lead)
    lead.name, lead.phone, lead.email, lead.need = name[:120], phone, email, need
    lead.preferred_time = (args.get("preferred_time") or None) and args["preferred_time"][:200]
    lead.language = language
    if created:
        lead.created_at = utcnow()
        lead.followup_due_at = lead.created_at + timedelta(minutes=settings.followup_delay_minutes)
    conv.customer_name = lead.name
    db.commit()
    return lead, created


def admin_url(conv_id: str) -> str:
    return f"{settings.base_url}/admin#c={conv_id}"


def alert_subject(lead: Lead) -> str:
    return f"Nouveau lead : {lead.name} ({get_tenant(lead.tenant).name})"


def alert_html(lead: Lead) -> str:
    """The team's e-mail for a new lead."""
    e = html.escape
    rows = [("Nom", lead.name), ("Téléphone", mask_phone(lead.phone))]
    if lead.email:
        rows.append(("E-mail", lead.email))
    rows.append(("Besoin", lead.need))
    if lead.preferred_time:
        rows.append(("Disponibilité", lead.preferred_time))
    rows.append(("Canal", f"{CHANNEL_LABELS.get(lead.channel, lead.channel)} · {lead.language.upper()}"))
    rows.append(("Reçu le", local_time(lead)))
    table = "".join(
        f'<tr><td style="padding:4px 16px 4px 0;color:#64748b">{k}</td><td><b>{e(v)}</b></td></tr>'
        for k, v in rows
    )
    return (
        f"<p>🔔 Nouveau lead pour <b>{e(get_tenant(lead.tenant).name)}</b></p>"
        f"<table>{table}</table>"
        f'<p><a href="{admin_url(lead.conversation_id)}">Voir la conversation</a></p>'
    )


def reminder_subject(lead: Lead) -> str:
    return f"Relance à faire : {lead.name}"


def reminder_html(lead: Lead, text: str) -> str:
    """The follow-up the assistant couldn't send itself: the team sends it in one click."""
    e = html.escape
    return (
        f"<p>⏰ <b>{e(lead.name)}</b> ({e(mask_phone(lead.phone))}) a demandé : {e(lead.need)}</p>"
        f"<p>Message prêt à envoyer :</p><blockquote>{e(text)}</blockquote>"
        f'<p><a href="{whatsapp_link(lead, text)}">Envoyer la relance sur WhatsApp</a></p>'
    )


def n8n_payload(lead: Lead) -> dict:
    tenant = get_tenant(lead.tenant)
    text = followup_text(lead)
    return {
        "lead_id": lead.id,
        "tenant": lead.tenant,
        "business": tenant.name,
        "name": lead.name,
        "phone": mask_phone(lead.phone),
        "email": lead.email,
        "need": lead.need,
        "preferred_time": lead.preferred_time,
        "language": lead.language,
        "channel": lead.channel,
        "created_at": local_time(lead),
        "conversation_url": admin_url(lead.conversation_id),
        "alert_subject": alert_subject(lead),
        "alert_html": alert_html(lead),
        "reminder_subject": reminder_subject(lead),
        "reminder_html": reminder_html(lead, text),
        "followup_after_seconds": settings.followup_delay_minutes * 60,
        "followup_url": f"{settings.base_url}/internal/leads/{lead.id}/followup",
        "followup_text": text,
        "whatsapp_link": whatsapp_link(lead, text),
    }


def dispatch(db: Session, lead: Lead) -> None:
    """Send a new lead everywhere it should go. Each sink is independent: one failing
    (Google down, SMTP refused) never loses the lead, which is already in the database."""
    sinks = settings.lead_sink_set
    tenant = get_tenant(lead.tenant)
    if "sheets" in sinks:
        lead.sheet_synced = sheets.append_row(
            "Leads",
            [
                local_time(lead),
                tenant.name,
                lead.name,
                mask_phone(lead.phone),
                lead.email,
                lead.need,
                lead.preferred_time,
                lead.language.upper(),
                CHANNEL_LABELS.get(lead.channel, lead.channel),
                admin_url(lead.conversation_id),
            ],
        )
    if "email" in sinks:
        lead.team_notified = mailer.notify_team(alert_subject(lead), alert_html(lead))
    if "n8n" in sinks and settings.n8n_lead_webhook_url:
        try:
            httpx.post(settings.n8n_lead_webhook_url, json=n8n_payload(lead), timeout=8).raise_for_status()
            lead.n8n_sent = True
        except httpx.HTTPError as exc:
            log.warning("n8n webhook failed for lead %s: %s", lead.id, exc)
    if lead.team_notified or lead.n8n_sent:
        lead.status = "contacted"
    db.commit()


def followup_text(lead: Lead) -> str:
    tenant = get_tenant(lead.tenant)
    template = tenant.followup.get(lead.language) or tenant.followup["fr"]
    first_name = lead.name.split()[0]
    need = lead.need if len(lead.need) <= 80 else lead.need[:77] + "…"
    return template.format(name=first_name, need=need, phone=tenant.phone)


def _last_customer_message(db: Session, conv: Conversation):
    return db.scalar(
        select(func.max(Message.created_at)).where(Message.conversation_id == conv.id, Message.role == "user")
    )


def _send_on_whatsapp(db: Session, lead: Lead, conv: Conversation, text: str) -> bool:
    """Free text while the 24-hour window is open, the approved template after, else nothing."""
    last = _last_customer_message(db, conv)
    if last and utcnow() - last < WHATSAPP_WINDOW:
        return whatsapp.send_text(conv.external_id, text)
    if settings.whatsapp_followup_template:
        return whatsapp.send_template(
            conv.external_id,
            settings.whatsapp_followup_template,
            settings.whatsapp_template_language,
            [lead.name.split()[0], get_tenant(lead.tenant).name],
        )
    return False


def send_followup(db: Session, lead: Lead, remind_team: bool = True) -> dict:
    """The 24-hour follow-up. WhatsApp leads get it from the business number directly. Web leads
    left a phone number, so the team gets an e-mail with a one-click WhatsApp link, message ready."""
    if lead.followup_sent_at:
        return {"lead_id": lead.id, "already_sent": True, "channel": lead.followup_channel}
    text = followup_text(lead)
    conv = lead.conversation
    sent = False
    if conv.channel == "whatsapp" and conv.external_id:
        sent = _send_on_whatsapp(db, lead, conv, text)
        if sent:
            db.add(Message(conversation_id=conv.id, role="assistant", content=text))
    channel = "whatsapp" if sent else "team"
    if not sent and remind_team:
        mailer.notify_team(reminder_subject(lead), reminder_html(lead, text))
    lead.followup_sent_at = utcnow()
    lead.followup_channel = channel
    lead.status = "followed_up"
    db.commit()
    return {
        "lead_id": lead.id,
        "already_sent": False,
        "channel": channel,
        "sent_to_customer": sent,
        "text": text,
        "email": lead.email,
        "whatsapp_link": whatsapp_link(lead, text),
    }


def due_followups(db: Session) -> list[Lead]:
    return list(
        db.scalars(select(Lead).where(Lead.followup_sent_at.is_(None), Lead.followup_due_at <= utcnow()))
    )
