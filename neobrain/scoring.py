"""Relevance scoring.

Deliberately transparent: additive term weights you can read off the config,
plus a few structural signals. You should always be able to answer "why did
this paper surface?" by looking at the `matched` column — which is why there is
no opaque learned model here. Tuning happens in ``config/interests.yaml``.
"""

from __future__ import annotations

import re

# Structural bonuses, applied on top of the configured term weights.
_STUDY_SIGNALS = {
    r"\brandomi[sz]ed\b": 3,
    r"\bphase\s*(1|2|3|i|ii|iii)\b": 3,
    r"\bfirst[- ]in[- ]human\b": 3,
    r"\bbenchmark(ing|ed)?\b": 2,
    r"\bprospective\b": 1,
    r"\bcohort\b": 1,
}

# Words that usually mean the hit is about something else entirely. These are
# domain false-friends: "neoantigen" is specific, but "vaccine" alone is not.
_OFF_TARGET = {
    r"\bsars-cov-2\b|\bcovid-19\b": -3,
    r"\binfluenza\b": -2,
    r"\bmalaria\b|\btuberculosis\b": -2,
    r"\bveterinary\b": -3,
}


def score_record(
    title: str | None,
    abstract: str | None,
    boosts: dict[str, int],
    penalties: dict[str, int],
    *,
    is_oa: bool = False,
    pub_type: str | None = None,
) -> tuple[int, list[str]]:
    """Return ``(score, matched_terms)`` for one record.

    Matching is substring-based on lowercased ``title + abstract`` so that
    stems like ``immunopeptidom`` catch every inflection. Title hits count
    double — a term in the title is what the paper is *about*, a term in the
    abstract may be a passing mention.
    """
    title = title or ""
    abstract = abstract or ""
    blob = f"{title} {abstract}".lower()
    title_l = title.lower()

    total = 0
    matched: list[str] = []

    for term, weight in (boosts or {}).items():
        t = str(term).lower()
        if t in blob:
            weight = int(weight)
            total += weight
            if t in title_l:
                total += weight  # title hits count double
            matched.append(str(term))

    for term, weight in (penalties or {}).items():
        if str(term).lower() in blob:
            total += int(weight)

    for pattern, weight in _STUDY_SIGNALS.items():
        if re.search(pattern, blob):
            total += weight

    for pattern, weight in _OFF_TARGET.items():
        if re.search(pattern, blob) and "neoantigen" not in blob:
            total += weight

    # A record with a real abstract is worth more than a metadata stub.
    if len(abstract) > 300:
        total += 1
    elif len(abstract) < 40:
        total -= 3

    if is_oa:
        total += 1  # we can actually read it, and ingest its methods

    if pub_type:
        pt = pub_type.lower()
        if "editorial" in pt or "comment" in pt or "news" in pt:
            total -= 5
        if "retracted" in pt:
            total -= 20

    return total, matched


def explain(score: int, matched: list[str], threshold: int) -> str:
    verdict = "surfaced" if score >= threshold else "logged only"
    terms = ", ".join(matched[:8]) if matched else "no configured terms"
    return f"score {score} ({verdict}, threshold {threshold}) — matched: {terms}"
