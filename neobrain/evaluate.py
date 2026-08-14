"""The eval harness — measuring what this thing actually gets right.

The argument for building this before any new feature: **a tool that reports
its own measured recall is more trustworthy than one that reports none.**
Elicit publishes 95% search recall and 96% extraction accuracy against 994
Cochrane reviews. NeoBrain will publish worse numbers against a much smaller
gold set, and that is fine — the number existing at all is the point, and it
turns every future retrieval or extraction change into a measurable one rather
than a plausible one.

The gold set
------------
``config/gold/*.yaml``. Each file is one annotated paper: its methods text and
the values a careful human reader says are in it. Ship set is a **bootstrap**
of hand-written passages whose ground truth is known by construction, clearly
labelled as such. It exists so the harness runs on a fresh clone; it is not a
substitute for annotating papers you have actually read.

To make the numbers mean something, add your own:

    neobrain eval add MED:39012345 --sample-size 10 --randomization yes ...

Fifty papers you have read carefully is the target. At that size the numbers
are defensible; below about twenty they are indicative at best, and the harness
says so rather than printing a confident-looking decimal.

What is measured
----------------
* **Extraction** — precision and recall per field. Precision should stay at or
  near 1.0: pattern extraction cannot fabricate, so anything below 1.0 is a
  pattern matching the wrong span and is a bug, not a tuning problem.
* **Methods audit** — Cohen's κ against the human checklist, because raw
  agreement is inflated when most items are present.
* **Retrieval** — recall@k for queries with known-relevant papers.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, db, extract

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# The gold set lives under the active NEOBRAIN_HOME, resolved per call rather
# than at import: tests and `NEOBRAIN_HOME=... neobrain eval` both move it.
PACKAGED_GOLD = Path(__file__).resolve().parent.parent / "config" / "gold"

# Below this the numbers are indicative, not defensible, and every report says so.
DEFENSIBLE_N = 20


def gold_dir() -> Path:
    return config.CONFIG_DIR / "gold"


@dataclass
class GoldPaper:
    id: str
    title: str = ""
    text: str = ""                       # methods passage, when self-contained
    paper_id: str = ""                   # or a reference to a corpus paper
    fields: dict[str, Any] = field(default_factory=dict)
    relevant_to: list[str] = field(default_factory=list)   # queries this answers
    source: str = "bootstrap"            # bootstrap | annotated

    @classmethod
    def from_dict(cls, d: dict) -> "GoldPaper":
        return cls(
            id=str(d.get("id", "")), title=d.get("title", ""),
            text=d.get("text", ""), paper_id=d.get("paper_id", ""),
            fields=d.get("fields", {}) or {},
            relevant_to=d.get("relevant_to", []) or [],
            source=d.get("source", "bootstrap"),
        )


def load_gold(directory: Path | None = None) -> list[GoldPaper]:
    """Every annotated paper, from the active home and from the shipped bootstrap.

    Both are read, and the home wins on id collision: your annotation of a paper
    should override a bootstrap entry with the same id, never be shadowed by it.
    """
    if yaml is None:
        return []
    roots = [directory] if directory else [gold_dir(), PACKAGED_GOLD]
    by_id: dict[str, GoldPaper] = {}
    seen_roots: set[Path] = set()
    for root in roots:
        if root is None or not root.exists() or root.resolve() in seen_roots:
            continue
        seen_roots.add(root.resolve())
        for path in sorted(root.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for item in data.get("papers", []) or []:
                gp = GoldPaper.from_dict(item)
                if gp.id and gp.id not in by_id:
                    by_id[gp.id] = gp
    return list(by_id.values())


# ------------------------------------------------------------------ metrics

def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3),
            "f1": round(f1, 3), "tp": tp, "fp": fp, "fn": fn}


def cohens_kappa(a: list[bool], b: list[bool]) -> float:
    """Agreement corrected for chance.

    Raw agreement is misleading on a checklist where most items are usually
    present — two raters who both say "yes" to everything agree 90% of the time
    and have learned nothing about each other.
    """
    if not a or len(a) != len(b):
        return 0.0
    n = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    pa_yes, pb_yes = sum(a) / n, sum(b) / n
    expected = pa_yes * pb_yes + (1 - pa_yes) * (1 - pb_yes)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return round((observed - expected) / (1 - expected), 3)


def _normalise(value: Any) -> set[str]:
    """Compare gold and extracted values without punctuation noise."""
    if value is None:
        return set()
    if isinstance(value, bool):
        return {"yes"} if value else set()
    if isinstance(value, (int, float)):
        return {str(value)}
    if isinstance(value, list):
        out: set[str] = set()
        for v in value:
            out |= _normalise(v)
        return out
    text = str(value).strip().lower()
    if text in ("", "none", "null", "not reported", "no"):
        return set()
    if text in ("yes", "true", "present"):
        return {"yes"}
    return {t.strip() for t in text.replace(";", ",").split(",") if t.strip()}


# Fields the gold set annotates as present/absent rather than by value. The
# extractor returns a descriptive token for these ("randomized", "blinded",
# "approved"), so comparing them to the literal string "yes" scores every
# correct extraction as a miss. The first run of this harness reported recall
# 0.00 on all five while the audit reported κ 0.88 against the same data —
# which is what a bug in the *measurement* looks like, and is worth more than
# the tuning it would otherwise have caused.
BOOLEAN_FIELDS = {
    "randomization", "blinding", "power_calculation", "ethics_approval",
    "multiplicity_correction",
}


def _matches(gold: set[str], got: set[str], *, boolean: bool = False) -> bool:
    """A hit if the extraction agrees with the annotation.

    For boolean fields, agreement means "found anything at all". For valued
    fields, substring either way, because "randomised" should match
    "randomized" and "10" should match "n = 10".
    """
    if not gold or not got:
        return False
    if boolean:
        return True          # both non-empty: annotation says present, extractor found it
    if gold & got:
        return True
    return any(g in x or x in g for g in gold for x in got)


# ------------------------------------------------------------- extraction

def evaluate_extraction(gold: list[GoldPaper] | None = None,
                        con: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Precision and recall per field against the gold annotations."""
    gold = gold if gold is not None else load_gold()
    if not gold:
        return {"n": 0, "note": "No gold set. See config/gold/README.md."}

    close_after = con is None
    con = con or db.connect()

    per_field: dict[str, dict[str, int]] = {}
    absent_correct = absent_missed = 0
    scored: list[GoldPaper] = []
    uningested: list[str] = []

    for gp in gold:
        ex = _extract_for(con, gp)
        if ex is None:
            uningested.append(gp.paper_id)
            continue
        scored.append(gp)
        got = {f: {i.value for i in items} for f, items in ex.items()}

        for f, gold_value in gp.fields.items():
            gold_tokens = _normalise(gold_value)
            got_tokens: set[str] = set()
            for v in got.get(f, set()):
                got_tokens |= _normalise(v)

            bucket = per_field.setdefault(f, {"tp": 0, "fp": 0, "fn": 0})
            if gold_tokens and got_tokens:
                if _matches(gold_tokens, got_tokens,
                            boolean=f in BOOLEAN_FIELDS):
                    bucket["tp"] += 1
                else:
                    # Extracted something, but the wrong thing: both a miss and
                    # a false positive. Precision below 1.0 is a pattern bug.
                    bucket["fp"] += 1
                    bucket["fn"] += 1
            elif gold_tokens and not got_tokens:
                bucket["fn"] += 1
            elif not gold_tokens and got_tokens:
                bucket["fp"] += 1
                absent_missed += 1
            else:
                absent_correct += 1

    fields = {f: _prf(v["tp"], v["fp"], v["fn"]) for f, v in sorted(per_field.items())}
    tp = sum(v["tp"] for v in per_field.values())
    fp = sum(v["fp"] for v in per_field.values())
    fn = sum(v["fn"] for v in per_field.values())

    if close_after:
        con.close()

    n = len(scored)
    return {
        "n": n,
        "overall": _prf(tp, fp, fn),
        "by_field": fields,
        "absence_correct": absent_correct,
        "absence_wrong": absent_missed,
        "uningested": uningested,
        "defensible": n >= DEFENSIBLE_N,
        "note": (
            f"n={n} annotated papers. "
            + ("" if n >= DEFENSIBLE_N else
               f"Below {DEFENSIBLE_N} these numbers are indicative, not defensible — "
               f"add papers you have read with `neobrain eval add`. ")
            + (f"{len(uningested)} annotation(s) skipped: the paper is not in this "
               f"corpus, so scoring it would measure the library rather than the "
               f"extractor. " if uningested else "")
            + "Precision below 1.00 on a field means a pattern matched the wrong "
              "span, which is a bug rather than a tuning question."
        ),
    }


