"""Evidence grading and contradiction detection.

Two jobs that turn retrieval into reasoning.

**Grading.** A randomized trial and a single-cell-line preprint are both
"a source". Presenting them as equally weighted is how a literature review
becomes wrong. Every paper gets a tier from its own text, and retrieved
passages carry that tier, so the agent's instruction to label claims as
*established / contested / single-paper* has something to stand on.

**Contradiction detection.** The failure mode of a long-running research brain
is that it accumulates beliefs and never notices when the field moves. This
scans new evidence against stored beliefs and queues the tensions it finds.

On the design of the detector
-----------------------------
Scientific claim verification (SciFact and its successors) frames this as
three-way stance classification — SUPPORTS / REFUTES / NOINFO — and the good
models are fine-tuned transformers. Running one locally is possible and would
be a reasonable upgrade; what is implemented here is the deterministic
precursor: shared-entity overlap plus negation and reversal cues plus numeric
divergence.

That combination has poor precision and decent recall, and this is the right
trade for the job. A queue of candidate contradictions that you glance at and
dismiss in five seconds costs almost nothing. A missed contradiction costs you
a wrong claim in a paper. The detector is explicitly a *notice*, never a
verdict — `conflicts` rows are opened for review, never applied.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from . import db, graph

# ------------------------------------------------------------ evidence tiers

# Higher is stronger. The tier is a property of study design, not of how
# interesting the result is.
TIERS = {
    5: "randomized trial / prospective multi-team benchmark",
    4: "controlled trial, large cohort, or systematic prospective evaluation",
    3: "primary research with in vivo or human validation",
    2: "primary research, in vitro or computational only",
    1: "preprint, unreplicated",
    0: "review, editorial, commentary, or unclear",
}

_STUDY_PATTERNS: list[tuple[int, str, re.Pattern]] = [
    (5, "randomized trial", re.compile(
        r"\brandomi[sz]ed (?:controlled )?(?:trial|study|phase)\b|\bplacebo-controlled\b", re.I)),
    (5, "prospective benchmark", re.compile(
        r"\bTESLA\b|\bprospective(?:ly)? (?:validated|evaluated|benchmark)", re.I)),
    (4, "clinical trial", re.compile(
        r"\bphase (?:1|2|3|i|ii|iii)\b|\bfirst-in-human\b|\bclinical trial\b", re.I)),
    (4, "benchmark", re.compile(
        r"\bbenchmark(?:ing|ed)?\b.{0,60}\b(?:tools?|methods?|predictors?|pipelines?)\b", re.I)),
    (4, "cohort", re.compile(r"\b(?:prospective|retrospective) cohort\b|\bcohort of \d+", re.I)),
    (3, "in vivo", re.compile(
        r"\bin vivo\b|\bmice were\b|\bmouse model\b|\bxenograft\b|\bsyngeneic\b|\bpatients? (?:were|received)\b", re.I)),
    (2, "in vitro / computational", re.compile(
        r"\bin vitro\b|\bcell line\b|\bwe (?:trained|developed|implemented)\b|\bdataset\b", re.I)),
    (0, "review", re.compile(
        r"\b(?:systematic |narrative |scoping )?review\b|\beditorial\b|\bcommentary\b|\bperspective\b|\bwe discuss\b", re.I)),
]


def grade(title: str | None, abstract: str | None, *, source: str = "journal",
          pub_type: str | None = None) -> tuple[int, str]:
    """Return ``(tier, study_type)`` for one paper."""
    blob = f"{title or ''} {abstract or ''}"
    pt = (pub_type or "").lower()

    if "retracted" in pt:
        return 0, "retracted"
    if any(w in pt for w in ("editorial", "comment", "news", "letter")):
        return 0, "editorial"

    best_tier, best_label = 1 if source == "preprint" else 2, "unclassified"
    for tier, label, pattern in _STUDY_PATTERNS:
        if pattern.search(blob):
            if tier == 0:  # a review claim overrides upward guesses
                return 0, label
            if tier > best_tier:
                best_tier, best_label = tier, label

    # A preprint caps at 3: it may describe a trial, but it has not been reviewed.
    if source == "preprint":
        best_tier = min(best_tier, 3)
        best_label = f"{best_label} (preprint)"

    return best_tier, best_label


def grade_corpus(con: sqlite3.Connection, limit: int | None = None) -> int:
    """Grade every paper that has no tier yet."""
    sql = "SELECT id, title, abstract, source, pub_date FROM papers WHERE evidence_tier IS NULL"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = con.execute(sql).fetchall()
    for r in rows:
        tier, label = grade(r["title"], r["abstract"], source=r["source"] or "journal")
        con.execute(
            "UPDATE papers SET evidence_tier=?, study_type=? WHERE id=?",
            (tier, label, r["id"]),
        )
    con.commit()
    return len(rows)


def tier_label(tier: int | None) -> str:
    if tier is None:
        return "ungraded"
    return TIERS.get(int(tier), "unclear")


def consensus_language(tiers: list[int], n_sources: int) -> str:
    """How an answer resting on this evidence should be hedged."""
    if not tiers or n_sources == 0:
        return "no evidence in the corpus — say so rather than answering from memory"
    high = [t for t in tiers if t >= 4]
    if len(high) >= 2:
        return "established: multiple higher-tier sources agree"
    if n_sources >= 3 and max(tiers) >= 3:
        return "supported: several primary sources, no high-tier confirmation"
    if n_sources == 1 or max(tiers) <= 2:
        return "single-paper or low-tier: present as provisional, name what would confirm it"
    return "mixed: state the disagreement explicitly"


# ----------------------------------------------------- contradiction detection

# Cues that a passage is *denying* something rather than reporting it.
_NEGATION_CUES = [
    (r"\bdid not (?:improve|increase|enhance|correlate|predict|differ|show|reduce)\b", "explicit negative result"),
    (r"\bno (?:significant )?(?:difference|association|correlation|benefit|effect|improvement)\b", "null result"),
    (r"\bfail(?:ed|s|ure) to (?:replicate|reproduce|confirm|show|improve|predict)\b", "failure to replicate"),
    (r"\bcontrary to\b|\bin contrast to (?:previous|prior|earlier)\b", "explicit contrast with prior work"),
    (r"\bchallenges? (?:the |our )?(?:prevailing|current|widely|previous)\b", "challenges prior view"),
    (r"\bdoes not (?:support|hold|generalize|outperform)\b", "non-support"),
    (r"\bwe (?:were unable|could not) (?:to )?(?:confirm|replicate|detect)\b", "could not confirm"),
    (r"\boverestimat(?:e|ed|ing)\b|\boverstat(?:e|ed|ing)\b", "prior work overstated"),
    (r"\bno longer\b|\bsupersed(?:es|ed)\b|\bnow known to be\b", "supersession"),
]
_NEGATION = [(re.compile(p, re.I), why) for p, why in _NEGATION_CUES]

_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*(%|percent|fold|nM|µM|uM)?", re.I)


def _numbers(text: str) -> list[float]:
    out = []
    for value, _unit in _NUMBER.findall(text or ""):
        try:
            out.append(float(value))
        except ValueError:
            continue
    return out


def _numeric_divergence(claim: str, passage: str) -> float:
    """How far apart the numbers are, as a ratio. 0 when either side has none."""
    a, b = _numbers(claim), _numbers(passage)
    if not a or not b:
        return 0.0
    ca, cb = max(a), max(b)
    if ca <= 0 or cb <= 0:
        return 0.0
    return max(ca, cb) / min(ca, cb)


def _shared_entities(claim: str, passage: str) -> set[str]:
    return {n for _, n in graph.extract(claim)} & {n for _, n in graph.extract(passage)}


def scan_belief(
    con: sqlite3.Connection,
    belief: dict,
    *,
    k: int = 12,
    min_shared: int = 1,
) -> list[dict[str, Any]]:
    """Look for passages that appear to contradict one belief."""
    from . import retrieve  # imported here to avoid a cycle at module load

    claim = belief["claim"]
    hits = retrieve.search(claim, k=k, con=con)
    found: list[dict[str, Any]] = []

    for h in hits:
        shared = _shared_entities(claim, h.text)
        if len(shared) < min_shared:
            continue

        cues = [why for pattern, why in _NEGATION if pattern.search(h.text)]
        divergence = _numeric_divergence(claim, h.text)
        if divergence >= 3.0:
            cues.append(f"numbers differ by ~{divergence:.0f}×")
        if not cues:
            continue

        found.append({
            "belief_id": belief["id"],
            "paper_id": h.paper_id,
            "chunk_id": h.chunk_id,
            "cue": "; ".join(cues[:3]) + f" (shared: {', '.join(sorted(shared)[:4])})",
            "passage": h.text[:1200],
        })
    return found


def scan(con: sqlite3.Connection, *, limit_beliefs: int = 50, k: int = 12) -> int:
    """Scan all active beliefs against the corpus. Returns new conflicts opened."""
    from . import memory

    opened = 0
    for belief in memory.get_beliefs(con, limit=limit_beliefs):
        for c in scan_belief(con, belief, k=k):
            try:
                con.execute(
                    """INSERT INTO conflicts(detected_at, belief_id, paper_id, chunk_id,
                                             cue, passage, status)
                       VALUES (?,?,?,?,?,?, 'open')""",
                    (db.now(), c["belief_id"], c["paper_id"], c["chunk_id"],
                     c["cue"], c["passage"]),
                )
                opened += 1
            except sqlite3.IntegrityError:
                pass  # already flagged this belief/passage pair
    con.commit()
    return opened


def open_conflicts(con: sqlite3.Connection, limit: int = 25) -> list[dict]:
    rows = con.execute(
        """SELECT c.*, b.claim, b.confidence, p.title, p.url, p.evidence_tier
           FROM conflicts c
           JOIN beliefs b ON b.id = c.belief_id
           LEFT JOIN papers p ON p.id = c.paper_id
           WHERE c.status='open'
           ORDER BY COALESCE(p.evidence_tier, 0) DESC, c.id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return db.rows_to_dicts(rows)


def resolve(con: sqlite3.Connection, conflict_id: int, status: str, note: str = "") -> None:
    if status not in ("resolved", "dismissed", "open"):
        raise ValueError("status must be resolved, dismissed, or open")
    con.execute(
        "UPDATE conflicts SET status=?, note=? WHERE id=?", (status, note, conflict_id)
    )
    con.commit()
