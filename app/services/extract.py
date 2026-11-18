"""Invoice and quote PDFs -> one structured row each (and a row in the sheet)."""

import time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import settings
from app.llm import get_llm
from app.models import ExtractedDocument
from app.services import sheets
from app.services.costs import usage_cost

NUMBER = {"type": ["number", "null"]}
TEXT = {"type": ["string", "null"]}

SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "enum": ["facture", "devis", "avoir", "bon_de_commande", "autre"],
        },
        "supplier": {**TEXT, "description": "Company that issued the document"},
        "supplier_tax_id": {**TEXT, "description": "Matricule fiscal / tax id of the issuer"},
        "number": {**TEXT, "description": "Document number"},
        "date": {**TEXT, "description": "Issue date, YYYY-MM-DD"},
        "currency": {**TEXT, "description": "ISO code, e.g. TND, EUR"},
        "total_ht": {**NUMBER, "description": "Total before tax"},
        "total_tva": {**NUMBER, "description": "Total VAT"},
        "stamp_duty": {**NUMBER, "description": "Timbre fiscal, if any"},
        "total_ttc": {**NUMBER, "description": "Total to pay, tax included"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": NUMBER,
                    "unit_price": NUMBER,
                    "total": NUMBER,
                },
                "required": ["description", "quantity", "unit_price", "total"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "document_type",
        "supplier",
        "supplier_tax_id",
        "number",
        "date",
        "currency",
        "total_ht",
        "total_tva",
        "stamp_duty",
        "total_ttc",
        "lines",
    ],
    "additionalProperties": False,
}

INSTRUCTIONS = """You extract data from business documents (invoices, quotes, credit notes), \
often Tunisian and in French. Read the document and fill every field. Use null when a value \
isn't printed; never guess or compute a value that isn't on the document, except converting \
dates to YYYY-MM-DD. Amounts are plain numbers: "1 234,500 DT" is 1234.5. In Tunisia amounts \
have 3 decimals (millimes). The supplier is the issuer of the document, not the customer."""


def _money(value) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value)).quantize(Decimal("0.001"))
    except InvalidOperation:
        return None


def extract_document(db: Session, pdf: bytes, filename: str) -> ExtractedDocument:
    started = time.perf_counter()
    data, usage = get_llm().extract_pdf(pdf, filename, INSTRUCTIONS, SCHEMA)
    doc = ExtractedDocument(
        filename=filename[:200],
        doc_type=data.get("document_type"),
        supplier=(data.get("supplier") or None) and data["supplier"][:200],
        supplier_tax_id=(data.get("supplier_tax_id") or None) and data["supplier_tax_id"][:40],
        number=(data.get("number") or None) and data["number"][:60],
        date=(data.get("date") or None) and data["date"][:10],
        currency=(data.get("currency") or None) and data["currency"][:8],
        total_ht=_money(data.get("total_ht")),
        total_tva=_money(data.get("total_tva")),
        total_ttc=_money(data.get("total_ttc")),
        data=data,
        cost_usd=usage_cost(usage),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    db.add(doc)
    db.commit()
    added = doc.created_at.astimezone(ZoneInfo(settings.timezone)).strftime("%d/%m/%Y %H:%M")
    doc.sheet_synced = sheets.append_row(
        "Factures",
        [
            added,
            doc.filename,
            doc.doc_type,
            doc.supplier,
            doc.supplier_tax_id,
            doc.number,
            doc.date,
            doc.currency,
            _sheet_number(doc.total_ht),
            _sheet_number(doc.total_tva),
            _sheet_number(_money(data.get("stamp_duty"))),
            _sheet_number(doc.total_ttc),
        ],
    )
    db.commit()
    return doc


def _sheet_number(value: Decimal | None):
    return None if value is None else float(value)
