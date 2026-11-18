"""One customer message in, one answer out: retrieve, ask the model, run its tools, log it all."""

import html
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.llm import LLMUnavailable, get_llm
from app.models import Conversation, Message
from app.services import leads, mailer
from app.services.costs import spent_today, usage_cost
from app.services.knowledge import Hit, search
from app.services.language import darija_style, detect_language
from app.tenants import Tenant, get_tenant

log = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 3

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_lead",
            "description": "Save the customer as a lead for the sales team. Call it once you have "
            "their name, phone number and need. Call it again if they correct something.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Full name as the customer gave it"},
                    "phone": {"type": "string", "description": "Phone number exactly as given"},
                    "need": {"type": "string", "description": "One line: what they want, in their words"},
                    "email": {
                        "type": ["string", "null"],
                        "description": "Only if the customer gave one; never ask for it. Otherwise null",
                    },
                    "preferred_time": {
                        "type": ["string", "null"],
                        "description": "When they'd like to come or be called, if they said; never "
                        "ask for it. Otherwise null",
                    },
                    "language": {"type": "string", "enum": ["fr", "en", "ar"]},
                },
                "required": ["name", "phone", "need", "email", "preferred_time", "language"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff_to_human",
            "description": "Pass the conversation to a team member, who will answer in this chat.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why, in one short sentence (in French)"},
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]

SYSTEM_PROMPT = """You are the virtual assistant of {name}, {activity}, in {city}. You chat with \
customers on the business's {channel}. Today is {today}.

How to answer:
- Use ONLY the business information below. If the answer isn't there, say you don't have that \
information and offer to pass the question to the team. Never invent a price, a product, an \
opening hour or a policy.
- Reply in the customer's language: French, English or Arabic. If they write Tunisian darija \
(in Arabic or Latin letters, like "9adech"), reply in simple Tunisian darija in the same script.
- Be brief and warm: 1 to 4 short sentences, plain text, no markdown. Give prices exactly as \
written, in TND.
{rules}

Capturing leads:
- When the customer shows real interest (booking, a quote, ordering, asking to be called), \
collect: full name, phone number and {lead_need}. Ask only for what's missing, at most two \
things per message. Never ask for an e-mail address or anything not listed here: call \
save_lead as soon as you have them. Don't ask a customer who is just asking questions.
- Once you have name, phone and need, call save_lead, then thank them and say the team will \
call back. If save_lead returns an error, ask the customer to correct that detail.
{lead_status}

Handing off:
- Call handoff_to_human if the customer asks for a person, complains or is upset, has an \
urgent problem, or if you couldn't answer their last two questions.

<business_information>
{context}
</business_information>"""


@dataclass
class Reply:
    text: str | None
    mode: str
    lead_id: int | None = None
    new_lead: bool = False
    handoff: bool = False
    sources: list[dict] = field(default_factory=list)
    limited: bool = False


LIMIT_TEXT = {
    "fr": "Cette conversation de démonstration a atteint sa limite. Merci de l'avoir essayée !",
    "en": "This demo conversation has reached its limit. Thanks for trying it!",
    "ar": "المحادثة التجريبية هذي وصلت للحد متاعها. يعيشك على التجربة!",
}
BUDGET_TEXT = {
    "fr": "La démo a atteint son budget du jour. Revenez demain, ou contactez-nous directement.",
    "en": "The demo has used its budget for today. Please come back tomorrow.",
    "ar": "الديمو وصل للميزانية متاع اليوم. ارجع غدوة إن شاء الله.",
}
BUSY_TEXT = {
    "fr": "Je suis très sollicité en ce moment. Réessayez dans une minute, ou appelez-nous directement.",
    "en": "I'm very busy right now. Please try again in a minute, or call us directly.",
    "ar": "عندي برشا طلبات توا. عاود جرّب بعد دقيقة، ولا كلمنا مباشرة.",
}
HANDOFF_NOTICE = {
    "fr": "Un membre de l'équipe a été prévenu et va vous répondre ici.",
    "en": "A team member has been notified and will answer you here.",
    "ar": "تم إعلام واحد من الفريق، باش يجاوبك هنا.",
}


