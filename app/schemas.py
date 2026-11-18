from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ChatIn(BaseModel):
    tenant: str = Field(examples=["dentiste"])
    message: str = Field(min_length=1, max_length=4000, examples=["Combien coûte un détartrage ?"])
    conversation_id: str | None = Field(None, description="Omit on the first message")


class Source(BaseModel):
    source: str
    heading: str
    distance: float


class ChatOut(BaseModel):
    conversation_id: str
    reply: str | None = Field(description="None while a team member has the conversation")
    mode: str = Field(description="bot, or human after a handoff")
    lead_captured: bool
    handoff: bool
    sources: list[Source]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    created_at: datetime
    sources: list | None = None
    cost_usd: Decimal | None = None
    latency_ms: int | None = None


class TenantOut(BaseModel):
    slug: str
    name: str
    city: str
    color: str
    welcome: dict[str, str]
    suggestions: dict[str, list[str]]


class LoginIn(BaseModel):
    password: str


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant: str
    conversation_id: str
    name: str
    phone: str
    email: str | None
    need: str
    preferred_time: str | None
    language: str
    channel: str
    status: str
    sheet_synced: bool
    team_notified: bool
    n8n_sent: bool
    followup_due_at: datetime | None
    followup_sent_at: datetime | None
    followup_channel: str | None
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant: str
    channel: str
    customer_name: str | None
    language: str | None
    mode: str
    needs_human: bool
    handoff_reason: str | None
    message_count: int
    cost_usd: Decimal
    created_at: datetime
    updated_at: datetime
    preview: str | None = None
    has_lead: bool = False


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
    lead: LeadOut | None


class HumanReplyIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class KnowledgeDocOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant: str
    source: str
    title: str
    chunks: int
    created_at: datetime


class ExtractedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    doc_type: str | None
    supplier: str | None
    supplier_tax_id: str | None
    number: str | None
    date: str | None
    currency: str | None
    total_ht: Decimal | None
    total_tva: Decimal | None
    total_ttc: Decimal | None
    data: dict
    sheet_synced: bool
    cost_usd: Decimal
    latency_ms: int | None
    created_at: datetime
