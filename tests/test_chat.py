import pytest
from sqlalchemy import select

from app.core.config import settings
from app.llm.base import LLMUnavailable
from app.llm.openai_llm import OpenAILLM
from app.models import Conversation, Lead, Message
from app.services.knowledge import search, split_sections
from tests.conftest import chat


def test_answers_from_the_price_list(client):
    data = chat(client, "Combien coûte un détartrage ?")
    assert "80 à 120 TND" in data["reply"]
    assert data["sources"][0]["source"] == "tarifs.md"
    assert data["mode"] == "bot"
    assert not data["lead_captured"]


def test_each_business_only_sees_its_own_documents(db):
    hits, _ = search(db, "meubles", "détartrage dents prix")
    assert all(h.source in {"catalogue.md", "faq.md", "infos.md"} for h in hits)
    hits, _ = search(db, "meubles", "salon Carthage prix")
    assert hits and "Carthage" in hits[0].content


def test_conversation_continues_with_its_id(client, db):
    first = chat(client, "Vous êtes ouverts le samedi ?")
    second = chat(client, "Et le dimanche ?", first["conversation_id"])
    assert second["conversation_id"] == first["conversation_id"]
    roles = db.scalars(select(Message.role).where(Message.conversation_id == first["conversation_id"])).all()
    assert roles == ["user", "assistant", "user", "assistant"]


def test_every_answer_is_logged_with_its_cost(client, db):
    data = chat(client, "Combien coûte un blanchiment ?")
    answer = db.scalar(
        select(Message).where(Message.conversation_id == data["conversation_id"], Message.role == "assistant")
    )
    assert answer.prompt_tokens > 0 and answer.embedding_tokens > 0
    assert answer.cost_usd > 0
    assert answer.latency_ms is not None
    assert answer.sources
    conv = db.get(Conversation, data["conversation_id"])
    assert conv.cost_usd == answer.cost_usd


def test_lead_is_captured_and_sent_to_sheet_and_team(client, db, outbox):
    first = chat(client, "Je veux un rendez-vous pour un détartrage")
    assert "nom" in first["reply"] and "téléphone" in first["reply"]
    assert not first["lead_captured"]

    second = chat(
        client, "Je m'appelle Ines Trabelsi, mon numéro est le 22 345 678", first["conversation_id"]
    )
    assert second["lead_captured"]
    assert "Ines" in second["reply"]

    lead = db.scalar(select(Lead))
    assert lead.phone == "+216 22 345 678"
    assert lead.tenant == "dentiste" and lead.channel == "web"
    assert "détartrage" in lead.need
    assert lead.sheet_synced and lead.team_notified and lead.status == "contacted"
    assert lead.followup_due_at is not None

    [(tab, row)] = outbox["sheets"]
    assert tab == "Leads"
    assert row[1] == "Cabinet Dentaire Sahel Sourire"
    assert row[3] == "+216 22 ••• 678"  # masked on the public demo
    [(to, subject, body)] = outbox["email"]
    assert to == ["ventes@example.tn"] and subject.startswith("Nouveau lead : Ines Trabelsi")
    assert "+216 22 ••• 678" in body and "/admin#c=" in body


def test_invalid_phone_is_refused_and_asked_again(client, db, outbox):
    first = chat(client, "Je veux un devis")
    second = chat(
        client,
        "je suis Karim, 12 34",
        first["conversation_id"],
    )
    assert not second["lead_captured"]
    assert db.scalar(select(Lead)) is None
    assert outbox["sheets"] == []


def test_handoff_to_human_stops_the_bot(client, db, outbox):
    data = chat(client, "J'ai très mal et ma joue a gonflé, c'est une urgence")
    assert data["handoff"] and data["mode"] == "human"
    assert data["reply"]
    conv = db.get(Conversation, data["conversation_id"])
    assert conv.needs_human and conv.handoff_reason
    assert any("demande un humain" in subject for _, subject, _ in outbox["email"])

    # While a person has the conversation, the bot stays quiet.
    later = chat(client, "Vous êtes là ?", data["conversation_id"])
    assert later["reply"] is None and later["mode"] == "human"


def test_replies_in_the_customers_language(client):
    en = chat(client, "Hi, how much is teeth whitening?")
    assert en["reply"]
    ar = chat(client, "نحب موعد", tenant="meubles")
    assert "اسمك" in ar["reply"]


def test_unknown_business_is_rejected(client):
    r = client.post("/api/v1/chat", json={"tenant": "nope", "message": "hello"})
    assert r.status_code == 404


def test_message_cap_per_conversation(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "max_messages_per_conversation", 2)
    first = chat(client, "Bonjour")
    capped = chat(client, "Encore une question", first["conversation_id"])
    assert "limite" in capped["reply"]


