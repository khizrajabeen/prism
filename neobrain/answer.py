"""Answers, claim verification, and citation export.

This module is the answer to "how does it give accurate and precise information
with references, and how does it deal with it?"

Three things, in increasing order of usefulness:

**1. A structured answer, not a pile of passages.** ``compose`` returns a
scaffold: what supports the claim, what contradicts it, how strong the evidence
collectively is, how well the corpus even covers the topic, and what would
settle it. A model fills the prose in; the structure and the citations are
computed, so the parts that can be wrong in a dangerous way are not generated.

**2. Claim checking.** ``check`` takes a sentence — from your draft, from a
supervisor's email, from a model's output — and puts the passages of your own
corpus that bear on it in front of you, ranked, with the specific reasons each
one might agree or disagree. This is the operation researchers actually need
and almost no tool provides: not "find me papers about X" but "what does my
library actually say about this exact sentence I am about to publish?"

**3. Citation export.** From an answer or a check, produce the BibTeX for
exactly the sources used. The path from "the assistant told me" to "the
reference list in my paper" has no manual retyping step, which is where
citation errors are introduced.

The honest limit, and why the verdicts are hedged
--------------------------------------------------
This is lexical analysis, not entailment detection. "Removes all class I from
the surface" supports "abolishes MHC class I presentation" while sharing almost
no vocabulary with it; no amount of regex tuning closes that gap. An earlier
version of this module returned SUPPORTED / CONTRADICTED and was wrong in both
directions on real examples — calling a wild quantitative claim "mixed" because
a passage shared the word "neoantigen", and missing genuine support because the
source phrased it differently.

So the verdict vocabulary is deliberately hedged — ``likely-supported``,
``possibly-contradicted``, ``needs-review``, ``no-evidence``,
``unverifiable-number``, ``disputed`` — and every result foregrounds the
passages with the instruction to read them. The verdict tells you where to
look; it does not tell you whether the claim is true, and a tool that claimed
otherwise would be lying about its own capability.

Where it *is* reliable: telling you the corpus contains nothing on a topic,
telling you a quantitative claim has no quantitative source behind it, and
surfacing the three passages you should read before submitting the sentence.
Those three cover most of the real risk.

A trained stance model (SciFact-style, three-way SUPPORTS/REFUTES/NOINFO) is
the natural upgrade and would slot in behind ``_signals`` without changing
anything above it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import db, evidence, retrieve

# Cues that a passage denies rather than asserts. Shared with the contradiction
# scanner, kept here in the claim-checking framing.
_NEGATIVE = [
    (r"\bdid not (?:improve|increase|enhance|correlate|predict|differ|show|reduce|affect)\b", 3),
    (r"\bno (?:significant )?(?:difference|association|correlation|benefit|effect|improvement)\b", 3),
    (r"\bfail(?:ed|s|ure) to (?:replicate|reproduce|confirm|show|improve|predict)\b", 3),
    (r"\bwe (?:were unable|could not) (?:to )?(?:confirm|replicate|detect)\b", 3),
    (r"\bcontrary to\b|\bin contrast to (?:previous|prior|earlier)\b", 2),
    (r"\bdoes not (?:support|hold|generalize|outperform)\b", 3),
    (r"\bnot (?:sufficient|necessary|required|predictive)\b", 2),
    (r"\bunlikely\b|\bimplausible\b", 1),
    (r"\boverestimat|overstat", 2),
    (r"\bhowever\b|\bnevertheless\b", 0.5),
]
_POSITIVE = [
    (r"\b(?:significantly )?(?:improved|increased|enhanced|correlated|predicted)\b", 2),
    (r"\bwe (?:show|demonstrate|find|report|observe)\b", 2),
    (r"\bconsistent with\b|\bin agreement with\b|\bconfirms?\b", 2),
    (r"\bassociated with\b|\bcorrelates? with\b", 1.5),
    (r"\bis (?:required|necessary|sufficient) for\b", 2),
]
_NEG = [(re.compile(p, re.I), w) for p, w in _NEGATIVE]
_POS = [(re.compile(p, re.I), w) for p, w in _POSITIVE]

_HEDGE = re.compile(
    r"\b(may|might|could|suggests?|appears?|possibly|potentially|likely|"
    r"seems?|indicat\w+)\b", re.I)


_STOP = {
    "the", "and", "that", "this", "with", "from", "have", "has", "been", "were",
    "was", "are", "for", "not", "but", "can", "may", "all", "any", "than", "then",
    "them", "they", "their", "there", "which", "when", "what", "into", "also",
    "more", "most", "such", "some", "these", "those", "will", "would", "could",
    "should", "shows", "show", "using", "used", "between", "among", "after",
}


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9-]{3,}", (text or "").lower())
            if w not in _STOP}


def _relevant_sentences(claim_words: set[str], passage: str) -> list[str]:
    """The sentences of a passage that actually mention the claim's terms.

    Scoping cue detection to these matters: a chunk about mouse models contains
    a bulleted list of things people get wrong, full of negations. Scanning the
    whole chunk for "did not" attributes that negation to whatever claim you
    happened to ask about.
    """
    out = []
    for s in re.split(r"(?<=[.!?;])\s+|\n", passage or ""):
        if claim_words & _content_words(s):
            out.append(s)
    return out


def _signals(claim: str, passage: str) -> dict[str, Any]:
    """Measurable signals about how a passage relates to a claim.

    Deliberately **not** a stance verdict. Lexical matching cannot resolve
    entailment — "removes all class I from the surface" supports "abolishes MHC
    class I presentation" and shares almost no words with it. Pretending
    otherwise produces a tool that is confidently wrong in both directions,
    which is worse than one that hands you the passages and says "read these".

    So this reports what is actually measurable, and the caller foregrounds the
    passages rather than a verdict.
    """
    from . import graph

    shared_entities = ({n for _, n in graph.extract(claim)}
                       & {n for _, n in graph.extract(passage)})
    claim_words = _content_words(claim)
    coverage = (len(claim_words & _content_words(passage)) / len(claim_words)
                if claim_words else 0.0)

    scoped = " ".join(_relevant_sentences(claim_words, passage)) or ""
    neg = sum(w for pattern, w in _NEG if pattern.search(scoped))
    pos = sum(w for pattern, w in _POS if pattern.search(scoped))

    claim_nums = [float(x) for x in re.findall(r"\b\d+(?:\.\d+)?\b", claim)]
    pass_nums = [float(x) for x in re.findall(r"\b\d+(?:\.\d+)?\b", scoped)]
    numeric = None
    if claim_nums:
        if not pass_nums:
            numeric = "claim is quantitative; this passage contains no comparable number"
        else:
            best = min(max(a, b) / max(min(a, b), 1e-6) for a in claim_nums for b in pass_nums)
            numeric = ("a number in the passage is close to the claim's"
                       if best <= 1.5 else
                       f"nearest number differs by ~{best:.0f}×")

    notes = []
    if shared_entities:
        notes.append("shares: " + ", ".join(sorted(shared_entities)[:4]))
    if neg > pos and neg:
        notes.append("contains negation language in the relevant sentences")
    elif pos > neg and pos:
        notes.append("contains assertion language in the relevant sentences")
    if numeric:
        notes.append(numeric)

    return {
        "coverage": round(coverage, 2),
        "shared_entities": sorted(shared_entities),
        "negation": neg,
        "assertion": pos,
        "numeric": numeric,
        "relevance": round(coverage + 0.1 * len(shared_entities), 3),
        "notes": notes,
    }


# ------------------------------------------------------------------- check

def check(
    claim: str,
    *,
    con: sqlite3.Connection | None = None,
    k: int = 12,
    record: bool = True,
) -> dict[str, Any]:
    """Check one claim against the corpus.

    Use it on a sentence from your draft before it goes to a supervisor, on a
    number you half-remember, or on anything a model told you.
    """
    close_after = con is None
    con = con or db.connect()

    hits = retrieve.search(claim, k=k, con=con)

    bearing, peripheral = [], []
    for h in hits:
        sig = _signals(claim, h.text)
        item = {
            "title": h.title or h.doc_ref, "paper_id": h.paper_id, "url": h.url,
            "tier": h.evidence_tier, "section": h.heading,
            "passage": h.snippet(claim, 500), "signals": sig,
        }
        (bearing if sig["coverage"] >= 0.35 or len(sig["shared_entities"]) >= 2
         else peripheral).append(item)

    bearing.sort(key=lambda i: -i["signals"]["relevance"])
    tiers = [h.evidence_tier for h in hits if h.evidence_tier is not None]
    sources = {h.paper_id for h in hits if h.paper_id}
    top_tier = max((b["tier"] or 0 for b in bearing), default=0)

    negating = [b for b in bearing if b["signals"]["negation"] > b["signals"]["assertion"]]
    asserting = [b for b in bearing if b["signals"]["assertion"] > b["signals"]["negation"]]
    quantitative = bool(re.search(r"\b\d+(?:\.\d+)?\b", claim))
    uncorroborated = quantitative and not any(
        b["signals"]["numeric"] and "close to" in b["signals"]["numeric"] for b in bearing)

    # The verdict vocabulary is hedged on purpose. This is lexical analysis over
    # your own corpus, not entailment, and it must never read as a truth claim.
    if not bearing:
        verdict = "no-evidence"
        note = ("Nothing in your corpus bears on this claim. That is a fact about "
                "your corpus, not about the literature — it is not disagreement. "
                "Widen config/interests.yaml or sweep further back before relying "
                "on the claim either way.")
    elif quantitative and uncorroborated:
        verdict = "unverifiable-number"
        note = (f"{len(bearing)} passage(s) are on topic, but none contains a number "
                f"matching the one you stated. A quantitative claim needs a "
                f"quantitative source — find the primary figure before citing it.")
    elif negating and asserting:
        verdict = "disputed"
        note = ("Your corpus contains passages pulling both ways. State the "
                "disagreement in your text rather than picking the convenient side.")
    elif negating:
        verdict = "possibly-contradicted"
        note = (f"{len(negating)} on-topic passage(s) use negating language about "
                f"this. Read them — if they really do contradict the claim, it "
                f"needs rewriting.")
    elif asserting:
        verdict = "likely-supported"
        note = (f"{len(asserting)} on-topic passage(s) assert something consistent "
                f"with this, best evidence tier {top_tier}/5. Verify by reading "
                f"them, and cite the primary source rather than this tool.")
    else:
        verdict = "needs-review"
        note = ("On-topic passages exist but none clearly asserts or denies the "
                "claim. Usually this means the claim is a step beyond what the "
                "source actually said — the most common way a citation goes wrong.")

    if _HEDGE.search(claim) and verdict == "likely-supported":
        note += (" Your claim is hedged ('may', 'suggests'), which is easier to "
                 "support than the unhedged version — check you are not citing "
                 "hedged evidence for an unhedged statement.")

    result = {
        "claim": claim,
        "verdict": verdict,
        "note": note,
        "method": ("Lexical analysis over your local corpus — term coverage, shared "
                   "entities, negation/assertion cues scoped to the relevant "
                   "sentences, and numeric corroboration. It is NOT entailment "
                   "detection and cannot decide whether the claim is true. Read the "
                   "passages; the verdict only tells you where to look."),
        "n_bearing": len(bearing),
        "n_asserting": len(asserting),
        "n_negating": len(negating),
        "distinct_sources": len(sources),
        "top_tier": top_tier,
        "evidence_strength": evidence.consensus_language(tiers, len(sources)),
        "passages": bearing,
        "peripheral": peripheral[:3],
    }

    if record:
        con.execute(
            """INSERT INTO claim_checks(checked_at, claim, verdict, confidence,
                                        n_support, n_contra, top_tier, sources, note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (db.now(), claim, verdict, result["evidence_strength"], len(asserting),
             len(negating), top_tier, json.dumps(sorted(sources)), note),
        )
        con.commit()

    if close_after:
        con.close()
    return result