def _extract_for(con: sqlite3.Connection, gp: GoldPaper) -> dict[str, list] | None:
    """Run extraction over a gold item, whether it is inline text or a real paper.

    Returns None when the annotation names a paper this corpus does not hold.
    Scoring that as a miss would measure the size of the library, not the
    quality of the extractor — the same class of mistake that made the first
    run of this harness report a retrieval failure that did not exist.
    """
    if gp.paper_id:
        row = con.execute("SELECT 1 FROM papers WHERE id=?", (gp.paper_id,)).fetchone()
        if row is None:
            return None
        return extract.extract_paper(con, gp.paper_id)

    # Inline passage: stage it in a temporary row so the real code path runs.
    tmp_id = f"GOLD:{gp.id}"
    db.upsert_paper(con, {
        "id": tmp_id, "source": "journal", "provider": "gold", "title": gp.title,
        "abstract": "", "authors": "", "journal": "", "pub_date": "", "doi": "",
        "pmid": "", "pmcid": "", "url": "", "is_oa": 0, "score": 0,
        "matched": "", "buckets": "gold"})
    con.execute("DELETE FROM sections WHERE paper_id=?", (tmp_id,))
    con.execute(
        "INSERT INTO sections(paper_id, ord, heading, kind, text) "
        "VALUES (?,0,'Methods','methods',?)", (tmp_id, gp.text))
    con.commit()
    try:
        return extract.extract_paper(con, tmp_id)
    finally:
        con.execute("DELETE FROM papers WHERE id=?", (tmp_id,))
        con.commit()


