"""The demo businesses. A real client is one more entry here plus a folder in knowledge/."""

from dataclasses import dataclass, field
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"


@dataclass(frozen=True)
class Tenant:
    slug: str
    name: str
    activity: str
    city: str
    phone: str
    # Extra rules for the assistant, specific to the trade.
    rules: str
    # What a lead's "need" looks like here, so the assistant knows what to ask.
    lead_need: str
    welcome: dict[str, str]
    suggestions: dict[str, list[str]]
    followup: dict[str, str]
    color: str = "#0f766e"
    # For WhatsApp: the button title (20 characters max) and words that pick this business
    # from a customer's first message (the demo's wa.me links prefill one).
    short_name: str = ""
    keywords: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    @property
    def knowledge_dir(self) -> Path:
        return KNOWLEDGE_DIR / self.slug


TENANTS: dict[str, Tenant] = {
    t.slug: t
    for t in [
        Tenant(
            slug="dentiste",
            name="Cabinet Dentaire Sahel Sourire",
            short_name="🦷 Sahel Sourire",
            keywords=("sourire", "dentaire", "dentiste", "dentist"),
            activity="a dental practice (general dentistry, orthodontics, implants, whitening)",
            city="Khezama, Sousse",
            phone="+216 73 000 000",
            rules=(
                "- Never diagnose or give medical advice beyond what the documents say. For pain, "
                "swelling, bleeding or a broken tooth, give the emergency instructions from the "
                "documents and call handoff_to_human with reason 'urgence'.\n"
                "- Prices are indicative ranges; say the exact price is set after the examination.\n"
                "- Booking an appointment counts as buying intent."
            ),
            lead_need="the treatment or reason for the visit, and a preferred day/time if given",
            welcome={
                "fr": "Bonjour 👋 Je suis l'assistant du Cabinet Sahel Sourire. Tarifs, horaires, "
                "rendez-vous : posez-moi votre question.",
                "en": "Hi 👋 I'm the Sahel Sourire dental clinic assistant. Ask me about prices, "
                "opening hours or booking a visit.",
                "ar": "مرحبا 👋 أنا المساعد الآلي لعيادة ساحل سوريـر لطب الأسنان. اسألني عن "
                "الأسعار، المواعيد أو أوقات العمل.",
            },
            suggestions={
                "fr": [
                    "Combien coûte un détartrage ?",
                    "Vous êtes ouverts le samedi ?",
                    "Je veux un rendez-vous",
                ],
                "en": ["How much is teeth whitening?", "Do you accept CNAM?"],
                "ar": ["قداش يكلف تقويم الأسنان؟"],
            },
            followup={
                "fr": "Bonjour {name}, ici le Cabinet Sahel Sourire. Suite à votre demande "
                "d'hier ({need}), souhaitez-vous que nous fixions votre rendez-vous ? "
                "Répondez simplement à ce message ou appelez le {phone}.",
                "en": "Hello {name}, this is Sahel Sourire dental clinic. Following your "
                "request yesterday ({need}), shall we book your appointment? Just reply to "
                "this message or call {phone}.",
                "ar": "مرحبا {name}، معاك عيادة ساحل سوريـر. بخصوص طلبك البارح ({need})، "
                "تحب نثبتولك موعد؟ جاوب على الرسالة هذي ولا اتصل بـ {phone}.",
            },
            color="#0e7490",
        ),
        Tenant(
            slug="meubles",
            name="Maison Zitouna",
            short_name="🛋️ Maison Zitouna",
            keywords=("zitouna", "meuble", "furniture"),
            activity="a furniture and home decor shop (living rooms, bedrooms, dining, made to measure)",
            city="Ariana, Grand Tunis",
            phone="+216 70 000 000",
            rules=(
                "- Only quote products, dimensions, prices and lead times that appear in the "
                "documents. For a made-to-measure request, collect the need and say a quote will "
                "follow.\n"
                "- A quote request, an order, a showroom visit or a delivery question for a "
                "specific product counts as buying intent."
            ),
            lead_need="the product(s) or project, dimensions or budget if given, and the delivery town",
            welcome={
                "fr": "Bonjour 👋 Bienvenue chez Maison Zitouna. Prix, délais de livraison, "
                "sur-mesure : je vous réponds tout de suite.",
                "en": "Hi 👋 Welcome to Maison Zitouna. Prices, delivery times, made to measure: ask away.",
                "ar": "مرحبا بيك في دار الزيتونة 👋 الأسعار، التوصيل، التفصيل على القياس: اسألني.",
            },
            suggestions={
                "fr": [
                    "Prix du salon Carthage ?",
                    "Vous livrez à Sousse ?",
                    "Je veux un devis pour une cuisine",
                ],
                "en": ["Do you have a 160 cm bed?", "Can I pay in instalments?"],
                "ar": ["عندكم طاولة أكل لـ 8 أشخاص؟"],
            },
            followup={
                "fr": "Bonjour {name}, ici Maison Zitouna. Suite à votre demande d'hier "
                "({need}), voulez-vous recevoir votre devis ou passer au showroom ? Répondez à "
                "ce message ou appelez le {phone}.",
                "en": "Hello {name}, this is Maison Zitouna. Following your request yesterday "
                "({need}), would you like your quote or a showroom visit? Reply here or call "
                "{phone}.",
                "ar": "مرحبا {name}، معاك دار الزيتونة. بخصوص طلبك البارح ({need})، تحب نبعثولك "
                "الديفي ولا تجي تزور المعرض؟ جاوب هنا ولا اتصل بـ {phone}.",
            },
            color="#a16207",
        ),
    ]
}

DEFAULT_TENANT = "dentiste"


def get_tenant(slug: str | None) -> Tenant:
    return TENANTS.get(slug or DEFAULT_TENANT) or TENANTS[DEFAULT_TENANT]
