from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import utcnow
from app.llm import Usage
from app.models import Conversation, ExtractedDocument, Message

MILLION = Decimal(1_000_000)


def usage_cost(usage: Usage) -> Decimal:
    """USD cost of one call, from the token counts the API returned."""
    uncached = usage.prompt_tokens - usage.cached_tokens
    return (
        uncached * settings.price_input_per_m
        + usage.cached_tokens * settings.price_cached_input_per_m
        + usage.completion_tokens * settings.price_output_per_m
        + usage.embedding_tokens * settings.price_embedding_per_m
    ) / MILLION


def spent_today(db: Session) -> Decimal:
    since = utcnow() - timedelta(days=1)
    chat = db.scalar(select(func.coalesce(func.sum(Message.cost_usd), 0)).where(Message.created_at >= since))
    pdf = db.scalar(
        select(func.coalesce(func.sum(ExtractedDocument.cost_usd), 0)).where(
            ExtractedDocument.created_at >= since
        )
    )
    return Decimal(chat) + Decimal(pdf)


def cost_summary(db: Session) -> dict:
    """What running the assistant actually costs, measured on the conversations so far."""
    # Only conversations where the assistant answered at least once count.
    convs = db.execute(
        select(func.count(), func.coalesce(func.sum(Conversation.cost_usd), 0)).where(
            Conversation.cost_usd > 0
        )
    ).one()
    answered = db.execute(
        select(
            func.count(),
            # The median: a few slow answers (a provider at capacity) shouldn't set the figure.
            func.coalesce(func.percentile_cont(0.5).within_group(Message.latency_ms), 0),
            func.coalesce(func.sum(Message.prompt_tokens), 0),
            func.coalesce(func.sum(Message.completion_tokens), 0),
        ).where(Message.role == "assistant", Message.latency_ms.is_not(None))
    ).one()
    n_conv, total_usd = convs[0], Decimal(convs[1])
    per_conv = total_usd / n_conv if n_conv else Decimal(0)
    per_1000_tnd = per_conv * 1000 * settings.usd_to_tnd
    return {
        "model": settings.chat_model if settings.llm_provider != "mock" else "mock",
        "free_tier": settings.llm_free_tier,
        "conversations_measured": n_conv,
        "answers_measured": answered[0],
        "median_answer_seconds": round(float(answered[1]) / 1000, 2),
        "avg_cost_per_conversation_usd": f"{per_conv:.6f}",
        "cost_per_1000_conversations_usd": f"{per_conv * 1000:.2f}",
        "cost_per_1000_conversations_tnd": f"{per_1000_tnd:.2f}",
        "usd_to_tnd": str(settings.usd_to_tnd),
        "tokens": {"prompt": int(answered[2]), "completion": int(answered[3])},
    }
