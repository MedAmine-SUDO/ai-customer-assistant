"""Generate the sample supplier invoices and quotes in samples/ (fictional companies).

uv run python scripts/make_sample_pdfs.py
"""

from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "samples"

DOCS = [
    {
        "file": "facture-medident-2026-0412.pdf",
        "kind": "FACTURE",
        "supplier": "MEDI-DENT DISTRIBUTION SARL",
        "address": "Zone industrielle Sidi Abdelhamid, 4061 Sousse",
        "tax_id": "1456789/B/A/M/000",
        "number": "FA-2026-0412",
        "date": "03/11/2026",
        "customer": "Cabinet Dentaire Sahel Sourire, Khezama Est, Sousse",
        "vat": Decimal("7"),
        "lines": [
            ("Composite nano-hybride A2, seringue 4 g", 10, Decimal("38.500")),
            ("Gants nitrile taille M, boîte de 100", 20, Decimal("14.900")),
            ("Fraises diamantées assorties, kit 10", 3, Decimal("96.000")),
            ("Sachets de stérilisation 90x230, x200", 5, Decimal("22.400")),
        ],
    },
    {
        "file": "devis-bois-du-cap-bon-D2611.pdf",
        "kind": "DEVIS",
        "supplier": "SCIERIE DU CAP BON",
        "address": "Route de Kélibia, Km 3, 8050 Hammamet",
        "tax_id": "0987654/K/A/M/000",
        "number": "D2611-087",
        "date": "12/11/2026",
        "customer": "Maison Zitouna, Ariana",
        "vat": Decimal("19"),
        "lines": [
            ("Hêtre étuvé 27 mm, qualité A (m³)", Decimal("2.5"), Decimal("2150.000")),
            ("Chêne massif 40 mm (m³)", Decimal("1.2"), Decimal("3480.000")),
            ("Panneaux MDF hydrofuge 18 mm 2800x2070", 40, Decimal("118.000")),
            ("Transport et déchargement Ben Arous", 1, Decimal("250.000")),
        ],
    },
    {
        "file": "facture-imprimerie-el-amal-F-3391.pdf",
        "kind": "FACTURE",
        "supplier": "IMPRIMERIE EL AMAL",
        "address": "12 rue de Marseille, 1000 Tunis",
        "tax_id": "1122334/N/M/000",
        "number": "F-3391",
        "date": "28/10/2026",
        "customer": "Maison Zitouna, Ariana",
        "vat": Decimal("19"),
        "lines": [
            ("Catalogue 24 pages A4, couché 170 g", 500, Decimal("6.200")),
            ("Flyers A5 recto-verso", 2000, Decimal("0.180")),
            ("Kakémono 85x200 avec structure", 2, Decimal("145.000")),
        ],
    },
]
STAMP = Decimal("1.000")


def money(v: Decimal) -> str:
    q = v.quantize(Decimal("0.001"))
    whole, frac = f"{q:.3f}".split(".")
    return f"{int(whole):,}".replace(",", " ") + "," + frac


def draw(doc: dict) -> None:
    c = canvas.Canvas(str(OUT / doc["file"]), pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, h - 25 * mm, doc["supplier"])
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, h - 31 * mm, doc["address"])
    c.drawString(20 * mm, h - 36 * mm, f"Matricule fiscal : {doc['tax_id']}")
    c.setFont("Helvetica-Bold", 20)
    c.drawRightString(w - 20 * mm, h - 25 * mm, doc["kind"])
    c.setFont("Helvetica", 10)
    label = "Facture" if doc["kind"] == "FACTURE" else "Devis"
    c.drawRightString(w - 20 * mm, h - 32 * mm, f"{label} N° {doc['number']}")
    c.drawRightString(w - 20 * mm, h - 37 * mm, f"Date : {doc['date']}")
    c.drawString(20 * mm, h - 50 * mm, "Client :")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(35 * mm, h - 50 * mm, doc["customer"])

    y = h - 65 * mm
    c.setFillColor(colors.HexColor("#1f2937"))
    c.rect(20 * mm, y - 2 * mm, w - 40 * mm, 8 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9)
    for x, text in ((22, "Désignation"), (120, "Qté"), (140, "P.U. HT"), (170, "Total HT")):
        c.drawString(x * mm, y, text)
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    total_ht = Decimal(0)
    for desc, qty, pu in doc["lines"]:
        y -= 8 * mm
        line = Decimal(qty) * pu
        total_ht += line
        c.drawString(22 * mm, y, desc)
        c.drawString(120 * mm, y, str(qty).replace(".", ","))
        c.drawString(140 * mm, y, money(pu))
        c.drawString(170 * mm, y, money(line))

    tva = (total_ht * doc["vat"] / 100).quantize(Decimal("0.001"))
    stamp = STAMP if doc["kind"] == "FACTURE" else Decimal(0)
    ttc = total_ht + tva + stamp
    y -= 14 * mm
    rows = [("Total HT", total_ht), (f"TVA {doc['vat']} %", tva)]
    if stamp:
        rows.append(("Timbre fiscal", stamp))
    rows.append(("Total TTC" if doc["kind"] == "FACTURE" else "Total TTC (devis)", ttc))
    for i, (label, value) in enumerate(rows):
        bold = i == len(rows) - 1
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        c.drawString(125 * mm, y, label)
        c.drawRightString(w - 20 * mm, y, f"{money(value)} TND")
        y -= 6 * mm
    c.setFont("Helvetica-Oblique", 8)
    footer = (
        "Devis valable 30 jours. Acompte de 40 % à la commande."
        if doc["kind"] == "DEVIS"
        else "Paiement à 30 jours par virement ou chèque. Merci de votre confiance."
    )
    c.drawString(20 * mm, 20 * mm, footer)
    c.save()


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for d in DOCS:
        draw(d)
        print("wrote", OUT / d["file"])
