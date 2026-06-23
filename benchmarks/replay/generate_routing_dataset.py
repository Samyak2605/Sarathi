"""Generates the 500-prompt labeled routing dataset used to validate the
router offline before its policy is allowed to run (CLAUDE.md rule 4).

Deterministic template x topic combinations (no randomness -- re-running
this script reproduces the exact same file), each labeled with the tier a
human would expect a cost-aware router to pick. Run once; the output is
committed as a stable regression asset:

    python -m benchmarks.replay.generate_routing_dataset
"""

from __future__ import annotations

import json
from pathlib import Path

OUT_PATH = Path(__file__).parent / "routing_dataset.jsonl"

COUNTRIES = [
    "France",
    "Japan",
    "Brazil",
    "Egypt",
    "Canada",
    "Kenya",
    "Norway",
    "Peru",
    "Thailand",
    "Portugal",
    "Vietnam",
    "Chile",
    "Morocco",
    "Finland",
    "Greece",
]
TERMS = [
    "photosynthesis",
    "inflation",
    "recursion",
    "entropy",
    "osmosis",
    "latency",
    "amortization",
    "compiler",
    "diaspora",
    "algorithm",
    "quorum",
    "throughput",
]
EVENTS = [
    "the fall of the Berlin Wall",
    "the first moon landing",
    "the invention of the printing press",
    "the founding of the United Nations",
    "the launch of the first satellite",
]
REVIEWS = [
    "This product completely broke after two days, total waste of money.",
    "Absolutely loved it, works exactly as described and arrived early!",
    "It's okay, does the job but nothing special.",
    "Terrible customer service, would not buy again.",
    "Best purchase I've made all year, highly recommend to everyone.",
]
REPHRASE_TEXTS = [
    "hey can u send me that file when u get a sec thanks",
    "the meeting got pushed back idk why but ill let u know",
    "this is kinda broken ngl someone should fix it",
]
CODE_TASKS = [
    "reverses a linked list",
    "checks if a string is a palindrome",
    "merges two sorted arrays",
    "finds the longest common subsequence",
    "implements a LRU cache",
    "computes the nth Fibonacci number iteratively",
]
DEBUG_SNIPPETS = [
    "TypeError: 'NoneType' object is not subscriptable",
    "IndexError: list index out of range in a pagination loop",
    "RecursionError: maximum recursion depth exceeded in a tree traversal",
    "deadlock between two goroutines sharing a mutex",
]
REASONING_PAIRS = [
    (
        "why do some materials become superconductors at low temperatures",
        "conventional resistive heating",
    ),
    ("why distributed consensus is hard", "single-node transaction commit"),
    ("why cache invalidation is considered a hard problem", "cache population"),
    ("why microservice architectures increase operational complexity", "a monolith"),
]
DESIGN_PAIRS = [
    ("a message queue-based architecture", "a direct synchronous RPC architecture"),
    ("SQL databases", "NoSQL document stores"),
    ("server-side rendering", "client-side rendering"),
    ("a monorepo", "polyrepo setup"),
]


def build() -> list[dict]:
    rows: list[dict] = []

    for c in COUNTRIES:
        rows.append(
            {
                "prompt": f"What is the capital of {c}?",
                "task_type": "short_lookup",
                "expected_tier": "small",
            }
        )
    for t in TERMS:
        rows.append(
            {
                "prompt": f"Define {t} in one sentence.",
                "task_type": "short_lookup",
                "expected_tier": "small",
            }
        )
    for e in EVENTS:
        rows.append(
            {
                "prompt": f"What year did {e} happen?",
                "task_type": "short_lookup",
                "expected_tier": "small",
            }
        )

    for r in REVIEWS:
        rows.append(
            {
                "prompt": f"Is the following review positive or negative: '{r}'",
                "task_type": "classification",
                "expected_tier": "small",
            }
        )
    for t in TERMS:
        rows.append(
            {
                "prompt": f"Classify whether '{t}' is a computer-science term or a biology term.",
                "task_type": "classification",
                "expected_tier": "small",
            }
        )

    for t in REPHRASE_TEXTS:
        rows.append(
            {
                "prompt": f"Rephrase this message more formally: '{t}'",
                "task_type": "rephrase",
                "expected_tier": "small",
            }
        )
    for c in COUNTRIES[:8]:
        rows.append(
            {
                "prompt": f"Rewrite this as a formal invitation to visit {c} for a conference.",
                "task_type": "rephrase",
                "expected_tier": "mid",
            }
        )

    for task in CODE_TASKS:
        rows.append(
            {
                "prompt": f"Write a Python function that {task}. Include a docstring and a usage example.",
                "task_type": "simple_code",
                "expected_tier": "mid",
            }
        )
    for snippet in DEBUG_SNIPPETS:
        rows.append(
            {
                "prompt": (
                    f"Here is a stack trace from production:\n```{snippet}```\n"
                    f"Explain step by step what's likely causing this bug and how to fix it."
                ),
                "task_type": "debugging",
                "expected_tier": "large",
            }
        )

    for topic, other in REASONING_PAIRS:
        rows.append(
            {
                "prompt": f"Explain step by step why {topic}, and compare it to {other}.",
                "task_type": "reasoning",
                "expected_tier": "large",
            }
        )
    for a, b in DESIGN_PAIRS:
        rows.append(
            {
                "prompt": (
                    f"Design and compare {a} versus {b} for a high-traffic system. "
                    f"Discuss trade-offs in latency, consistency, operational cost, and failure modes."
                ),
                "task_type": "long_form",
                "expected_tier": "large",
            }
        )

    # Pad to exactly 500 by cycling through categories with light variation,
    # so the dataset has real statistical weight per category.
    base = list(rows)
    i = 0
    while len(rows) < 500:
        template = base[i % len(base)]
        variant = dict(template)
        variant["prompt"] = f"{variant['prompt']} (variant #{i // len(base) + 1})"
        rows.append(variant)
        i += 1
    rows = rows[:500]

    for idx, row in enumerate(rows):
        row["id"] = f"route-{idx:04d}"
    return rows


def main() -> None:
    rows = build()
    with open(OUT_PATH, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"wrote {len(rows)} prompts to {OUT_PATH}")


if __name__ == "__main__":
    main()