def test_conversations_per_visitor_are_capped(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "max_conversations_per_ip_per_day", 1)
    chat(client, "Bonjour")
    r = client.post("/api/v1/chat", json={"tenant": "dentiste", "message": "Re-bonjour"})
    assert r.status_code == 429


def test_widget_polls_team_replies(client, admin):
    data = chat(client, "Je veux parler à un conseiller")
    conv_id = data["conversation_id"]
    r = client.post(
        f"/api/v1/admin/conversations/{conv_id}/reply", json={"message": "Bonjour, ici Salma."}, headers=admin
    )
    assert r.status_code == 200
    rows = client.get(f"/api/v1/chat/{conv_id}/messages").json()
    assert rows[-1]["role"] == "human" and rows[-1]["content"] == "Bonjour, ici Salma."
    after = client.get(f"/api/v1/chat/{conv_id}/messages", params={"after_id": rows[-1]["id"]}).json()
    assert after == []


def test_markdown_is_split_by_heading():
    sections = split_sections(
        "# Titre\n\n## Horaires\nLundi\n\n## Prix\n### Soins\nDétartrage 80 TND", "Titre"
    )
    assert [s.heading for s in sections] == ["Horaires", "Prix › Soins"]


def test_a_busy_provider_gets_a_polite_answer(client, db, monkeypatch):
    from app.llm import LLMUnavailable, get_llm

    def busy(*args, **kwargs):
        raise LLMUnavailable("429 quota exceeded")

    monkeypatch.setattr(get_llm(), "chat", busy)
    data = chat(client, "Combien coûte un détartrage ?")
    assert "Réessayez dans une minute" in data["reply"]
    events = db.scalars(select(Message).where(Message.role == "event")).all()
    assert any("indisponible" in m.content for m in events)


def test_an_overloaded_model_falls_back_to_the_backup(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "fallback_model", "backup-model")
    llm = OpenAILLM()
    tried = []

    def create(model, **kwargs):
        tried.append(model)
        if model == "main-model":
            raise LLMUnavailable("503")
        return "answer"

    monkeypatch.setattr(llm, "_complete_once", create)
    assert llm._complete("main-model", messages=[]) == "answer"
    assert tried == ["main-model", "backup-model"]

    monkeypatch.setattr(settings, "fallback_model", "")
    with pytest.raises(LLMUnavailable):
        llm._complete("main-model", messages=[])


@pytest.mark.parametrize(
    ("text", "style"),
    [
        ("9adech salon Carthage ?", "latin"),
        ("قداش الصالون قرطاج؟", "arabic"),
        ("كم سعر الصالون؟", None),
        ("Combien coûte un détartrage ?", None),
    ],
)
def test_darija_is_spotted_in_both_scripts(text, style):
    from app.services.language import darija_style

    assert darija_style(text) == style


def test_provider_fields_on_a_tool_call_are_sent_back(client, monkeypatch):
    """Gemini 3 rejects the follow-up unless the tool call keeps its thought_signature."""
    from app.llm import ChatResult, ToolCall, get_llm

    signature = {"extra_content": {"google": {"thought_signature": "sig"}}}
    seen = []

    def fake_chat(messages, tools=None):
        seen.append(messages)
        if len(seen) == 1:
            args = {"name": "Ines Trabelsi", "phone": "22 345 678", "need": "Blanchiment"}
            return ChatResult(content="", tool_calls=[ToolCall("call_1", "save_lead", args, signature)])
        return ChatResult(content="C'est noté, Ines !")

    monkeypatch.setattr(get_llm(), "chat", fake_chat)
    data = chat(client, "Ines Trabelsi, 22 345 678, blanchiment")
    assert data["lead_captured"] and data["reply"] == "C'est noté, Ines !"
    [assistant] = [m for m in seen[1] if m.get("tool_calls")]
    assert assistant["tool_calls"][0]["extra_content"] == signature["extra_content"]


@pytest.mark.parametrize(
    ("text", "fallback", "expected"),
    [
        ("I'd like a quote for a kitchen, 3 metres", "fr", "en"),
        ("Sarah, +216 29 000 111", "en", "en"),
        ("Je veux un devis pour un dressing", "en", "fr"),
        ("9adech salon ?", "fr", "ar"),
    ],
)
def test_language_follows_the_conversation_when_a_message_has_no_clue(text, fallback, expected):
    from app.services.language import detect_language

    assert detect_language(text, fallback) == expected


@pytest.mark.parametrize(
    "url",
    [
        "postgres://u:p@ep-x.neon.tech/db?sslmode=require",
        "postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
    ],
)
def test_provider_database_urls_use_psycopg3(url):
    from app.core.config import Settings

    assert (
        Settings(database_url=url).database_url
        == "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require"
    )
