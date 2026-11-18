"""A keyless stand-in for the LLM, used by the tests and for running the app without an API key.

It is deliberately simple but behaves like the real assistant on the happy paths: hashed
bag-of-words embeddings (good enough to find the right FAQ entry), answers taken from the
retrieved context, the lead and handoff tools called with real arguments.
"""

import hashlib
import io
import json
import math
import re
import unicodedata
import uuid

from app.llm.base import ChatResult, ToolCall, Usage
from app.models import EMBEDDING_DIM
from app.services.language import detect_language

STOPWORDS = set(
    [
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "de",
        "du",
        "d",
        "l",
        "et",
        "ou",
        "a",
        "au",
        "aux",
        "en",
        "est",
        "ce",
        "que",
        "qui",
        "pour",
        "par",
        "sur",
        "avec",
        "vous",
        "je",
        "j",
        "me",
        "m",
        "mon",
        "ma",
        "mes",
        "il",
        "elle",
        "on",
        "nous",
        "votre",
        "vos",
        "the",
        "a",
        "an",
        "of",
        "to",
        "and",
        "or",
        "is",
        "are",
        "you",
        "i",
        "my",
        "do",
        "does",
        "for",
        "in",
        "on",
        "with",
        "what",
        "how",
        "much",
    ]
)
PHONE_RE = re.compile(r"(?:\+|00)?(?:216[\s.-]?)?[2-9]\d(?:[\s.-]?\d){6}\b")
NAME_RE = re.compile(
    r"(?:je m'appelle|je suis|moi c'est|my name is|i am|i'm|ismi|esmi|اسمي|انا)\s+"
    r"([^\s,.!?\d]+(?:\s+[A-ZÀ-Ý][^\s,.!?\d]+)?)",
    re.IGNORECASE,
)
INTENT_WORDS = (
    "rendez-vous",
    "rdv",
    "réserver",
    "devis",
    "commander",
    "acheter",
    "livraison chez",
    "rappeler",
    "appointment",
    "book",
    "quote",
    "order",
    "buy",
    "call me",
    "موعد",
    "نحب",
    "ديفي",
    "nheb",
    "n7eb",
)
HANDOFF_WORDS = (
    "humain",
    "conseiller",
    "une personne",
    "réclamation",
    "urgence",
    "très mal",
    "saigne",
    "human",
    "agent",
    "complaint",
    "emergency",
    "عاجل",
    "نحكي مع",
    "شكوى",
)

PRICE_WORDS = ("combien", "prix", "cout", "tarif", "how much", "price", "cost", "قداش", "سوم", "9adech")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[\w؀-ۿ]+", _normalize(text))
    return [w.rstrip("s") if len(w) > 3 else w for w in words if w not in STOPWORDS]


def embed_text(text: str) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    for tok in _tokens(text):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        # Longer words carry more meaning ("détartrage" vs "prix"): a crude stand-in for IDF.
        weight = max(1, len(tok) - 3) ** 1.5
        vec[h % EMBEDDING_DIM] += weight
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


REPLIES = {
    "ask": {
        "fr": "Avec plaisir ! Pour que l'équipe vous recontacte, il me manque : {missing}.",
        "en": "Sure! So the team can get back to you, I just need: {missing}.",
        "ar": "باهي! باش الفريق يتصل بيك، نحتاج: {missing}.",
    },
    "fields": {
        "fr": {"name": "votre nom", "phone": "votre numéro de téléphone"},
        "en": {"name": "your name", "phone": "your phone number"},
        "ar": {"name": "اسمك", "phone": "رقم تليفونك"},
    },
    "lead_ok": {
        "fr": "Merci {name} ! C'est noté, l'équipe vous rappelle très vite au {phone}.",
        "en": "Thank you {name}! It's noted, the team will call you soon on {phone}.",
        "ar": "شكرا {name}! تسجّل طلبك، الفريق باش يتصل بيك قريب على {phone}.",
    },
    "lead_error": {
        "fr": "Ce numéro ne semble pas valide, pouvez-vous le vérifier ?",
        "en": "That phone number doesn't look right, could you check it?",
        "ar": "الرقم هذا موش صحيح، تنجم تثبت فيه؟",
    },
    "handoff": {
        "fr": "Je transmets votre demande à un membre de l'équipe, il vous répond ici même.",
        "en": "I'm passing you to a member of the team; they will answer you right here.",
        "ar": "باش نحوّل طلبك لواحد من الفريق، باش يجاوبك هنا.",
    },
    "unknown": {
        "fr": "Je n'ai pas cette information. Voulez-vous que je transmette votre question à l'équipe ?",
        "en": "I don't have that information. Shall I pass your question to the team?",
        "ar": "ما عنديش المعلومة هذي. تحب نبعث سؤالك للفريق؟",
    },
}


