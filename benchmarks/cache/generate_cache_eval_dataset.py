"""Generates the semantic-cache evaluation dataset: clusters of
(canonical prompt, true near-duplicate paraphrases, confusable-but-
different prompts) used by tau_sweep.py to measure hit rate vs false-hit
rate as the similarity threshold moves.

Confusable prompts share surface structure with the canonical prompt but
ask something different ("weather in X" vs "population of X") -- these
are the ones that risk a false hit at a loose threshold.

    python -m benchmarks.cache.generate_cache_eval_dataset
"""

from __future__ import annotations

import json
from pathlib import Path

OUT_PATH = Path(__file__).parent / "cache_eval_dataset.jsonl"

CITIES = [
    "Tokyo",
    "Nairobi",
    "Lisbon",
    "Toronto",
    "Jakarta",
    "Cairo",
    "Oslo",
    "Manila",
    "Warsaw",
    "Lima",
]
COUNTRIES = [
    "France",
    "Brazil",
    "Kenya",
    "Norway",
    "Vietnam",
    "Chile",
    "Greece",
    "Peru",
    "Egypt",
    "Canada",
]
TERMS = [
    "recursion",
    "inflation",
    "osmosis",
    "entropy",
    "latency",
    "quorum",
    "amortization",
    "diaspora",
    "compiler",
    "throughput",
]
LANGUAGES = [
    "Spanish",
    "Japanese",
    "Swahili",
    "French",
    "Hindi",
    "German",
    "Korean",
    "Italian",
    "Arabic",
    "Portuguese",
]
CODE_TASKS = [
    ("reverses a string", "reverses a linked list"),
    ("checks if a number is prime", "checks if a number is a perfect square"),
    ("merges two sorted lists", "merges two dictionaries"),
    ("finds the max of a list", "finds the min of a list"),
    ("counts vowels in a string", "counts words in a string"),
    ("flattens a nested list", "deduplicates a list"),
    ("computes a factorial", "computes a Fibonacci number"),
    ("sorts a list of tuples by the second element", "sorts a list of tuples by the first element"),
    ("converts Celsius to Fahrenheit", "converts Fahrenheit to Celsius"),
    ("validates an email address", "validates a phone number"),
]


def build() -> list[dict]:
    clusters = []

    # "confusable" = same entity, different intent (weather vs population).
    # "entity_confusable" = same template/intent, DIFFERENT entity (capital
    # of France vs capital of Japan) -- a harder, more insidious false-hit
    # case: embedding similarity is dominated by template structure, so a
    # naive semantic cache can collide two prompts with different correct
    # answers. See results/cache/tau_sweep.json and README Limitations.

    for i, city in enumerate(CITIES):
        other_city = CITIES[(i + 1) % len(CITIES)]
        clusters.append(
            {
                "topic": "weather",
                "canonical": f"What is the weather in {city} today?",
                "near_duplicates": [
                    f"Tell me the weather in {city} today.",
                    f"What's the weather like in {city} today?",
                ],
                "confusable": [f"What is the population of {city} today?"],
                "entity_confusable": [f"What is the weather in {other_city} today?"],
            }
        )

    for i, country in enumerate(COUNTRIES):
        other_country = COUNTRIES[(i + 1) % len(COUNTRIES)]
        clusters.append(
            {
                "topic": "capital",
                "canonical": f"What is the capital of {country}?",
                "near_duplicates": [
                    f"Tell me the capital of {country}.",
                    f"What's the capital city of {country}?",
                ],
                "confusable": [f"What is the currency of {country}?"],
                "entity_confusable": [f"What is the capital of {other_country}?"],
            }
        )

    for i, term in enumerate(TERMS):
        other_term = TERMS[(i + 1) % len(TERMS)]
        clusters.append(
            {
                "topic": "definition",
                "canonical": f"Define {term} in simple terms.",
                "near_duplicates": [
                    f"Explain {term} in simple terms.",
                    f"Give me a simple definition of {term}.",
                ],
                "confusable": [f"Give an example of {term} being used in practice."],
                "entity_confusable": [f"Define {other_term} in simple terms."],
            }
        )

    for i, lang in enumerate(LANGUAGES):
        other_lang = LANGUAGES[(i + 1) % len(LANGUAGES)]
        clusters.append(
            {
                "topic": "translate",
                "canonical": f"Translate the word 'hello' into {lang}.",
                "near_duplicates": [
                    f"How do you say 'hello' in {lang}?",
                    f"What is 'hello' in {lang}?",
                ],
                "confusable": [f"Translate the word 'goodbye' into {lang}."],
                "entity_confusable": [f"Translate the word 'hello' into {other_lang}."],
            }
        )

    for canonical_task, confusable_task in CODE_TASKS:
        clusters.append(
            {
                "topic": "code",
                "canonical": f"Write a Python function that {canonical_task}.",
                "near_duplicates": [
                    f"Write Python code that {canonical_task}.",
                    f"Give me a Python function that {canonical_task}.",
                ],
                "confusable": [f"Write a Python function that {confusable_task}."],
                "entity_confusable": [],
            }
        )

    for idx, cluster in enumerate(clusters):
        cluster["id"] = f"cache-{idx:03d}"
    return clusters


def main() -> None:
    clusters = build()
    with open(OUT_PATH, "w") as f:
        for c in clusters:
            f.write(json.dumps(c) + "\n")
    n_near = sum(len(c["near_duplicates"]) for c in clusters)
    n_conf = sum(len(c["confusable"]) for c in clusters)
    print(
        f"wrote {len(clusters)} clusters ({n_near} near-duplicates, {n_conf} confusable) to {OUT_PATH}"
    )


if __name__ == "__main__":
    main()
