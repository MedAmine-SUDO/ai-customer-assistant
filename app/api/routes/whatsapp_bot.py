"""WhatsApp: same assistant, same leads, one business number for both demo businesses."""

import hmac
import logging
from collections import deque

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.routes.chat import run_after_reply
from app.core.config import settings
from app.models import Conversation
from app.services import whatsapp
from app.services.assistant import handle_message
from app.tenants import TENANTS, get_tenant

log = logging.getLogger(__name__)
router = APIRouter(tags=["whatsapp"])

CHOOSE = (
    "Bonjour 👋 Quelle entreprise de démonstration voulez-vous tester ?\n"
    "Hi! Which demo business would you like to try?\n"
    "مرحبا! أي شركة تحب تجرب؟"
)
SWITCH_WORDS = {"menu", "changer", "switch", "start", "/start"}
UNSUPPORTED = "Je ne lis que les messages écrits pour l'instant. / I can only read text messages for now."
# Meta re-sends a call it thinks failed; don't answer the same message twice.
_seen: deque[str] = deque(maxlen=1000)


def _latest(db, wa_id: str) -> Conversation | None:
    return db.scalar(
        select(Conversation)
        .where(Conversation.channel == "whatsapp", Conversation.external_id == wa_id)
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )


def _tenant_in(text: str) -> str | None:
    lowered = text.lower()
    return next((t.slug for t in TENANTS.values() if any(k in lowered for k in t.keywords)), None)


def _offer_businesses(wa_id: str) -> None:
    whatsapp.send_buttons(
        wa_id, CHOOSE, [(f"tenant:{t.slug}", t.short_name or t.name) for t in TENANTS.values()]
    )


def _start(db, wa_id: str, name: str | None, tenant: str, lang: str = "fr") -> Conversation:
    """A fresh conversation for the chosen business, with its welcome message."""
    conv = Conversation(tenant=tenant, channel="whatsapp", external_id=wa_id, customer_name=name)
    db.add(conv)
    db.commit()
    t = get_tenant(tenant)
    welcome = t.welcome.get(lang, t.welcome["fr"])
    tips = "\n".join(f"• {s}" for s in t.suggestions.get(lang, t.suggestions["fr"]))
    whatsapp.send_text(wa_id, f"{welcome}\n\n{tips}\n\n_menu_ : autre entreprise / other business")
    return conv


def _handle(db, background: BackgroundTasks, msg: dict, name: str | None) -> None:
    wa_id = msg["from"]
    kind = msg.get("type")

    if kind == "interactive":
        choice = (msg["interactive"].get("button_reply") or {}).get("id", "")
        if choice.startswith("tenant:") and choice[7:] in TENANTS:
            _start(db, wa_id, name, choice[7:])
        return
    if kind == "button":  # a quick-reply button on a template (the follow-up)
        text = msg["button"].get("text", "")
    elif kind == "text":
        text = msg["text"].get("body", "")
    else:
        whatsapp.send_text(wa_id, UNSUPPORTED)
        return
    if not text.strip():
        return

    conv = _latest(db, wa_id)
    if text.strip().lower() in SWITCH_WORDS:
        _offer_businesses(wa_id)
        return
    if conv is None:
        # First contact: the demo's links prefill "Bonjour Sahel Sourire", which picks the business.
        tenant = _tenant_in(text)
        if tenant is None:
            _offer_businesses(wa_id)
            return
        conv = Conversation(tenant=tenant, channel="whatsapp", external_id=wa_id, customer_name=name)
        db.add(conv)
        db.flush()

    whatsapp.mark_read(msg["id"])
    reply = handle_message(db, conv, text)
    if reply.text:
        whatsapp.send_text(wa_id, reply.text)
    run_after_reply(background, conv.id, reply)


def _process(db, background: BackgroundTasks, payload: dict) -> None:
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", [])}
            # Delivery and read receipts ("statuses") are ignored.
            for msg in value.get("messages", []):
                if msg.get("id") in _seen:
                    continue
                _seen.append(msg.get("id"))
                try:
                    _handle(db, background, msg, (names.get(msg.get("from")) or "")[:120] or None)
                except Exception:
                    # Answer 200 anyway, or Meta retries the same broken message for days.
                    log.exception("WhatsApp message %s failed", msg.get("id"))
                    db.rollback()


@router.get("/whatsapp/webhook", include_in_schema=False)
def verify(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    """Meta calls this once, when you register the webhook URL."""
    if (
        mode == "subscribe"
        and settings.whatsapp_verify_token
        and hmac.compare_digest(token, settings.whatsapp_verify_token)
    ):
        return PlainTextResponse(challenge)
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Bad verify token")


@router.post("/whatsapp/webhook", include_in_schema=False)
async def webhook(
    request: Request,
    db: DbSession,
    background: BackgroundTasks,
    x_hub_signature_256: str = Header(""),
):
    body = await request.body()
    if not whatsapp.valid_signature(body, x_hub_signature_256):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad signature")
    payload = await request.json()
    if payload.get("object") == "whatsapp_business_account":
        await run_in_threadpool(_process, db, background, payload)
    return {"ok": True}


def send_human_reply(db, conv: Conversation, text: str) -> bool:
    """A team member answered from the dashboard: deliver it on WhatsApp."""
    if conv.channel != "whatsapp" or not conv.external_id:
        return False
    return whatsapp.send_text(conv.external_id, f"👤 {text}")
