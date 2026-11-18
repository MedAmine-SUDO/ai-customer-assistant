import re

ENGLISH_WORDS = {
    "the",
    "how",
    "what",
    "do",
    "you",
    "is",
    "price",
    "much",
    "hello",
    "hi",
    "can",
    "your",
    "have",
    "want",
    "need",
    "please",
    "thanks",
    "where",
    "when",
    "i",
    "i'd",
    "i'm",
    "like",
    "would",
    "for",
    "quote",
    "my",
    "are",
    "and",
    "with",
    "thank",
    "deliver",
    "does",
    "any",
    "there",
}
FRENCH_WORDS = {
    "je",
    "vous",
    "est",
    "le",
    "la",
    "les",
    "bonjour",
    "combien",
    "prix",
    "merci",
    "pour",
    "une",
    "des",
    "avez",
    "êtes",
    "quel",
    "quelle",
    "et",
    "un",
    "du",
    "de",
    "veux",
    "voudrais",
    "pouvez",
    "livrez",
    "avec",
    "oui",
    "c'est",
}
# Tunisian darija typed in Latin letters, often with digits for Arabic sounds (3, 7, 9).
DARIJA_WORDS = {
    "chnowa",
    "chneya",
    "9adech",
    "qadech",
    "kadech",
    "nheb",
    "n7eb",
    "aslema",
    "3aslema",
    "brabi",
    "famma",
    "fama",
    "barcha",
    "yesser",
    "ena",
    "inti",
    "enti",
    "mta3",
    "3andkom",
    "andkom",
    "chkoun",
    "win",
    "wa9tech",
    "taw",
}


# Tunisian darija in Arabic script: words that standard Arabic doesn't use.
DARIJA_ARABIC_WORDS = {
    "قداش", "قدّاش", "نحب", "نحبّ", "برشا", "برشة", "فما", "فمّا", "توا", "توّا", "عسلامة", "شنوة",
    "شنوا", "متاع", "باش", "ياسر", "شكون", "وقتاش", "علاش", "كيفاش", "بالله", "يعيشك", "نجم", "تنجم",
}  # fmt: skip


def darija_style(text: str) -> str | None:
    """ "latin" or "arabic" when the customer writes Tunisian darija, else None."""
    if set(re.findall(r"[؀-ۿ]+", text)) & DARIJA_ARABIC_WORDS:
        return "arabic"
    if not re.search(r"[؀-ۿ]", text) and set(re.findall(r"[a-zà-ÿ0-9']+", text.lower())) & DARIJA_WORDS:
        return "latin"
    return None


def detect_language(text: str, fallback: str = "fr") -> str:
    """fr, en or ar. Darija in Latin letters counts as Arabic: that's who the customer is.
    A message with no clue ("Sarah, 29 000 111") keeps the conversation's language."""
    if re.search(r"[؀-ۿ]", text):
        return "ar"
    words = set(re.findall(r"[a-zà-ÿ0-9']+", text.lower()))
    if words & DARIJA_WORDS:
        return "ar"
    english, french = len(words & ENGLISH_WORDS), len(words & FRENCH_WORDS)
    if english == french:
        return fallback
    return "en" if english > french else "fr"
