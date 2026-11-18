from pathlib import Path

from sqlalchemy import func, select

from app.models import Conversation, ExtractedDocument, Lead
from app.seed import reset, seed_examples
from tests.conftest import chat

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def test_dashboard_needs_login(client):
    assert client.get("/api/v1/admin/leads").status_code == 401
    assert client.post("/api/v1/admin/login", json={"password": "wrong"}).status_code == 401


def test_overview_counts_and_costs(client, admin):
    first = chat(client, "Je veux un rendez-vous pour un détartrage")
    chat(client, "Je m'appelle Ines, 22 345 678", first["conversation_id"])
    chat(client, "Vous livrez à Sousse ?", tenant="meubles")
    body = client.get("/api/v1/admin/overview", headers=admin).json()
    assert body["conversations"] == 2 and body["leads"] == 1
    assert body["by_tenant"]["dentiste"] == {"conversations": 1, "leads": 1}
    assert body["costs"]["conversations_measured"] == 2
    assert float(body["costs"]["cost_per_1000_conversations_tnd"]) > 0


def test_leads_list_masks_phones_on_the_demo(client, admin):
    first = chat(client, "Je veux un devis pour une cuisine", tenant="meubles")
    chat(client, "Je m'appelle Sami, 98 765 432", first["conversation_id"], tenant="meubles")
    [lead] = client.get("/api/v1/admin/leads", headers=admin).json()
    assert lead["name"] == "Sami" and lead["phone"] == "+216 98 ••• 432"


def test_conversation_review_and_takeover(client, admin, db):
    data = chat(client, "Je veux parler à un humain")
    conv_id = data["conversation_id"]
    waiting = client.get("/api/v1/admin/conversations", params={"needs_human": True}, headers=admin).json()
    assert [c["id"] for c in waiting] == [conv_id]

    detail = client.get(f"/api/v1/admin/conversations/{conv_id}", headers=admin).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "event", "assistant"]

    client.post(f"/api/v1/admin/conversations/{conv_id}/release", headers=admin)
    assert db.get(Conversation, conv_id).mode == "bot"
    again = chat(client, "Vous êtes ouverts le samedi ?", conv_id)
    assert again["reply"] and again["mode"] == "bot"


def test_upload_a_document_and_ask_about_it(client, admin):
    doc = "# Promo\n\n## Offre de novembre\nPour tout détartrage en novembre, le polissage est offert."
    r = client.post(
        "/api/v1/admin/knowledge/dentiste",
        files={"file": ("promo.md", doc.encode(), "text/markdown")},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["chunks"] == 1
    docs = client.get("/api/v1/admin/knowledge", params={"tenant": "dentiste"}, headers=admin).json()
    assert "promo.md" in {d["source"] for d in docs}
    answer = chat(client, "Offre novembre polissage ?")
    assert "offert" in answer["reply"]

    client.delete(f"/api/v1/admin/knowledge/{r.json()['id']}", headers=admin)
    docs = client.get("/api/v1/admin/knowledge", params={"tenant": "dentiste"}, headers=admin).json()
    assert "promo.md" not in {d["source"] for d in docs}


def test_invoice_pdf_becomes_a_row(client, admin, outbox, db):
    pdf = (SAMPLES / "facture-medident-2026-0412.pdf").read_bytes()
    r = client.post("/api/v1/admin/extract", files={"file": ("facture.pdf", pdf, "application/pdf")},
                    headers=admin)  # fmt: skip
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["supplier"] == "MEDI-DENT DISTRIBUTION SARL"
    assert row["number"] == "FA-2026-0412" and row["date"] == "2026-11-03"
    assert row["supplier_tax_id"] == "1456789/B/A/M/000"
    assert row["total_ht"] == "1083.000" and row["total_ttc"] == "1159.810"
    assert row["sheet_synced"]
    [(tab, sheet_row)] = outbox["sheets"]
    assert tab == "Factures" and sheet_row[3] == "MEDI-DENT DISTRIBUTION SARL"
    assert db.scalar(select(func.count()).select_from(ExtractedDocument)) == 1


def test_extract_refuses_non_pdf(client, admin):
    r = client.post("/api/v1/admin/extract", files={"file": ("x.pdf", b"hello", "application/pdf")},
                    headers=admin)  # fmt: skip
    assert r.status_code == 415


def test_demo_examples_and_reset(client, db):
    assert seed_examples(db) == 4
    assert seed_examples(db) == 0  # idempotent
    assert db.scalar(select(func.count()).select_from(Lead)) == 2
    chat(client, "Bonjour")
    result = reset(db)
    # Today's visitor conversation is kept (48 h), the examples are rebuilt.
    assert result["conversations_deleted"] == 0 and result["examples"] == 4
    assert db.scalar(select(func.count()).select_from(Conversation)) == 5


def test_reset_endpoint_runs_with_the_cron_secret(client):
    r = client.get("/internal/reset-demo", headers={"Authorization": "Bearer internal-secret"})
    assert r.status_code == 200, r.text
    assert r.json()["reset"]["examples"] == 4


def test_public_stats_have_no_personal_data(client):
    chat(client, "Combien coûte un détartrage ?")
    body = client.get("/api/v1/stats").json()
    assert body["conversations_measured"] == 1
    assert "cost_per_1000_conversations_tnd" in body


def test_demo_pages_are_served(client):
    for path in ("/", "/admin", "/demo/dentiste", "/demo/meubles", "/widget.js"):
        assert client.get(path).status_code == 200, path
    assert client.get("/demo/nope").status_code == 404


def test_transcripts_mask_phones_on_the_demo(client, admin):
    first = chat(client, "Je veux un rendez-vous")
    chat(client, "Je m'appelle Ines, 22 345 678", first["conversation_id"])
    detail = client.get(f"/api/v1/admin/conversations/{first['conversation_id']}", headers=admin).json()
    text = " ".join(m["content"] for m in detail["messages"])
    assert "22 345 678" not in text and "22 ••• 678" in text