def _format_context(hits: list[Hit]) -> str:
    if not hits:
        return "(No document matches this question.)"
    return "\n\n".join(f"[{i}] {h.source} — {h.heading}\n{h.content}" for i, h in enumerate(hits, 1))


def _lead_status(conv: Conversation) -> str:
    lead = conv.lead
    if not lead:
        return "- No lead saved yet in this conversation."
    return (
        f"- Lead already saved ({lead.name}, {lead.phone}, need: {lead.need}). Don't ask for "
        "these again; call save_lead only if the customer changes something."
    )


def _channel(conv: Conversation) -> str:
    if conv.channel == "whatsapp" and conv.external_id:
        # WhatsApp already gives the number: no need to ask for it.
        try:
            number = leads.normalize_phone("+" + conv.external_id)
        except leads.InvalidLead:
            number = None
        if number:
            return (
                f"WhatsApp (the customer writes from {number}: use it as their phone for save_lead "
                "unless they give another one, and don't ask for it)"
            )
    return "website chat"


def build_messages(conv: Conversation, tenant: Tenant, history: list[Message], hits: list[Hit]) -> list[dict]:
    today = datetime.now(ZoneInfo(settings.timezone)).strftime("%A %d %B %Y")
    system = SYSTEM_PROMPT.format(
        name=tenant.name,
        activity=tenant.activity,
        city=tenant.city,
        channel=_channel(conv),
        today=today,
        rules=tenant.rules,
        lead_need=tenant.lead_need,
        lead_status=_lead_status(conv),
        context=_format_context(hits),
    )
    messages = [{"role": "system", "content": system}]
    for m in history:
        if m.role == "user":
            messages.append({"role": "user", "content": m.content})
        elif m.role in ("assistant", "human"):
            prefix = "[Team member] " if m.role == "human" else ""
            messages.append({"role": "assistant", "content": prefix + m.content})
    return messages


def _recent_history(db: Session, conv: Conversation) -> list[Message]:
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conv.id, Message.role.in_(("user", "assistant", "human")))
        .order_by(Message.id.desc())
        .limit(settings.history_messages)
    ).all()
    return list(reversed(rows))


def _run_tool(db: Session, conv: Conversation, name: str, args: dict, reply: Reply) -> dict:
    if name == "save_lead":
        try:
            lead, created = leads.save_lead(db, conv, args)
        except leads.InvalidLead as exc:
            return {"ok": False, "error": str(exc)}
        reply.lead_id, reply.new_lead = lead.id, reply.new_lead or created
        db.add(
            Message(
                conversation_id=conv.id,
                role="event",
                content=f"Lead {'enregistré' if created else 'mis à jour'} : {lead.name}, {lead.phone}",
            )
        )
        return {"ok": True, "lead_id": lead.id, "name": lead.name, "phone": lead.phone}
    if name == "handoff_to_human":
        reason = (args.get("reason") or "").strip()[:500] or "Demande du client"
        conv.mode, conv.needs_human, conv.handoff_reason = "human", True, reason
        db.add(Message(conversation_id=conv.id, role="event", content=f"Transfert à l'équipe : {reason}"))
        reply.handoff = True
        return {"handoff": True, "ok": True}
    return {"ok": False, "error": f"unknown tool {name}"}


def notify_handoff(conv: Conversation) -> None:
    tenant = get_tenant(conv.tenant)
    who = conv.customer_name or ("client WhatsApp" if conv.channel == "whatsapp" else "visiteur du site")
    e = html.escape
    mailer.notify_team(
        f"Un client demande un humain : {who} ({tenant.name})",
        f"<p>🙋 <b>{e(who)}</b> demande à parler à quelqu'un de <b>{e(tenant.name)}</b> :</p>"
        f"<p>{e(conv.handoff_reason or '')}</p>"
        f'<p><a href="{leads.admin_url(conv.id)}">Répondre dans le tableau de bord</a></p>',
    )


