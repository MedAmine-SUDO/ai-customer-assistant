import hashlib
import hmac
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.core.config import settings
from app.models import Conversation, Lead, Message
from app.services import mailer
from app.services.whatsapp import valid_signature

WA_ID = "21698765432"
INTERNAL = {"Authorization": "Bearer internal-secret"}


def message(text: str, wa_id: str = WA_ID, msg_id: str | None = None) -> dict:
    msg = {"from": wa_id, "id": msg_id or f"wamid.{uuid4().hex}", "type": "text", "text": {"body": text}}
    return _update(msg, wa_id)


def button(choice: str, wa_id: str = WA_ID) -> dict:
    msg = {"from": wa_id, "id": f"wamid.{uuid4().hex}", "type": "interactive",
           "interactive": {"type": "button_reply", "button_reply": {"id": choice, "title": "x"}}}  # fmt: skip
    return _update(msg, wa_id)


def _update(msg: dict, wa_id: str) -> dict:
    value = {"messaging_product": "whatsapp", "contacts": [{"wa_id": wa_id, "profile": {"name": "Sami"}}],
             "messages": [msg]}  # fmt: skip
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": value}]}],
    }


def send(client, update: dict, secret: str = "wa-app-secret"):
    body = json.dumps(update).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/whatsapp/webhook",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )


def texts(outbox) -> list[tuple[str, str]]:
    return [(to, p["text"]["body"]) for to, p in outbox["whatsapp"] if p["type"] == "text"]


def test_webhook_rejects_a_bad_signature(client):
    assert send(client, message("hi"), secret="wrong").status_code == 401


def test_signature_check():
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"wa-app-secret", body, hashlib.sha256).hexdigest()
    assert valid_signature(body, good)
    assert not valid_signature(body, "sha256=00") and not valid_signature(body, "")


def test_meta_verifies_the_webhook(client):
    params = {"hub.mode": "subscribe", "hub.verify_token": "wa-verify", "hub.challenge": "42"}
    r = client.get("/whatsapp/webhook", params=params)
    assert r.status_code == 200 and r.text == "42"
    params["hub.verify_token"] = "nope"
    assert client.get("/whatsapp/webhook", params=params).status_code == 403


def test_unknown_first_message_offers_both_businesses(client, outbox):
    assert send(client, message("salut")).status_code == 200
    [(to, payload)] = outbox["whatsapp"]
    assert to == WA_ID and payload["type"] == "interactive"
    ids = [b["reply"]["id"] for b in payload["interactive"]["action"]["buttons"]]
    assert ids == ["tenant:dentiste", "tenant:meubles"]
    assert all(len(b["reply"]["title"]) <= 20 for b in payload["interactive"]["action"]["buttons"])


def test_prefilled_greeting_picks_the_business_and_answers(client, outbox, db):
    send(client, message("Bonjour Maison Zitouna"))
    send(client, message("Je voudrais connaître le prix du salon Carthage, combien coûte-t-il ?"))
    to, reply = texts(outbox)[-1]
    assert to == WA_ID and "3 450 TND" in reply
    conv = db.scalar(select(Conversation).where(Conversation.channel == "whatsapp"))
    assert conv.tenant == "meubles" and conv.external_id == WA_ID and conv.customer_name == "Sami"


def test_choosing_a_business_with_a_button(client, outbox):
    send(client, button("tenant:dentiste"))
    assert "Sahel Sourire" in texts(outbox)[-1][1]


def test_the_same_message_delivered_twice_is_answered_once(client, outbox):
    update = message("Bonjour Maison Zitouna, livrez-vous à Sousse ?", msg_id="wamid.dup")
    send(client, update)
    send(client, update)
    assert len(texts(outbox)) == 1


def test_the_assistant_knows_the_number_on_whatsapp(client, db):
    from app.services.assistant import _channel

    send(client, message("Bonjour Sahel Sourire"))
    conv = db.scalar(select(Conversation).where(Conversation.channel == "whatsapp"))
    assert "+216 98 765 432" in _channel(conv)


def _whatsapp_lead(client, db) -> Lead:
    send(client, message("Bonjour Sahel Sourire, je veux un rendez-vous pour un blanchiment"))
    send(client, message("Je m'appelle Sami Ben Ali, 98 765 432"))
    lead = db.scalar(select(Lead))
    assert lead.channel == "whatsapp" and lead.phone == "+216 98 765 432"
    return lead


def test_followup_is_sent_on_whatsapp_inside_the_24h_window(client, outbox, db):
    lead = _whatsapp_lead(client, db)
    r = client.post(f"/internal/leads/{lead.id}/followup", headers=INTERNAL)
    assert r.json()["channel"] == "whatsapp" and r.json()["sent_to_customer"]
    to, text = texts(outbox)[-1]
    assert to == WA_ID and "Sami" in text and "Sahel Sourire" in text


def _close_window(db, lead: Lead) -> None:
    for m in db.scalars(select(Message).where(Message.conversation_id == lead.conversation_id)):
        m.created_at -= timedelta(hours=25)
    db.commit()


def test_after_24h_the_followup_uses_the_approved_template(client, outbox, db, monkeypatch):
    lead = _whatsapp_lead(client, db)
    _close_window(db, lead)
    monkeypatch.setattr(settings, "whatsapp_followup_template", "relance_lead")
    r = client.post(f"/internal/leads/{lead.id}/followup", headers=INTERNAL)
    assert r.json()["sent_to_customer"]
    to, payload = outbox["whatsapp"][-1]
    assert payload["type"] == "template" and payload["template"]["name"] == "relance_lead"
    params = [p["text"] for p in payload["template"]["components"][0]["parameters"]]
    assert params == ["Sami", "Cabinet Dentaire Sahel Sourire"]


def test_after_24h_without_a_template_the_team_is_reminded(client, outbox, db):
    lead = _whatsapp_lead(client, db)
    _close_window(db, lead)
    outbox["email"].clear()
    sent_before = len(outbox["whatsapp"])
    lead.followup_due_at -= timedelta(days=2)
    db.commit()
    [sent] = client.get("/internal/followups", headers=INTERNAL).json()["sent"]
    assert sent["channel"] == "team" and not sent["sent_to_customer"]
    assert len(outbox["whatsapp"]) == sent_before
    [(_, subject, body)] = outbox["email"]
    assert "Relance à faire" in subject and "wa.me/21698765432" in body


def test_team_reply_is_delivered_on_whatsapp(client, outbox, admin, db):
    send(client, message("Bonjour Sahel Sourire, je veux parler à un conseiller"))
    conv = db.scalar(select(Conversation).where(Conversation.channel == "whatsapp"))
    client.post(f"/api/v1/admin/conversations/{conv.id}/reply", json={"message": "Bonjour Sami !"},
                headers=admin)  # fmt: skip
    assert texts(outbox)[-1] == (WA_ID, "👤 Bonjour Sami !")


def test_voice_notes_get_a_polite_answer(client, outbox):
    update = _update({"from": WA_ID, "id": "wamid.audio", "type": "audio", "audio": {"id": "1"}}, WA_ID)
    send(client, update)
    assert "text" in texts(outbox)[-1][1]


def test_html_email_has_a_plain_text_part():
    text = mailer.html_to_text('<p>Lead <b>Ines</b></p><a href="https://x.tn/a">Voir</a>')
    assert text == "Lead Ines\nVoir: https://x.tn/a"
