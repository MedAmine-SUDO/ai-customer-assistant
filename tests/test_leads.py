from datetime import UTC, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db import utcnow
from app.models import Lead
from app.services.leads import InvalidLead, followup_text, normalize_phone
from tests.conftest import chat

INTERNAL = {"Authorization": "Bearer internal-secret"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("22345678", "+216 22 345 678"),
        ("22 345 678", "+216 22 345 678"),
        ("+216 98.765.432", "+216 98 765 432"),
        ("0021671000000", "+216 71 000 000"),
        ("+33 6 12 34 56 78", "+33612345678"),
    ],
)
def test_phone_numbers_are_normalized(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "1234", "12345678", "+216 1234", "hello"])
def test_bad_phone_numbers_are_refused(raw):
    with pytest.raises(InvalidLead):
        normalize_phone(raw)


def _make_lead(client) -> int:
    first = chat(client, "Je veux un rendez-vous pour un blanchiment")
    chat(client, "Je m'appelle Ines, 22 345 678", first["conversation_id"])
    return first["conversation_id"]


def test_correcting_details_updates_the_same_lead(client, db, outbox):
    conv_id = _make_lead(client)
    chat(client, "Je veux un rendez-vous. Je m'appelle Ines, 55 111 222", conv_id)
    leads = db.scalars(select(Lead)).all()
    assert len(leads) == 1 and leads[0].phone == "+216 55 111 222"
    # The team was alerted once, for the new lead, not for the correction.
    assert len(outbox["sheets"]) == 1


def test_n8n_followup_for_a_web_lead_returns_a_whatsapp_link(client, db):
    _make_lead(client)
    lead = db.scalar(select(Lead))
    r = client.post(f"/internal/leads/{lead.id}/followup", headers=INTERNAL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel"] == "team" and not body["sent_to_customer"]
    assert body["whatsapp_link"].startswith("https://wa.me/21622345678?text=")
    assert "Ines" in body["text"]
    # Calling it twice (n8n retry) doesn't send it twice.
    assert client.post(f"/internal/leads/{lead.id}/followup", headers=INTERNAL).json()["already_sent"]


def test_internal_endpoints_need_the_secret(client):
    assert client.post("/internal/leads/1/followup").status_code == 401
    assert client.get("/internal/followups", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_due_followups_are_sent_by_the_schedule(client, db, outbox):
    _make_lead(client)
    lead = db.scalar(select(Lead))
    lead.followup_due_at = utcnow() - timedelta(minutes=1)
    db.commit()
    outbox["email"].clear()

    r = client.get("/internal/followups", headers=INTERNAL)
    [sent] = r.json()["sent"]
    assert sent["lead_id"] == lead.id
    [(to, subject, body)] = outbox["email"]
    assert to == ["ventes@example.tn"] and subject == "Relance à faire : Ines" and "wa.me/216" in body
    db.refresh(lead)
    assert lead.status == "followed_up" and lead.followup_sent_at


def test_followup_waits_for_n8n_when_n8n_runs_it(client, monkeypatch):
    monkeypatch.setattr(settings, "lead_sinks", "sheets,n8n")
    assert client.get("/internal/followups", headers=INTERNAL).json() == {"skipped": "n8n handles follow-ups"}


def test_n8n_receives_the_lead(client, db, monkeypatch):
    import httpx

    posted = []

    class Resp:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(settings, "lead_sinks", "n8n")
    monkeypatch.setattr(settings, "n8n_lead_webhook_url", "http://n8n:5678/webhook/lead")
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: posted.append((url, json)) or Resp())
    _make_lead(client)
    [(url, payload)] = posted
    assert url.endswith("/webhook/lead")
    assert payload["business"] == "Cabinet Dentaire Sahel Sourire"
    assert payload["followup_url"].endswith(f"/internal/leads/{payload['lead_id']}/followup")
    assert payload["followup_after_seconds"] == settings.followup_delay_minutes * 60
    assert db.scalar(select(Lead)).n8n_sent


def test_followup_is_written_in_the_leads_language(db):
    lead = Lead(
        tenant="meubles",
        conversation_id="x",
        name="Sami Ben Ali",
        phone="+216 98 765 432",
        need="Salon Carthage",
        language="ar",
        channel="web",
    )
    text = followup_text(lead)
    assert "سامي" not in text and "Sami" in text and "دار الزيتونة" in text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("+216 22 ••• 678", "'+216 22 ••• 678"),
        ('=IMPORTXML("http://x", "//a")', '\'=IMPORTXML("http://x", "//a")'),
        ("-5", "'-5"),
        ("Ines Trabelsi", "Ines Trabelsi"),
        (1234.5, 1234.5),
        (None, ""),
    ],
)
def test_sheet_cells_never_run_as_formulas(value, expected):
    from app.services.sheets import cell

    assert cell(value) == expected


def test_public_sheet_masks_and_pruning():
    from datetime import datetime

    from app.services import sheets
    from app.services.leads import mask_email, mask_name

    assert mask_email("sarah.b@gmail.com") == "sa•••@gmail.com"
    assert mask_email(None) is None
    assert mask_name("Ines Trabelsi") == "Ines T."
    assert mask_name("Karim") == "Karim"
    # Sheet rows (after the header) older than the cutoff are found; unreadable dates are kept.
    dates = [["20/11/2026 10:00"], ["21/11/2026 09:00"], ["bad"], ["22/11/2026 12:00"]]
    cutoff = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    assert sheets.old_row_indexes(dates, cutoff) == [1, 2]