def check_draft(text: str, *, con: sqlite3.Connection | None = None,
                k: int = 8) -> list[dict[str, Any]]:
    """Split a paragraph into sentences and check each one.

    Skips sentences that make no checkable factual claim — questions, headings,
    and pure transitions — because flagging "In this section we describe…" as
    unsupported is noise that trains you to ignore the output.
    """
    close_after = con is None
    con = con or db.connect()

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]
    out = []
    for s in sentences:
        words = s.split()
        if len(words) < 6 or s.endswith("?") or not re.search(r"[a-z]{4}", s):
            continue
        if re.match(r"^(here|in this|we (?:describe|present|report on)|figure|table)\b", s, re.I):
            continue
        out.append(check(s, con=con, k=k, record=False))
    if close_after:
        con.close()
    return out


# ----------------------------------------------------------------- compose

def compose(
    question: str,
    *,
    con: sqlite3.Connection | None = None,
    k: int = 12,
) -> dict[str, Any]:
    """Build a structured, citable answer scaffold for a question.

    The prose is the model's job. The structure, the citations, the coverage
    statistics and the confidence verdict are computed here, so the parts a
    language model gets dangerously wrong are not generated by one.
    """
    close_after = con is None
    con = con or db.connect()

    hits = retrieve.multi_search(question, k=k, con=con)
    tiers = [h.evidence_tier for h in hits if h.evidence_tier is not None]
    sources = {h.paper_id for h in hits if h.paper_id}

    # How well does the corpus even cover this? "No evidence" and "I did not
    # look" must not be indistinguishable.
    kws = retrieve.keywords(question, limit=3)
    coverage = {"query_terms": kws}
    if kws:
        like = f"%{kws[0]}%"
        row = con.execute(
            "SELECT COUNT(*) n, MIN(pub_date) oldest, MAX(pub_date) newest FROM papers "
            "WHERE title LIKE ? OR abstract LIKE ?", (like, like),
        ).fetchone()
        coverage.update({"papers_mentioning": row["n"], "oldest": row["oldest"],
                         "newest": row["newest"]})

    beliefs = db.rows_to_dicts(con.execute(
        "SELECT id, claim, confidence FROM beliefs WHERE status='active' "
        "AND (claim LIKE ? OR topic LIKE ?) LIMIT 5",
        (f"%{kws[0] if kws else question}%", f"%{kws[0] if kws else question}%"),
    ).fetchall())

    passages = []
    for h in hits:
        passages.append({
            "title": h.title or h.doc_ref, "paper_id": h.paper_id, "url": h.url,
            "section": h.heading, "tier": h.evidence_tier, "study_type": h.study_type,
            "retrieved_via": h.how, "signals": _signals(question, h.text),
            "passage": h.snippet(question, 700),
            "citation": h.citation(),
        })

    result = {
        "question": question,
        "evidence_strength": evidence.consensus_language(tiers, len(sources)),
        "n_passages": len(passages),
        "n_sources": len(sources),
        "tier_spread": {str(t): tiers.count(t) for t in sorted(set(tiers))} if tiers else {},
        "coverage": coverage,
        "existing_beliefs": beliefs,
        "passages": passages,
        "bibtex": bibtex_for(con, sorted(sources)),
        "instructions": [
            "Answer only from the passages above; cite each claim by paper id.",
            "Carry the evidence tier into the phrasing: tier 4-5 may be stated as "
            "established; tier 1-2 must be marked provisional.",
            "If passages disagree, say so and name both positions.",
            "If the passages do not settle it, say that explicitly and name what "
            "evidence would.",
            "Do not add facts from background knowledge without labelling them as "
            "unsourced.",
        ],
    }
    if close_after:
        con.close()
    return result


