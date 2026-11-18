"""Measure what the assistant costs: play scripted conversations against a running instance,
then print the cost per conversation as the app logged it.

    uv run python scripts/benchmark.py http://localhost:8000 --rounds 2

Each conversation is 3 to 4 customer messages, in French, Arabic (darija) and English, about
prices and opening hours, ending in a lead or a handoff: a realistic mix, not a best case.
Point it at your own instance (it counts against the per-visitor limit of a public demo).
"""

import argparse
import statistics
import time

import httpx

SCRIPTS = [
    (
        "dentiste",
        [
            "Bonjour, combien coûte un détartrage ?",
            "Et un blanchiment ?",
            "Je voudrais un rendez-vous samedi matin",
            "Ines Trabelsi, 22 345 678",
        ],
    ),
    ("dentiste", ["Vous êtes ouverts le dimanche ?", "J'ai très mal à une dent et ma joue a gonflé"]),
    ("dentiste", ["قداش يكلف تقويم الأسنان؟", "نجم نخلص بالتقسيط؟", "نحب موعد، اسمي سامي 98765432"]),
    ("dentiste", ["Hi, do you accept CNAM?", "How long does an implant take?", "Thanks!"]),
    (
        "meubles",
        [
            "Prix du salon Carthage ?",
            "Vous livrez à Sousse ? Combien ?",
            "Je veux le commander",
            "Karim Ben Salah, 55 111 222, livraison à Sousse",
        ],
    ),
    ("meubles", ["9adech table testour ?", "famma livraison l sfax ?", "merci"]),
    (
        "meubles",
        [
            "Do you have a 160 cm bed?",
            "What's the warranty?",
            "I'd like a quote for a kitchen, 3 metres",
            "Sarah, +216 29 000 111",
        ],
    ),
    ("meubles", ["Je veux un devis pour un dressing sur mesure 2,5 m", "Ariana", "Mohamed, 98 000 222"]),
]


def run(base: str, rounds: int) -> None:
    latencies = []
    with httpx.Client(base_url=base, timeout=60) as client:
        for r in range(rounds):
            for tenant, messages in SCRIPTS:
                conv = None
                for text in messages:
                    t = time.perf_counter()
                    resp = client.post(
                        "/api/v1/chat", json={"tenant": tenant, "message": text, "conversation_id": conv}
                    )
                    resp.raise_for_status()
                    latencies.append(time.perf_counter() - t)
                    data = resp.json()
                    conv = data["conversation_id"]
                    print(f"[{tenant}] {text[:40]:40} → {(data['reply'] or '(équipe)')[:70]!r}")
            print(f"— round {r + 1}/{rounds} done")
        stats = client.get("/api/v1/stats").json()
    print()
    print(f"Messages sent:            {len(latencies)}")
    print(
        f"Median answer time:       {statistics.median(latencies):.2f} s (p90 "
        f"{statistics.quantiles(latencies, n=10)[-1]:.2f} s)"
    )
    print(f"Model:                    {stats['model']}")
    print(f"Conversations measured:   {stats['conversations_measured']}")
    print(f"Avg cost / conversation:  ${float(stats['avg_cost_per_conversation_usd']):.5f}")
    print(
        f"1,000 conversations:      ${stats['cost_per_1000_conversations_usd']} "
        f"≈ {stats['cost_per_1000_conversations_tnd']} TND"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url", nargs="?", default="http://localhost:8000")
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()
    run(args.base_url, args.rounds)
