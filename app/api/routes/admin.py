"""The team's dashboard: leads, conversations to review or take over, documents, PDF extraction."""

import hmac
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminOnly, DbSession, create_admin_token
from app.api.routes.whatsapp_bot import send_human_reply
from app.core.config import settings
from app.models import Chunk, Conversation, ExtractedDocument, KnowledgeDocument, Lead, Message
from app.schemas import (
    ConversationDetail,
    ConversationOut,
    ExtractedOut,
    HumanReplyIn,
    KnowledgeDocOut,
    LeadOut,
    LoginIn,
    MessageOut,
)
from app.services import costs, knowledge, mailer, whatsapp
from app.services.extract import extract_document
from app.services.leads import mask_phone, mask_text
from app.tenants import TENANTS

router = APIRouter(prefix="/admin", tags=["admin"])
protected = APIRouter(prefix="/admin", tags=["admin"], dependencies=[AdminOnly])


@router.post("/login")
def login(body: LoginIn):
    if not hmac.compare_digest(body.password, settings.admin_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong password")
    return {"access_token": create_admin_token(), "token_type": "bearer", "demo_mode": settings.demo_mode}


def _lead_out(lead: Lead) -> LeadOut:
    out = LeadOut.model_validate(lead)
    out.phone = mask_phone(lead.phone)
    return out


@protected.get("/overview")
def overview(db: DbSession):
    def count(model, *where):
        return db.scalar(select(func.count()).select_from(model).where(*where))

    total_conv = count(Conversation)
    handed_off = count(Conversation, Conversation.needs_human.is_(True))
    return {
        "conversations": total_conv,
        "messages": count(Message, Message.role == "user"),
        "leads": count(Lead),
        "waiting_for_human": count(Conversation, Conversation.mode == "human"),
        "handoff_rate": round(handed_off / total_conv, 3) if total_conv else 0,
        "lead_rate": round(count(Lead) / total_conv, 3) if total_conv else 0,
        "by_tenant": {
            slug: {
                "conversations": count(Conversation, Conversation.tenant == slug),
                "leads": count(Lead, Lead.tenant == slug),
            }
            for slug in TENANTS
        },
        "costs": costs.cost_summary(db),
        "spent_last_24h_usd": f"{costs.spent_today(db):.4f}",
        "daily_budget_usd": str(settings.daily_budget_usd),
        "integrations": {
            "llm": settings.llm_provider,
            "lead_sinks": sorted(settings.lead_sink_set),
            "google_sheets": bool(settings.google_sheet_id and settings.google_service_account_json),
            "email": mailer.enabled(),
            "whatsapp": whatsapp.enabled(),
            "n8n": bool(settings.n8n_lead_webhook_url),
        },
    }


@protected.get("/leads", response_model=list[LeadOut])
def list_leads(db: DbSession, tenant: str | None = None, limit: int = 200):
    q = select(Lead).order_by(Lead.created_at.desc()).limit(min(limit, 500))
    if tenant:
        q = q.where(Lead.tenant == tenant)
    return [_lead_out(lead) for lead in db.scalars(q)]


@protected.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    db: DbSession, tenant: str | None = None, needs_human: bool | None = None, limit: int = 100
):
    q = (
        select(Conversation)
        .where(Conversation.message_count > 0)
        .options(selectinload(Conversation.lead))
        .order_by(Conversation.updated_at.desc())
        .limit(min(limit, 300))
    )
    if tenant:
        q = q.where(Conversation.tenant == tenant)
    if needs_human is not None:
        q = q.where(Conversation.needs_human.is_(needs_human))
    convs = db.scalars(q).all()
    previews = (
        dict(
            db.execute(
                select(Message.conversation_id, func.min(Message.content))
                .where(Message.conversation_id.in_([c.id for c in convs]), Message.role == "user")
                .group_by(Message.conversation_id)
            ).all()
        )
        if convs
        else {}
    )
    out = []
    for c in convs:
        item = ConversationOut.model_validate(c)
        item.preview = mask_text(previews.get(c.id) or "")[:120]
        item.has_lead = c.lead is not None
        out.append(item)
    return out