# ------------------------------------------------------------------ export

def bibtex_for(con: sqlite3.Connection, paper_ids: list[str]) -> str:
    """BibTeX for exactly the papers cited, ready to paste into a reference manager."""
    if not paper_ids:
        return ""
    ph = ",".join("?" * len(paper_ids))
    rows = con.execute(
        f"SELECT id, title, authors, journal, pub_date, doi, url FROM papers "
        f"WHERE id IN ({ph})", paper_ids,
    ).fetchall()
    out = []
    for r in rows:
        key = re.sub(r"[^A-Za-z0-9]", "", (r["id"] or "ref"))
        year = (r["pub_date"] or "")[:4]
        out.append(
            f"@article{{{key},\n"
            f"  title   = {{{r['title'] or ''}}},\n"
            f"  author  = {{{r['authors'] or ''}}},\n"
            f"  journal = {{{r['journal'] or ''}}},\n"
            f"  year    = {{{year}}},\n"
            + (f"  doi     = {{{r['doi']}}},\n" if r["doi"] else "")
            + f"  url     = {{{r['url'] or ''}}}\n}}"
        )
    return "\n\n".join(out)


def format_check(result: dict[str, Any], *, width: int = 92) -> str:
    """Render a claim check for a terminal."""
    import textwrap

    marks = {"likely-supported": "~", "possibly-contradicted": "!",
             "disputed": "±", "no-evidence": "·", "needs-review": "?",
             "unverifiable-number": "#"}
    lines = [
        f"{marks.get(result['verdict'], '?')} {result['verdict'].upper()}  "
        f"— {result['n_bearing']} on-topic passage(s) from "
        f"{result['distinct_sources']} source(s)",
        "",
        textwrap.fill(result["claim"], width=width, initial_indent='  "',
                      subsequent_indent="   ") + '"',
        "",
        textwrap.fill(result["note"], width=width, initial_indent="  ", subsequent_indent="  "),
        "",
    ]
    if result["passages"]:
        lines.append("  PASSAGES THAT BEAR ON THIS — read them, the verdict only "
                     "tells you where to look")
        for i in result["passages"][:5]:
            tier = f" [tier {i['tier']}/5]" if i["tier"] is not None else ""
            lines.append(f"    • {i['title']}{tier}")
            if i["url"]:
                lines.append(f"      {i['url']}")
            if i["signals"]["notes"]:
                lines.append(f"      ({'; '.join(i['signals']['notes'])})")
            lines.append(textwrap.fill(i["passage"][:340], width=width - 6,
                                       initial_indent="      ", subsequent_indent="      "))
            lines.append("")
    lines.append(textwrap.fill(result["method"], width=width,
                               initial_indent="  ", subsequent_indent="  "))
    return "\n".join(lines)
