"""Load the demo: embed both businesses' documents and add a few example conversations.

python -m app.seed          # idempotent, safe to run on every start
"""

import logging
import sys
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, utcnow
from app.models import Conversation, ExtractedDocument, Lead, Message
from app.services import sheets
from app.services.knowledge import ingest_all

log = logging.getLogger(__name__)
SEED_KEY = "seed"
KEEP_VISITOR_DATA = timedelta(hours=48)

# (tenant, channel, language, minutes ago, messages, lead, handoff reason)
EXAMPLES = [
    (
        "dentiste",
        "web",
        "fr",
        180,
        [
            ("user", "Bonjour, combien coûte un blanchiment ?"),
            (
                "assistant",
                "Bonjour ! Le blanchiment au fauteuil coûte entre 450 et 600 TND, et le "
                "kit à domicile avec gouttières sur mesure 350 TND. Le prix exact est fixé après "
                "l'examen. Souhaitez-vous prendre rendez-vous ?",
            ),
            ("user", "Oui, samedi matin si possible. Je m'appelle Ines Trabelsi, 22 345 678"),
            ("event", "Lead enregistré : Ines Trabelsi, +216 22 345 678"),
            (
                "assistant",
                "Merci Ines ! C'est noté pour samedi matin, l'équipe vous appelle "
                "aujourd'hui pour confirmer l'heure.",
            ),
        ],
        {
            "name": "Ines Trabelsi",
            "phone": "+216 22 345 678",
            "need": "Blanchiment dentaire",
            "preferred_time": "Samedi matin",
        },
        None,
    ),
    (
        "meubles",
        "whatsapp",
        "ar",
        95,
        [
            ("user", "عسلامة، قداش سوم الصالون قرطاج؟"),
            (
                "assistant",
                "مرحبا! الصالون قرطاج (كنبة 3 بلايص + 2 فوتاي) بـ 3 450 دينار، "
                "والتوصيل مجاني في تونس الكبرى. تحب نبعثولك ديفي؟",
            ),
            ("user", "ايه، اسمي سامي، 98 765 432، التوصيل للمنستير"),
            ("event", "Lead enregistré : Sami, +216 98 765 432"),
            ("assistant", "يعيشك سامي! تسجّل طلبك، الفريق باش يتصل بيك اليوم بالديفي والتوصيل للمنستير."),
        ],
        {"name": "Sami", "phone": "+216 98 765 432", "need": "Salon Carthage, livraison Monastir"},
        None,
    ),
    (
        "dentiste",
        "web",
        "fr",
        40,
        [
            ("user", "J'ai très mal à une dent depuis hier soir et la joue a gonflé"),
            ("event", "Transfert à l'équipe : urgence : douleur et gonflement"),
            (
                "assistant",
                "Je suis désolé. Pour une douleur avec gonflement, appelez le cabinet "
                "au +216 73 000 000 : nous gardons des créneaux d'urgence chaque jour. Un membre de "
                "l'équipe a été prévenu et va vous répondre ici.",
            ),
        ],
        None,
        "Urgence : douleur et gonflement",
    ),
    (
        "meubles",
        "web",
        "en",
        15,
        [
            ("user", "Do you deliver to Sousse and how long does it take?"),
            (
                "assistant",
                "Yes, we deliver across Tunisia. Sousse and the Sahel take 5 to 7 "
                "working days, for 60 TND (free above 2,500 TND).",
            ),
        ],
        None,
        None,
    ),
]


def seed_examples(db: Session) -> int:
    if db.scalar(select(Conversation.id).where(Conversation.client_key == SEED_KEY).limit(1)):
        return 0
    now = utcnow()
    for tenant, channel, lang, minutes, messages, lead, handoff in EXAMPLES:
        start = now - timedelta(minutes=minutes)
        conv = Conversation(
            tenant=tenant,
            channel=channel,
            language=lang,
            client_key=SEED_KEY,
            external_id="0" if channel == "whatsapp" else None,
            mode="human" if handoff else "bot",
            needs_human=bool(handoff),
            handoff_reason=handoff,
            customer_name=lead["name"] if lead else None,
            message_count=sum(r in ("user", "assistant") for r, _ in messages),
            created_at=start,
            updated_at=start + timedelta(minutes=2),
        )
        db.add(conv)
        db.flush()
        for i, (role, content) in enumerate(messages):
            db.add(
                Message(
                    conversation_id=conv.id,
                    role=role,
                    content=content,
                    created_at=start + timedelta(seconds=20 * i),
                )
            )
        if lead:
            db.add(
                Lead(
                    tenant=tenant,
                    conversation_id=conv.id,
                    channel=channel,
                    language=lang,
                    status="contacted",
                    team_notified=True,
                    sheet_synced=True,
                    created_at=start + timedelta(minutes=1),
                    followup_due_at=start + timedelta(days=1),
                    **lead,
                )
            )
    db.commit()
    return len(EXAMPLES)


def reset(db: Session) -> dict:
    """Daily on the live demo: drop visitors' data older than 48 h, refresh the examples."""
    cutoff = utcnow() - KEEP_VISITOR_DATA
    old = db.execute(
        delete(Conversation).where(
            Conversation.client_key.is_distinct_from(SEED_KEY), Conversation.created_at < cutoff
        )
    ).rowcount
    docs = db.execute(delete(ExtractedDocument).where(ExtractedDocument.created_at < cutoff)).rowcount
    db.execute(delete(Conversation).where(Conversation.client_key == SEED_KEY))
    db.commit()
    # The public sheet shows the same visitors' leads and invoices: same 48 h.
    sheet_rows = sheets.prune("Leads", cutoff) + sheets.prune("Factures", cutoff)
    return {
        "conversations_deleted": old,
        "extractions_deleted": docs,
        "sheet_rows_deleted": sheet_rows,
        "documents_embedded": ingest_all(db),
        "examples": seed_examples(db),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    with SessionLocal() as db:
        if "--reset" in sys.argv:
            print(reset(db))
            return
        embedded = ingest_all(db)
        examples = seed_examples(db)
    print(f"Knowledge documents embedded: {embedded}, example conversations added: {examples}")


if __name__ == "__main__":
    main()
