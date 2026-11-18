import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow

EMBEDDING_DIM = 1536
Money = Numeric(12, 6)  # USD with enough decimals for a single cheap API call


def new_id() -> str:
    return uuid.uuid4().hex


class KnowledgeDocument(Base):
    """A file the business gave us: FAQ, price list, catalog..."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("tenant", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(String(40), index=True)
    source: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    tenant: Mapped[str] = mapped_column(String(40), index=True)
    heading: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_channel_external", "channel", "external_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant: Mapped[str] = mapped_column(String(40), index=True)
    channel: Mapped[str] = mapped_column(String(20))  # web | whatsapp
    # The customer's WhatsApp number (wa_id); None for the web widget.
    external_id: Mapped[str | None] = mapped_column(String(64))
    customer_name: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str | None] = mapped_column(String(8))
    # bot: the assistant answers. human: a team member took over.
    mode: Mapped[str] = mapped_column(String(10), default="bot")
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    handoff_reason: Mapped[str | None] = mapped_column(Text)
    client_key: Mapped[str | None] = mapped_column(String(64), index=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.id",
    )
    lead: Mapped["Lead | None"] = relationship(back_populates="conversation", uselist=False)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # user | assistant | human (a team member) | event (lead saved, handoff...)
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSON)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    embedding_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant: Mapped[str] = mapped_column(String(40), index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True
    )
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(200))
    need: Mapped[str] = mapped_column(Text)
    preferred_time: Mapped[str | None] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(8), default="fr")
    channel: Mapped[str] = mapped_column(String(20))
    # new -> contacted (alert sent) -> followed_up
    status: Mapped[str] = mapped_column(String(20), default="new")
    sheet_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    team_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    n8n_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    followup_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    followup_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    followup_channel: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="lead")


class ExtractedDocument(Base):
    """An invoice or quote PDF turned into one structured row."""

    __tablename__ = "extracted_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(200))
    doc_type: Mapped[str | None] = mapped_column(String(20))
    supplier: Mapped[str | None] = mapped_column(String(200))
    supplier_tax_id: Mapped[str | None] = mapped_column(String(40))
    number: Mapped[str | None] = mapped_column(String(60))
    date: Mapped[str | None] = mapped_column(String(10))  # ISO date as printed
    currency: Mapped[str | None] = mapped_column(String(8))
    total_ht: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    total_tva: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    total_ttc: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    data: Mapped[dict] = mapped_column(JSON)
    sheet_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
