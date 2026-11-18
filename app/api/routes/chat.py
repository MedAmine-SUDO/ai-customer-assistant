import re
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy import func, select

from app.api.deps import DbSession, client_key
from app.core.config import settings
from app.db import utcnow
from app.models import Conversation, Message
from app.schemas import ChatIn, ChatOut, MessageOut, TenantOut
from app.services import costs
from app.services.assistant import after_reply, handle_message
from app.tenants import TENANTS

router = APIRouter(tags=["chat"])


@router.get("/tenants", response_model=list[TenantOut])
def list_tenants():
    return [TenantOut(**{k: getattr(t, k) for k in TenantOut.model_fields}) for t in TENANTS.values()]


def run_after_reply(background: BackgroundTasks, conv_id: str, reply) -> None:
    if not (reply.new_lead or reply.handoff):
        return
    if settings.serverless:
        # A serverless function may be frozen once the response is sent: do it now.
        after_reply(conv_id, reply)
    else:
        background.add_task(after_reply, conv_id, reply)


@router.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, request: Request, db: DbSession, background: BackgroundTasks):
    if body.tenant not in TENANTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown business")
    conv = None
    if body.conversation_id:
        conv = db.get(Conversation, body.conversation_id)
        if conv and (conv.tenant != body.tenant or conv.channel != "web"):
            conv = None
    if conv is None:
        key = client_key(request)
        started_today = db.scalar(
            select(func.count()).where(
                Conversation.client_key == key,
                Conversation.created_at >= utcnow() - timedelta(days=1),
            )
        )
        if started_today >= settings.max_conversations_per_ip_per_day:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many conversations today")
        conv = Conversation(tenant=body.tenant, channel="web", client_key=key)
        db.add(conv)
        db.flush()

    reply = handle_message(db, conv, body.message)
    run_after_reply(background, conv.id, reply)
    return ChatOut(
        conversation_id=conv.id,
        reply=reply.text,
        mode=reply.mode,
        lead_captured=reply.lead_id is not None or conv.lead is not None,
        handoff=reply.handoff,
        sources=reply.sources,
    )


@router.get("/chat/{conversation_id}/messages", response_model=list[MessageOut])
def poll_messages(conversation_id: str, db: DbSession, after_id: int = 0):
    """The widget polls this while a team member has the conversation."""
    conv = db.get(Conversation, conversation_id)
    if not conv or conv.channel != "web":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    rows = db.scalars(
        select(Message)
        .where(
            Message.conversation_id == conv.id,
            Message.id > after_id,
            Message.role.in_(("assistant", "human")),
        )
        .order_by(Message.id)
    ).all()
    return [MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at) for m in rows]


@router.get("/config", tags=["meta"])
def public_config():
    """Links for the demo page."""
    number = re.sub(r"\D", "", settings.whatsapp_display_number)
    return {
        "sheet_url": settings.public_sheet_url or None,
        # One link per business: the prefilled greeting tells the assistant which one it is.
        "whatsapp": {
            slug: f"https://wa.me/{number}?text={quote('Bonjour ' + t.name)}" for slug, t in TENANTS.items()
        }
        if number
        else None,
        "llm": settings.llm_provider,
        "model": settings.chat_model if settings.llm_provider != "mock" else "mock",
        "demo_mode": settings.demo_mode,
        "admin_password": settings.admin_password if settings.demo_mode else None,
    }


@router.get("/stats", tags=["meta"])
def public_stats(db: DbSession):
    """Measured cost and speed, shown on the demo page. No personal data."""
    return costs.cost_summary(db)