def _answer(
    db: Session,
    conv: Conversation,
    tenant: Tenant,
    previous: list[Message],
    text: str,
    query: str,
    reply: Reply,
):
    """Retrieve, then let the model answer and run its tools (save_lead, handoff)."""
    hits, usage = search(db, conv.tenant, query)

    messages = build_messages(conv, tenant, previous, hits)
    # Smaller models drift to French or standard Arabic: say explicitly what this message is.
    style = darija_style(text)
    if not style:
        language = {"fr": "French", "en": "English", "ar": "Arabic"}[conv.language or "fr"]
        messages[0]["content"] += f"\n\nThe customer is writing in {language}: reply in {language}."
    else:
        script = (
            "Latin letters with digits for Arabic sounds (3, 7, 9)" if style == "latin" else "Arabic script"
        )
        messages[0]["content"] += (
            f"\n\nThis customer writes Tunisian darija in {script}. Reply in simple Tunisian darija, "
            f"in {script} too, not in French and not in Modern Standard Arabic."
        )
    messages.append({"role": "user", "content": text})
    llm = get_llm()
    content = ""
    for _ in range(MAX_TOOL_ROUNDS):
        result = llm.chat(messages, tools=TOOLS)
        usage += result.usage
        content = result.content
        if not result.tool_calls:
            break
        messages.append(
            {
                "role": "assistant",
                "content": result.content or None,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.name,
                            "arguments": json.dumps(c.arguments, ensure_ascii=False),
                        },
                        **c.extra,
                    }
                    for c in result.tool_calls
                ],
            }
        )
        for call in result.tool_calls:
            output = _run_tool(db, conv, call.name, call.arguments, reply)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(output, ensure_ascii=False)}
            )
    return hits, usage, content


def handle_message(db: Session, conv: Conversation, text: str) -> Reply:
    started = time.perf_counter()
    text = text.strip()[: settings.max_message_chars]
    lang = detect_language(text, conv.language or "fr")
    conv.language = lang
    tenant = get_tenant(conv.tenant)

    if conv.message_count >= settings.max_messages_per_conversation:
        return Reply(text=LIMIT_TEXT[lang], mode=conv.mode, limited=True)
    if settings.llm_provider != "mock" and spent_today(db) >= settings.daily_budget_usd:
        return Reply(text=BUDGET_TEXT[lang], mode=conv.mode, limited=True)

    previous = _recent_history(db, conv)
    db.add(Message(conversation_id=conv.id, role="user", content=text))
    conv.message_count += 1

    if conv.mode == "human":
        # A team member is handling it; the message waits in the dashboard.
        db.commit()
        return Reply(text=None, mode="human")

    # Follow-ups like "and for children?" need the previous question to find the right chunk.
    last_user = next((m.content for m in reversed(previous) if m.role == "user"), "")
    query = f"{last_user}\n{text}" if last_user and len(text) < 60 else text
    reply = Reply(text=None, mode=conv.mode)
    try:
        hits, usage, content = _answer(db, conv, tenant, previous, text, query, reply)
    except LLMUnavailable as exc:
        log.warning("LLM unavailable for conversation %s: %s", conv.id, exc)
        db.add(
            Message(
                conversation_id=conv.id,
                role="event",
                content="Assistant indisponible (limite du fournisseur IA)",
            )
        )
        db.commit()
        reply.text, reply.limited = BUSY_TEXT[lang], True
        return reply
    if not content:
        content = HANDOFF_NOTICE[lang] if reply.handoff else ""

    cost = usage_cost(usage)
    reply.sources = [
        {"source": h.source, "heading": h.heading, "distance": round(h.distance, 3)} for h in hits
    ]
    db.add(
        Message(
            conversation_id=conv.id,
            role="assistant",
            content=content,
            sources=reply.sources,
            prompt_tokens=usage.prompt_tokens,
            cached_tokens=usage.cached_tokens,
            completion_tokens=usage.completion_tokens,
            embedding_tokens=usage.embedding_tokens,
            cost_usd=cost,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    )
    conv.message_count += 1
    conv.cost_usd = (conv.cost_usd or Decimal(0)) + cost
    reply.text, reply.mode = content, conv.mode
    db.commit()
    return reply


def after_reply(conv_id: str, reply: Reply) -> None:
    """Slow side effects (Sheets, e-mail, n8n) run after the customer has the answer."""
    from app.db import SessionLocal
    from app.models import Lead

    with SessionLocal() as db:
        if reply.new_lead and reply.lead_id:
            lead = db.get(Lead, reply.lead_id)
            if lead:
                leads.dispatch(db, lead)
        if reply.handoff:
            conv = db.get(Conversation, conv_id)
            if conv:
                notify_handoff(conv)