def _get_conversation(db, conv_id: str) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conv


@protected.get("/conversations/{conv_id}", response_model=ConversationDetail)
def get_conversation(conv_id: str, db: DbSession):
    conv = _get_conversation(db, conv_id)
    detail = ConversationDetail.model_validate(
        {
            **ConversationOut.model_validate(conv).model_dump(),
            "messages": [
                MessageOut.model_validate(m).model_copy(update={"content": mask_text(m.content)})
                for m in conv.messages
            ],
            "lead": _lead_out(conv.lead) if conv.lead else None,
            "has_lead": conv.lead is not None,
        }
    )
    return detail


@protected.post("/conversations/{conv_id}/reply", response_model=MessageOut)
def reply_as_human(conv_id: str, body: HumanReplyIn, db: DbSession):
    """Answer the customer yourself. The bot stays quiet until you hand the chat back."""
    conv = _get_conversation(db, conv_id)
    conv.mode = "human"
    msg = Message(conversation_id=conv.id, role="human", content=body.message.strip())
    db.add(msg)
    db.commit()
    send_human_reply(db, conv, msg.content)
    return MessageOut.model_validate(msg)


@protected.post("/conversations/{conv_id}/release", response_model=ConversationOut)
def release_to_bot(conv_id: str, db: DbSession):
    conv = _get_conversation(db, conv_id)
    conv.mode = "bot"
    conv.needs_human = False
    db.add(Message(conversation_id=conv.id, role="event", content="Conversation rendue à l'assistant"))
    db.commit()
    return ConversationOut.model_validate(conv)


@protected.get("/knowledge", response_model=list[KnowledgeDocOut])
def list_knowledge(db: DbSession, tenant: str | None = None):
    q = (
        select(KnowledgeDocument, func.count(Chunk.id))
        .outerjoin(Chunk)
        .group_by(KnowledgeDocument.id)
        .order_by(KnowledgeDocument.tenant, KnowledgeDocument.source)
    )
    if tenant:
        q = q.where(KnowledgeDocument.tenant == tenant)
    return [
        KnowledgeDocOut(
            id=d.id, tenant=d.tenant, source=d.source, title=d.title, chunks=n, created_at=d.created_at
        )
        for d, n in db.execute(q)
    ]


@protected.post("/knowledge/{tenant}", response_model=KnowledgeDocOut)
async def upload_knowledge(tenant: str, file: UploadFile, db: DbSession):
    """Add or replace a document (.md, .txt or .pdf). It's searchable right away."""
    if tenant not in TENANTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown business")
    name = Path(file.filename or "document.txt").name[:200]
    data = await file.read()
    if len(data) > settings.max_pdf_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File too large")
    if name.lower().endswith(".pdf"):
        text = knowledge.pdf_to_text(data)
    elif name.lower().endswith((".md", ".txt")):
        text = data.decode("utf-8", errors="replace")
    else:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Send a .md, .txt or .pdf file")
    try:
        knowledge.ingest_text(db, tenant, name, text, force=True)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return next(d for d in list_knowledge(db, tenant) if d.source == name)


@protected.delete("/knowledge/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge(doc_id: int, db: DbSession):
    doc = db.get(KnowledgeDocument, doc_id)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    db.delete(doc)
    db.commit()


@protected.post("/extract", response_model=ExtractedOut)
async def extract_pdf(file: UploadFile, db: DbSession):
    """Bonus: an invoice or quote PDF in, one structured row out (and in the sheet)."""
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Send a PDF")
    if len(data) > settings.max_pdf_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"PDF over {settings.max_pdf_mb} MB")
    if settings.llm_provider != "mock" and costs.spent_today(db) >= settings.daily_budget_usd:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demo budget for today reached")
    return extract_document(db, data, Path(file.filename or "document.pdf").name)


@protected.get("/extract", response_model=list[ExtractedOut])
def list_extracted(db: DbSession):
    return db.scalars(select(ExtractedDocument).order_by(ExtractedDocument.id.desc()).limit(100)).all()
