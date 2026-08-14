"""Structured extraction and the methods audit.

Two capabilities that together are the sharpest thing in this system.

**1. Extraction with sentence-level provenance.** Pull `n per group`, model
system, randomization, blinding, endpoint, statistical test, predictor version,
HLA alleles and the rest out of full text into a comparison matrix — and make
every single cell click through to *the exact sentence it came from*.

Elicit does extraction with an LLM at ~94% accuracy after prompt iteration.
That is genuinely useful and it has two costs a researcher pays quietly: the
6% is invisible without checking, and a blank cell is ambiguous between "the
paper did not report this" and "the model missed it".

This module takes the opposite trade. Extraction is pattern-based, so it is
deterministic, auditable and cannot invent a value that is not in the text. It
recalls less than an LLM — it will miss unusually-phrased reporting — but every
value it produces carries the sentence, and **"not reported" is a first-class
result rather than an empty cell**. That distinction matters more than the
recall difference, because a paper that does not report its group sizes is not
a gap in your table; it is a finding about the paper.

**2. The methods audit.** Which of the reporting essentials does this paper
actually contain? Group sizes, randomization, blinding, a power calculation,
the right controls, a named statistical test, ethics approval. ARRIVE and
CONSORT have said for years that these belong in every paper; nobody checks at
corpus scale because nobody had the full text in a database.

This is what lets NeoBrain do something Scite explicitly does not. Scite's own
documentation notes that a supporting citation "might come from a paper where
the experimental evidence is weak" — it counts citations without weighting them
by the quality of the citing study. With an audit per paper, support can be
weighted by whether the supporting study was actually built to detect anything.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from . import db

# --------------------------------------------------------------------- model


@dataclass
class Extraction:
    field: str
    value: str
    evidence: str            # the sentence it came from — the whole point
    section: str = ""
    confidence: str = "medium"   # high | medium | low
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field, "value": self.value, "unit": self.unit,
            "evidence": self.evidence, "section": self.section,
            "confidence": self.confidence,
        }


NOT_REPORTED = "not reported"


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def _first(patterns: list[tuple[str, str]], sentences: list[str], section: str,
           *, confidence: str = "high",
           transform: Callable[[re.Match], str] | None = None) -> Extraction | None:
    """Return the first sentence matching any pattern, with its captured value."""
    for label, pattern in patterns:
        rx = re.compile(pattern, re.I)
        for s in sentences:
            m = rx.search(s)
            if m:
                value = transform(m) if transform else (
                    m.group(1) if m.groups() else label)
                return Extraction(field="", value=str(value).strip(), evidence=s[:400],
                                  section=section, confidence=confidence)
    return None


# ------------------------------------------------------------- field rules
# Each rule: (field, patterns, confidence, unit). Patterns capture group 1 as
# the value where a value exists; otherwise the label is used.

_SAMPLE_SIZE = [
    ("n", r"\bn\s*=\s*(\d{1,4})\s*(?:mice|animals|patients|per group|/group)?"),
    ("n", r"\b(\d{1,4})\s+(?:mice|animals|patients|subjects)\s+(?:per group|in each group)"),
    ("n", r"\b(?:groups?|arms?)\s+of\s+(\d{1,4})\b"),
    ("n", r"\b(\d{1,4})\s+(?:mice|animals|patients)\s+were\s+(?:randomi|assigned|enrolled)"),
]

_RANDOMIZATION = [
    ("randomized", r"\brandomi[sz]ed?\b|\brandomly (?:assigned|allocated|divided)\b"),
]
_BLINDING = [
    ("blinded", r"\bblind(?:ed|ing)\b|\bmasked\b(?!\s+by)"),
]
_POWER = [
    ("power calculation", r"\bpower(?:ed)?\s+(?:calculation|analysis|to detect)\b|"
                          r"\b\d{2}%\s+power\b|\bsample size (?:was )?(?:calculat|determin)"),
]
_ETHICS = [
    ("approved", r"\b(?:IACUC|institutional (?:animal care|review board)|ethics committee|"
                 r"IRB)\b.{0,60}\b(?:approv|permit)"),
    ("approved", r"\bapproved by the\b.{0,60}\b(?:committee|board)\b"),
]
_STATS = [
    ("log-rank", r"\blog[- ]rank\b|\bMantel[- ]Cox\b"),
    ("ANOVA", r"\bANOVA\b|\banalysis of variance\b"),
    ("Mann-Whitney", r"\bMann[- ]Whitney\b|\bWilcoxon rank[- ]sum\b"),
    ("t-test", r"\b(?:Student'?s )?t[- ]test\b"),
    ("mixed model", r"\bmixed[- ](?:effects? )?model\b|\brepeated[- ]measures\b"),
    ("Fisher/chi-square", r"\bFisher'?s exact\b|\bchi[- ]squared?\b|\bχ2\b"),
    ("Cox", r"\bCox (?:proportional|regression)\b"),
]
_MULTIPLICITY = [
    ("corrected", r"\b(?:Benjamini|Bonferroni|Holm|false discovery rate|FDR|"
                  r"multiple (?:comparison|testing) correction)\b"),
]
_CONTROLS = [
    ("wild-type peptide control", r"\bwild[- ]?type peptide\b|\bWT peptide\b"),
    ("adjuvant-alone arm", r"\badjuvant[- ]alone\b|\badjuvant only\b|\bvehicle\s*\+\s*adjuvant\b"),
    ("irrelevant peptide", r"\birrelevant peptide\b|\bcontrol peptide\b"),
    ("isotype control", r"\bisotype[- ]?(?:matched )?control\b"),
    ("vehicle control", r"\bvehicle(?:[- ]treated)? control\b|\bPBS control\b"),
    ("untreated control", r"\buntreated control\b|\bnaive control\b"),
]
_SEX = [
    # A strain name usually sits between the sex and the animal noun
    # ("female C57BL/6 mice"), so allow a couple of intervening tokens.
    ("both", r"\bmale and female\b|\bboth sexes\b|\bsex[- ]balanced\b"),
    ("female", r"\bfemale\b(?:\s+\S+){0,3}\s*(?:mice|animals|patients|rats|subjects)\b"),
    ("male", r"\bmale\b(?:\s+\S+){0,3}\s*(?:mice|animals|patients|rats|subjects)\b"),
]
_ROUTE = [
    ("subcutaneous", r"\bsubcutaneous(?:ly)?\b|\bs\.?c\.?\b"),
    ("intramuscular", r"\bintramuscular(?:ly)?\b|\bi\.?m\.?\b"),
    ("intradermal", r"\bintradermal(?:ly)?\b|\bi\.?d\.?\b"),
    ("intravenous", r"\bintravenous(?:ly)?\b|\bi\.?v\.?\b"),
    ("orthotopic", r"\borthotopic(?:ally)?\b"),
]
_ENDPOINT = [
    ("overall survival", r"\boverall survival\b|\bOS\b(?=\s|,|\.)"),
    ("progression-free survival", r"\bprogression[- ]free survival\b|\bPFS\b"),
    ("recurrence-free survival", r"\brecurrence[- ]free survival\b|\bRFS\b|\bDFS\b"),
    ("tumour volume", r"\btum(?:o|ou)r (?:volume|growth|burden)\b"),
    ("objective response rate", r"\bobjective response rate\b|\bORR\b"),
    ("immunogenicity (ELISpot)", r"\bELISpot\b"),
    ("immunogenicity (tetramer)", r"\btetramer\b|\bmultimer\b"),
]
_TIMEPOINT = [
    ("day", r"\b(?:on |at )?day\s+(\d{1,3})\b"),
    ("week", r"\b(?:at |after )?(\d{1,2})\s+weeks?\b"),
]
_PREDICTOR = [
    ("predictor", r"\b(NetMHC(?:II)?pan[- ]?[\d.]+)\b"),
    ("predictor", r"\b(MHCflurry[- ]?[\d.]*)\b"),
    ("predictor", r"\b(pVAC(?:seq|tools|bind|fuse)[- ]?[\d.]*)\b"),
    ("predictor", r"\b(MixMHC(?:2)?pred[- ]?[\d.]*)\b"),
    ("predictor", r"\b(BigMHC|PRIME|TransPHLA)\b"),
]
_THRESHOLD = [
    ("binding threshold", r"\b(?:IC50|affinity)\s*[<≤]\s*(\d{2,4})\s*nM"),
    ("percentile rank", r"\b(?:percentile )?rank\s*[<≤]\s*([\d.]+)\s*%?"),
]
_PEPTIDE_LEN = [
    ("peptide length", r"\b(\d{1,2})\s*(?:-|–|\s)?mer(?:s)?\b"),
    ("peptide length", r"\b(\d{1,2})\s*(?:to|-|–)\s*\d{1,2}\s*(?:-)?mer"),
]
_ADJUVANT = [
    ("poly-ICLC", r"\bpoly[- ]?ICLC\b|\bHiltonol\b"),
    ("montanide", r"\bmontanide\b|\bISA[- ]?51\b"),
    ("CpG", r"\bCpG\b"),
    ("GM-CSF", r"\bGM[- ]?CSF\b"),
    ("alum", r"\balum\b|\baluminium hydroxide\b"),
    ("LNP", r"\blipid nanoparticle\b|\bLNP\b"),
]
_EFFECT = [
    ("increase", r"\bsignificantly (?:increased|improved|enhanced|higher|greater|prolonged)\b"),
    ("decrease", r"\bsignificantly (?:decreased|reduced|lower|smaller|delayed|inhibited)\b"),
    ("no effect", r"\bno significant (?:difference|effect|change|improvement)\b|"
                  r"\bdid not (?:significantly )?(?:differ|improve|increase|reduce)\b"),
]
_PVALUE = [
    ("p", r"\bp\s*[<=≤]\s*(0?\.\d+|\d+\.?\d*e-\d+)\b"),
]

FIELD_RULES: list[tuple[str, list[tuple[str, str]], str, str]] = [
    # (field, patterns, confidence, unit)
    ("sample_size", _SAMPLE_SIZE, "high", "per group"),
    ("randomization", _RANDOMIZATION, "high", ""),
    ("blinding", _BLINDING, "medium", ""),
    ("power_calculation", _POWER, "high", ""),
    ("ethics_approval", _ETHICS, "high", ""),
    ("statistical_test", _STATS, "high", ""),
    ("multiplicity_correction", _MULTIPLICITY, "high", ""),
    ("controls", _CONTROLS, "high", ""),
    ("sex", _SEX, "high", ""),
    ("route", _ROUTE, "medium", ""),
    ("endpoint", _ENDPOINT, "medium", ""),
    ("timepoint", _TIMEPOINT, "low", ""),
    ("predictor", _PREDICTOR, "high", ""),
    ("threshold", _THRESHOLD, "high", ""),
    ("peptide_length", _PEPTIDE_LEN, "medium", "mer"),
    ("adjuvant", _ADJUVANT, "high", ""),
    ("effect_direction", _EFFECT, "medium", ""),
    ("p_value", _PVALUE, "high", ""),
]

# Fields where several distinct values are informative rather than conflicting.
MULTI_VALUE = {"controls", "statistical_test", "predictor", "endpoint", "adjuvant"}

ALL_FIELDS = [f for f, *_ in FIELD_RULES] + ["model_system", "hla_alleles"]


# ------------------------------------------------------------------ extract

def _paper_text(con: sqlite3.Connection, paper_id: str) -> list[tuple[str, str]]:
    """(section_kind, text) for a paper, methods first — that is where it lives."""
    rows = con.execute(
        "SELECT kind, heading, text FROM sections WHERE paper_id=? ORDER BY ord",
        (paper_id,),
    ).fetchall()
    if rows:
        ordered = sorted(rows, key=lambda r: 0 if r["kind"] == "methods" else 1)
        return [(r["kind"] or r["heading"] or "body", r["text"]) for r in ordered]

    row = con.execute("SELECT title, abstract FROM papers WHERE id=?", (paper_id,)).fetchone()
    if row is None:
        return []
    return [("abstract", f"{row['title'] or ''}. {row['abstract'] or ''}")]


def extract_paper(con: sqlite3.Connection, paper_id: str) -> dict[str, list[Extraction]]:
    """Extract every field from one paper. Values carry their source sentence."""
    blocks = _paper_text(con, paper_id)
    if not blocks:
        return {}

    found: dict[str, list[Extraction]] = {}

    for field_name, patterns, confidence, unit in FIELD_RULES:
        seen_values: set[str] = set()
        for section, text in blocks:
            sentences = _sentences(text)
            for label, pattern in patterns:
                rx = re.compile(pattern, re.I)
                for s in sentences:
                    m = rx.search(s)
                    if not m:
                        continue
                    value = (m.group(1).strip() if m.groups() and m.group(1) else label)
                    if value.lower() in seen_values:
                        continue
                    seen_values.add(value.lower())
                    found.setdefault(field_name, []).append(Extraction(
                        field=field_name, value=value, evidence=s[:400],
                        section=section, confidence=confidence, unit=unit))
                    if field_name not in MULTI_VALUE:
                        break
                if field_name in found and field_name not in MULTI_VALUE:
                    break
            if field_name in found and field_name not in MULTI_VALUE:
                break

    # Entities are better handled by the gazetteer than by ad-hoc regex.
    from . import graph

    joined = " ".join(t for _, t in blocks)
    entities = graph.extract(joined)
    models = [n for kind, n in entities if kind in ("cell_line", "mouse")]
    if models:
        found["model_system"] = [Extraction(
            field="model_system", value=", ".join(sorted(set(models))[:6]),
            evidence=_context_for(models[0], joined), section="methods",
            confidence="high")]
    alleles = [n for kind, n in entities if kind == "hla"]
    if alleles:
        found["hla_alleles"] = [Extraction(
            field="hla_alleles", value=", ".join(sorted(set(alleles))[:8]),
            evidence=_context_for(alleles[0], joined), section="methods",
            confidence="high")]

    return found


def _context_for(term: str, text: str, width: int = 320) -> str:
    idx = text.lower().find(term.lower())
    if idx < 0:
        return ""
    start = max(0, idx - width // 3)
    return ("…" if start else "") + text[start : start + width].strip() + "…"


def store(con: sqlite3.Connection, paper_id: str,
          extractions: dict[str, list[Extraction]]) -> int:
    con.execute("DELETE FROM extractions WHERE paper_id=?", (paper_id,))
    rows = [
        (paper_id, e.field, e.value, e.unit, e.evidence, e.section, e.confidence, db.now())
        for items in extractions.values() for e in items
    ]
    con.executemany(
        """INSERT INTO extractions(paper_id, field, value, unit, evidence, section,
                                   confidence, extracted_at)
           VALUES (?,?,?,?,?,?,?,?)""", rows)
    con.commit()
    return len(rows)


def extract_corpus(con: sqlite3.Connection, *, limit: int | None = None,
                   only_fulltext: bool = True) -> dict[str, int]:
    """Extract from every paper that has not been done yet."""
    sql = """SELECT p.id FROM papers p
             WHERE NOT EXISTS (SELECT 1 FROM extractions e WHERE e.paper_id = p.id)"""
    if only_fulltext:
        sql += " AND EXISTS (SELECT 1 FROM sections s WHERE s.paper_id = p.id)"
    sql += " ORDER BY p.score DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"

    papers = [r["id"] for r in con.execute(sql).fetchall()]
    total = 0
    for pid in papers:
        total += store(con, pid, extract_paper(con, pid))
    return {"papers": len(papers), "values": total}


def get_extractions(con: sqlite3.Connection, paper_id: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in con.execute(
        "SELECT * FROM extractions WHERE paper_id=? ORDER BY field", (paper_id,)
    ).fetchall():
        out.setdefault(r["field"], []).append(dict(r))
    return out


# -------------------------------------------------------------- the matrix

def matrix(con: sqlite3.Connection, paper_ids: list[str],
           fields: list[str] | None = None) -> dict[str, Any]:
    """A comparison table across papers, every cell carrying its provenance.

    Cells are one of: a value with its source sentence, or the explicit string
    "not reported". The second is not a hole in the table — it is a finding
    about the paper, and it is why the columns are worth reading downwards.
    """
    fields = fields or ["sample_size", "model_system", "randomization", "blinding",
                        "power_calculation", "controls", "statistical_test",
                        "endpoint", "effect_direction"]
    if not paper_ids:
        return {"fields": fields, "rows": [], "gaps": {}}

    ph = ",".join("?" * len(paper_ids))
    papers = {r["id"]: dict(r) for r in con.execute(
        f"SELECT id, title, authors, journal, pub_date, url, evidence_tier "
        f"FROM papers WHERE id IN ({ph})", paper_ids).fetchall()}

    rows = []
    gaps = {f: 0 for f in fields}
    for pid in paper_ids:
        paper = papers.get(pid)
        if not paper:
            continue
        ex = get_extractions(con, pid)
        cells = {}
        for f in fields:
            items = ex.get(f) or []
            if items:
                cells[f] = {
                    "value": "; ".join(sorted({i["value"] for i in items}))[:120],
                    "evidence": items[0]["evidence"],
                    "section": items[0]["section"],
                    "confidence": items[0]["confidence"],
                    "reported": True,
                }
            else:
                cells[f] = {"value": NOT_REPORTED, "evidence": "", "reported": False}
                gaps[f] += 1
        rows.append({
            "paper_id": pid, "title": paper["title"], "authors": paper["authors"],
            "journal": paper["journal"], "year": str(paper["pub_date"] or "")[:4],
            "url": paper["url"], "tier": paper["evidence_tier"], "cells": cells,
        })

    n = len(rows) or 1
    return {
        "fields": fields, "rows": rows, "gaps": gaps,
        "gap_summary": {f: f"{gaps[f]}/{n} papers do not report this" for f in fields},
        "note": ("Every reported cell links to the sentence it came from. "
                 "Extraction is pattern-based rather than model-generated: it cannot "
                 "invent a value, and it will miss unusually-phrased reporting. Treat "
                 f"'{NOT_REPORTED}' as 'not found by a literal reading' and check the "
                 "paper before citing the absence."),
    }


# --------------------------------------------------------- the methods audit

@dataclass
class AuditItem:
    key: str
    label: str
    present: bool
    evidence: str = ""
    why_it_matters: str = ""


# The reporting essentials. Drawn from what ARRIVE 2.0 and CONSORT ask for,
# restricted to items that are actually detectable in prose.
AUDIT_ITEMS = [
    ("sample_size", "Group sizes reported",
     "Without n, an effect size means nothing and the study cannot be appraised."),
    ("randomization", "Randomization stated",
     "Unrandomized allocation lets baseline differences masquerade as treatment effects."),
    ("blinding", "Blinding stated",
     "Unblinded caliper measurement is a documented source of inflated effect sizes."),
    ("power_calculation", "Power or sample-size calculation",
     "Without it, a null result cannot be distinguished from an underpowered one."),
    ("controls", "Controls described",
     "In vaccine work the adjuvant-alone arm is the one that separates a vaccine "
     "effect from innate activation, and it is the one most often missing."),
    ("statistical_test", "Statistical test named",
     "An unnamed test cannot be checked for appropriateness to the data."),
    ("ethics_approval", "Ethics approval stated",
     "Required for animal and human work; its absence is a reporting failure."),
]


def audit(con: sqlite3.Connection, paper_id: str) -> dict[str, Any]:
    """Score one paper against the reporting essentials.

    Deliberately **not** a single quality number. A score invites ranking
    papers by it, and this measures *reporting*, which correlates with rigour
    but is not the same thing. The output is a checklist, because the useful
    action is "go and check whether they blinded", not "this paper scores 4.2".
    """
    ex = get_extractions(con, paper_id)
    if not ex:
        ex = {k: [e.to_dict() for e in v] for k, v in extract_paper(con, paper_id).items()}

    has_fulltext = bool(con.execute(
        "SELECT 1 FROM sections WHERE paper_id=? LIMIT 1", (paper_id,)).fetchone())

    items = []
    for key, label, why in AUDIT_ITEMS:
        hits = ex.get(key) or []
        items.append(AuditItem(
            key=key, label=label, present=bool(hits),
            evidence=(hits[0].get("evidence", "") if hits else ""),
            why_it_matters=why))

    present = sum(1 for i in items if i.present)
    return {
        "paper_id": paper_id,
        "items": [vars(i) for i in items],
        "reported": present,
        "total": len(items),
        "completeness": round(present / len(items), 2),
        "basis": "full text" if has_fulltext else "abstract only",
        "caveat": (
            "Abstract only — most methods reporting lives in the Methods section, so "
            "this understates the paper. Fetch the full text before drawing a "
            "conclusion." if not has_fulltext else
            "Absence here means a literal reading found no statement of it. That is "
            "usually a real reporting gap, but check the paper before saying so in "
            "print."),
    }


def audit_corpus(con: sqlite3.Connection, paper_ids: list[str] | None = None,
                 limit: int = 50) -> dict[str, Any]:
    """Where the literature you are reading is weakest, in aggregate."""
    if paper_ids is None:
        paper_ids = [r["id"] for r in con.execute(
            """SELECT p.id FROM papers p
               WHERE EXISTS (SELECT 1 FROM sections s WHERE s.paper_id = p.id)
               ORDER BY p.score DESC LIMIT ?""", (limit,)).fetchall()]
    if not paper_ids:
        return {"papers": 0, "by_item": {}, "note": "No full text in the corpus yet."}

    audits = [audit(con, pid) for pid in paper_ids]
    by_item: dict[str, dict[str, Any]] = {}
    for key, label, why in AUDIT_ITEMS:
        n = sum(1 for a in audits
                if any(i["key"] == key and i["present"] for i in a["items"]))
        by_item[key] = {"label": label, "reported_by": n, "of": len(audits),
                        "share": round(n / len(audits), 2), "why_it_matters": why}

    worst = sorted(by_item.items(), key=lambda kv: kv[1]["share"])[:3]
    return {
        "papers": len(audits),
        "by_item": by_item,
        "weakest": [{"item": k, **v} for k, v in worst],
        "mean_completeness": round(sum(a["completeness"] for a in audits) / len(audits), 2),
    }


# --------------------------------------------- quality-weighted evidence

def evidence_weight(con: sqlite3.Connection, paper_id: str) -> dict[str, Any]:
    """How much this paper's support for a claim is actually worth.

    Scite's own documentation notes the gap this fills: a supporting citation
    "might come from a paper where the experimental evidence is weak", because
    citation counts do not know anything about the citing study's design.

    Weight = evidence tier (study design) × reporting completeness (was it
    built and described well enough to detect anything). Both components are
    shown, because a single number hides which one is doing the work.
    """
    row = con.execute(
        "SELECT evidence_tier, study_type, source FROM papers WHERE id=?", (paper_id,)
    ).fetchone()
    tier = (row["evidence_tier"] if row and row["evidence_tier"] is not None else 2)
    a = audit(con, paper_id)

    design = tier / 5.0
    reporting = a["completeness"]
    # Reporting cannot rescue a weak design, and a strong design that is
    # unreported cannot be appraised — so multiply rather than average, with a
    # floor so a paper is never worth literally nothing.
    weight = round(max(0.1, design * (0.4 + 0.6 * reporting)), 3)

    return {
        "paper_id": paper_id,
        "weight": weight,
        "evidence_tier": tier,
        "study_type": row["study_type"] if row else None,
        "reporting_completeness": reporting,
        "reporting_basis": a["basis"],
        "explanation": (
            f"tier {tier}/5 design × {int(reporting * 100)}% of reporting essentials "
            f"present → weight {weight}. A well-designed study that does not report "
            f"its methods cannot be appraised, and good reporting cannot rescue a "
            f"weak design."),
    }


def weigh_support(con: sqlite3.Connection, paper_ids: list[str]) -> dict[str, Any]:
    """Aggregate weighted support across the papers backing a claim."""
    if not paper_ids:
        return {"n": 0, "weighted": 0.0, "verdict": "no supporting sources"}

    weights = [evidence_weight(con, pid) for pid in paper_ids]
    total = round(sum(w["weight"] for w in weights), 3)
    best = max(weights, key=lambda w: w["weight"])

    if total >= 2.0:
        verdict = "well supported — several well-designed, well-reported studies"
    elif total >= 1.0:
        verdict = "supported, but the weight rests on few studies"
    elif best["weight"] >= 0.5:
        verdict = "one reasonable study; treat as provisional"
    else:
        verdict = ("weakly supported — the citing studies are low-tier, poorly "
                   "reported, or both. Count of citations is not the same as weight "
                   "of evidence.")

    return {
        "n": len(weights), "weighted": total, "verdict": verdict,
        "per_paper": weights,
        "strongest": best["paper_id"],
    }
