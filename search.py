"""
search.py — Fuzzy item search with substring pre-filtering and score boosting.

Fixes from original code:
  • 'name' variable bug (was printing last loop value instead of matched item name)
  • user_input prompt was inside the match loop (moved to main.py)
  • Added prefix/exact-match score boosts for better ranking
"""

from difflib import SequenceMatcher
from typing import List, Tuple


def search_items(
    query: str,
    data: list,
    min_score: float = 0.60,
    top_n: int = 3,
) -> List[Tuple[float, dict]]:
    """
    Return up to top_n items whose names best match query, scored ≥ min_score.
    Results are sorted highest-score first.
    """
    q = query.lower().strip()

    # ── 1. Substring pre-filter (fast) ───────────────────────────────────────
    candidates = [item for item in data if q in item["name"].lower()]
    pool = candidates if candidates else data

    # ── 2. Fuzzy scoring ─────────────────────────────────────────────────────
    scored: List[Tuple[float, dict]] = []
    for item in pool:
        name_lower = item["name"].lower()
        score = SequenceMatcher(None, name_lower, q).ratio()

        # Boost exact / prefix matches so they reliably top the list
        if name_lower == q:
            score = 1.0
        elif name_lower.startswith(q):
            score = min(score + 0.15, 1.0)

        scored.append((score, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [(s, it) for s, it in scored if s >= min_score][:top_n]