def _context_chunks(system: str) -> list[str]:
    match = re.search(r"<business_information>(.*?)</business_information>", system, re.S)
    if not match:
        return []
    parts = re.split(r"\n(?=\[\d+\] )", match.group(1).strip())
    return [p for p in parts if p.startswith("[")]


class MockLLM:
    name = "mock"

    def embed(self, texts: list[str]) -> tuple[list[list[float]], Usage]:
        tokens = sum(len(t) // 4 for t in texts)
        return [embed_text(t) for t in texts], Usage(embedding_tokens=tokens)

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResult:
        usage = Usage(
            prompt_tokens=sum(len(str(m.get("content", ""))) // 4 for m in messages),
            completion_tokens=40,
        )
        user_texts = [m["content"] for m in messages if m["role"] == "user"]
        lang = detect_language(user_texts[-1] if user_texts else "")
        last = messages[-1]

        if last["role"] == "tool":
            result = json.loads(last["content"])
            if result.get("handoff"):
                key = "handoff"
            elif result.get("ok"):
                key = "lead_ok"
            else:
                key = "lead_error"
            text = REPLIES[key][lang].format(name=result.get("name", ""), phone=result.get("phone", ""))
            return ChatResult(content=text, usage=usage)

        text = user_texts[-1]
        norm = _normalize(text)
        if any(_normalize(w) in norm for w in HANDOFF_WORDS):
            return self._call("handoff_to_human", {"reason": text[:200]}, usage)

        intent_msgs = [u for u in user_texts if any(_normalize(w) in _normalize(u) for w in INTENT_WORDS)]
        if intent_msgs:
            joined = " ".join(user_texts)
            # The latest values win: the customer may have corrected them.
            phone = ([*PHONE_RE.finditer(joined)] or [None])[-1]
            name = ([*NAME_RE.finditer(joined)] or [None])[-1]
            if phone and name:
                args = {
                    "name": name.group(1).strip().title(),
                    "phone": phone.group(0),
                    "need": intent_msgs[0][:200],
                    "email": None,
                    "preferred_time": None,
                    "language": lang,
                }
                return self._call("save_lead", args, usage)
            fields = REPLIES["fields"][lang]
            missing = [fields[k] for k, found in (("name", name), ("phone", phone)) if not found]
            return ChatResult(content=REPLIES["ask"][lang].format(missing=", ".join(missing)), usage=usage)

        chunks = _context_chunks(messages[0]["content"])
        if not chunks:
            return ChatResult(content=REPLIES["unknown"][lang], usage=usage)
        # A price question wants the chunk with prices, like the real model would pick.
        if any(w in norm for w in PRICE_WORDS):
            chunks = [c for c in chunks if "TND" in c] or chunks
        body = chunks[0].split("\n", 1)[-1].strip()
        return ChatResult(content=body[:400], usage=usage)

    @staticmethod
    def _call(name: str, args: dict, usage: Usage) -> ChatResult:
        return ChatResult(
            content="",
            tool_calls=[ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=args)],
            usage=usage,
        )

    def extract_pdf(self, pdf: bytes, filename: str, instructions: str, schema: dict) -> tuple[dict, Usage]:
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        def amount(label: str) -> float | None:
            m = re.search(label + r"[^\d\n]*([\d\s]+[.,]\d{3})", text, re.IGNORECASE)
            return float(m.group(1).replace(" ", "").replace(",", ".")) if m else None

        number = re.search(r"(?:Facture|Devis|Invoice|Quote)\s*N[°o]?\s*:?\s*([\w/-]+)", text, re.I)
        date = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
        tax_id = re.search(r"\b(\d{7}/?[A-Z]/?[A-Z]/?[A-Z]?/?\d{3})\b", text)
        data = {
            "document_type": "devis" if re.search(r"\bdevis\b", text, re.I) else "facture",
            "supplier": lines[0] if lines else None,
            "supplier_tax_id": tax_id.group(1) if tax_id else None,
            "number": number.group(1) if number else None,
            "date": f"{date.group(3)}-{date.group(2)}-{date.group(1)}" if date else None,
            "currency": "TND" if re.search(r"TND|DT\b|dinar", text, re.I) else None,
            "total_ht": amount(r"Total\s*HT"),
            "total_tva": amount(r"TVA\s*[\d.,]*\s*%"),
            "stamp_duty": amount(r"Timbre"),
            "total_ttc": amount(r"(?:Total\s*TTC|Net\s*à\s*payer)"),
            "lines": [],
        }
        return data, Usage(prompt_tokens=len(text) // 4, completion_tokens=150)