# ------------------------------------------------------------------ audit

def evaluate_audit(gold: list[GoldPaper] | None = None,
                   con: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Cohen's κ between the automated methods audit and the human checklist."""
    gold = gold if gold is not None else load_gold()
    if not gold:
        return {"n": 0, "kappa": None, "note": "No gold set."}

    close_after = con is None
    con = con or db.connect()

    human: list[bool] = []
    machine: list[bool] = []
    per_item: dict[str, list[tuple[bool, bool]]] = {}

    scored = 0
    for gp in gold:
        ex = _extract_for(con, gp)
        if ex is None:       # annotated but not in this corpus — see _extract_for
            continue
        scored += 1
        for key, _label, _why in extract.AUDIT_ITEMS:
            h = bool(_normalise(gp.fields.get(key)))
            m = bool(ex.get(key))
            human.append(h)
            machine.append(m)
            per_item.setdefault(key, []).append((h, m))

    if close_after:
        con.close()

    if not human:
        return {"n": 0, "kappa": None,
                "note": "No gold item could be scored — every annotation names a "
                        "paper this corpus does not hold."}

    k = cohens_kappa(human, machine)
    agreement = sum(1 for h, m in zip(human, machine) if h == m) / len(human) if human else 0

    return {
        "n": scored,
        "kappa": k,
        "raw_agreement": round(agreement, 3),
        "by_item": {
            key: {
                "kappa": cohens_kappa([h for h, _ in pairs], [m for _, m in pairs]),
                "human_yes": sum(1 for h, _ in pairs if h),
                "machine_yes": sum(1 for _, m in pairs if m),
                "n": len(pairs),
            }
            for key, pairs in per_item.items()
        },
        "interpretation": _kappa_words(k),
    }


def _kappa_words(k: float | None) -> str:
    if k is None:
        return "not measured"
    if k >= 0.81:
        return "almost perfect agreement with the human reader"
    if k >= 0.61:
        return "substantial agreement"
    if k >= 0.41:
        return "moderate agreement — usable, but check disagreements"
    if k >= 0.21:
        return "fair agreement — the audit needs work before it is trusted"
    return "poor agreement — do not rely on the automated audit"


# --------------------------------------------------------------- retrieval

def evaluate_retrieval(gold: list[GoldPaper] | None = None,
                       con: sqlite3.Connection | None = None,
                       k: int = 10) -> dict[str, Any]:
    """recall@k for queries whose relevant papers are annotated."""
    from . import retrieve

    gold = gold if gold is not None else load_gold()
    close_after = con is None
    con = con or db.connect()

    queries: dict[str, set[str]] = {}
    inline_only = 0
    absent: list[str] = []
    for gp in gold:
        if not gp.relevant_to:
            continue
        if not gp.paper_id:
            # An inline passage is staged and deleted during extraction, so it
            # is never in the corpus and can never be retrieved. Counting it as
            # a miss would report a retrieval failure that does not exist.
            inline_only += 1
            continue
        row = con.execute("SELECT 1 FROM papers WHERE id=?", (gp.paper_id,)).fetchone()
        if row is None:
            # Annotated, but the paper is not in this corpus — a gap in the
            # library, not a gap in retrieval. Named, so it can be fixed.
            absent.append(gp.paper_id)
            continue
        for q in gp.relevant_to:
            queries.setdefault(q, set()).add(gp.paper_id)

    if not queries:
        if close_after:
            con.close()
        if absent:
            note = (f"Not measurable: {len(absent)} annotated paper(s) carry queries but "
                    f"are not in the corpus ({', '.join(absent[:4])}). Sweep or ingest "
                    f"them and this number appears.")
        elif inline_only:
            note = (f"Not measurable: {inline_only} gold item(s) carry queries but are "
                    f"inline passages rather than corpus papers. Retrieval recall needs "
                    f"gold entries added with `neobrain eval add <paper_id>`, so the "
                    f"paper is really in the corpus to be retrieved.")
        else:
            note = "No gold queries — add `relevant_to:` entries."
        return {"queries": 0, "skipped_inline": inline_only,
                "missing_from_corpus": absent, "note": note}

    hits = 0
    total = 0
    detail = []
    for q, expected in queries.items():
        found = {h.paper_id for h in retrieve.search(q, k=k, con=con) if h.paper_id}
        got = expected & found
        hits += len(got)
        total += len(expected)
        detail.append({"query": q, "expected": len(expected), "found": len(got)})

    if close_after:
        con.close()

    return {
        "queries": len(queries),
        "recall_at_k": round(hits / total, 3) if total else 0.0,
        "k": k,
        "detail": detail,
        "skipped_inline": inline_only,
        "missing_from_corpus": absent,
    }


# ------------------------------------------------------------------ report

def run_all(con: sqlite3.Connection | None = None) -> dict[str, Any]:
    gold = load_gold()
    close_after = con is None
    con = con or db.connect()
    try:
        result = {
            "gold_papers": len(gold),
            "annotated_by_hand": sum(1 for g in gold if g.source == "annotated"),
            "extraction": evaluate_extraction(gold, con),
            "audit": evaluate_audit(gold, con),
            "retrieval": evaluate_retrieval(gold, con),
            "calibration": calibration(con),
        }
    finally:
        if close_after:
            con.close()
    return result


def calibration(con: sqlite3.Connection) -> dict[str, Any]:
    """Per-verdict-class precision from claim checks you have overridden.

    A system that publishes its own error rate per verdict class is doing
    something almost no shipped tool does, and it is the strongest available
    answer to "why should I trust this". Empty until you start overriding
    verdicts, which is honest — it has not earned a number yet.
    """
    rows = con.execute(
        """SELECT verdict, COUNT(*) n,
                  SUM(CASE WHEN note LIKE '%[overridden%' THEN 1 ELSE 0 END) overridden
           FROM claim_checks GROUP BY verdict"""
    ).fetchall()
    out = {}
    for r in rows:
        n, over = r["n"], r["overridden"] or 0
        out[r["verdict"]] = {
            "emitted": n, "overridden": over,
            "precision": round((n - over) / n, 3) if n else None,
            "weak": bool(n >= 10 and (n - over) / n < 0.7),
        }
    return {
        "by_verdict": out,
        "note": ("Records every verdict and every time you overrode one. Override "
                 "with `neobrain check --wrong <id>`. A class below 0.70 precision "
                 "gets a reliability warning attached at emission."
                 if out else
                 "No verdicts recorded yet — this table earns its numbers in use."),
    }


def format_report(result: dict[str, Any], width: int = 92) -> str:
    import textwrap

    L = ["MEASURED PERFORMANCE", ""]
    ex = result.get("extraction", {})
    if ex.get("n"):
        o = ex["overall"]
        flag = "" if ex.get("defensible") else "   ← indicative only"
        L.append(f"  Extraction (n={ex['n']} papers){flag}")
        L.append(f"    precision {o['precision']:.2f} · recall {o['recall']:.2f} "
                 f"· F1 {o['f1']:.2f}")
        weak = [(f, m) for f, m in ex["by_field"].items() if m["recall"] < 0.7]
        for f, m in sorted(weak, key=lambda kv: kv[1]["recall"])[:5]:
            L.append(f"      weak: {f:<24} recall {m['recall']:.2f} "
                     f"({m['fn']} missed)")
        bad_precision = [f for f, m in ex["by_field"].items() if m["precision"] < 1.0]
        if bad_precision:
            L.append(f"      PRECISION BUG in: {', '.join(bad_precision)} "
                     f"(a pattern matched the wrong span)")
    else:
        L.append("  Extraction: no gold set — see config/gold/README.md")

    a = result.get("audit", {})
    if a.get("kappa") is not None:
        L += ["", f"  Methods audit (n={a['n']})",
              f"    Cohen's κ {a['kappa']:.2f} · raw agreement "
              f"{a['raw_agreement']:.2f} — {a['interpretation']}"]

    r = result.get("retrieval", {})
    if r.get("queries"):
        L += ["", "  Retrieval",
              f"    recall@{r['k']} {r['recall_at_k']:.2f} over {r['queries']} queries"]
        if r.get("missing_from_corpus"):
            L.append(f"      ({len(r['missing_from_corpus'])} annotated paper(s) not in "
                     f"the corpus, excluded rather than counted as misses)")
    elif r.get("note"):
        L += ["", "  Retrieval"]
        L += textwrap.wrap(r["note"], width=width - 4,
                           initial_indent="    ", subsequent_indent="    ")

    c = result.get("calibration", {})
    if c.get("by_verdict"):
        L += ["", "  Claim-check calibration"]
        for verdict, m in sorted(c["by_verdict"].items()):
            mark = "  ← weak" if m["weak"] else ""
            L.append(f"    {verdict:<24} emitted {m['emitted']:>4} · "
                     f"precision {m['precision'] if m['precision'] is not None else '—'}{mark}")
    else:
        L += ["", "  Calibration: " + c.get("note", "")]

    L += ["", textwrap.fill(ex.get("note", ""), width=width,
                            initial_indent="  ", subsequent_indent="  ")]
    return "\n".join(L)


def headline(con: sqlite3.Connection | None = None) -> list[str]:
    """Two lines for `neobrain doctor`, so the measurement is impossible to miss.

    The ship gate for this phase was "measured recall exists and is displayed".
    A number in a report nobody runs is not displayed; a number in the health
    check you run when something feels wrong is.
    """
    try:
        result = run_all(con)
    except Exception as e:  # noqa: BLE001
        return [f"eval            unavailable: {e}"]

    ex, a = result.get("extraction", {}), result.get("audit", {})
    if not ex.get("n"):
        return ["eval            no gold set — `neobrain eval add` to start measuring"]

    qualifier = "" if ex.get("defensible") else f" (n={ex['n']}, indicative)"
    lines = [f"extraction      P {ex['overall']['precision']:.2f} · "
             f"R {ex['overall']['recall']:.2f} · F1 {ex['overall']['f1']:.2f}{qualifier}"]
    if a.get("kappa") is not None:
        lines.append(f"methods audit   κ {a['kappa']:.2f} vs the human checklist")
    r = result.get("retrieval", {})
    if r.get("queries"):
        lines.append(f"retrieval       recall@{r['k']} {r['recall_at_k']:.2f} "
                     f"over {r['queries']} queries")
    return lines


# ------------------------------------------------------------ adding gold

def add_annotation(paper_id: str, fields: dict[str, Any], *,
                   title: str = "", relevant_to: list[str] | None = None) -> Path:
    """Append a hand-annotated paper to the gold set."""
    directory = gold_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "annotated.yaml"
    data = {"papers": []}
    if path.exists() and yaml is not None:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"papers": []}

    data.setdefault("papers", [])
    data["papers"] = [p for p in data["papers"] if p.get("id") != paper_id]
    data["papers"].append({
        "id": paper_id, "paper_id": paper_id, "title": title,
        "source": "annotated", "fields": fields,
        "relevant_to": relevant_to or [],
    })
    if yaml is not None:
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
    else:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
