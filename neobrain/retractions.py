"""Retraction and correction watch.

A research brain that cites a retracted paper has failed at its one job. Worse,
it fails silently and at exactly the wrong moment — the citation was fine when
you filed it, the retraction happened eighteen months later, and nothing in the
system had any reason to look again.

So this module does three things, in order of how much they matter:

1. **Check papers you actually depend on first.** Papers cited by a belief, a
   hypothesis, an experiment, or a claim check are checked before the rest of
   the library, because those are the ones whose retraction would change
   something you would otherwise write down as true.
2. **Propagate.** A retracted paper is not just a bad row in `papers`. It is a
   belief that no longer has the support you thought it had. `affected()` walks
   from the paper to every belief, hypothesis, experiment, and claim check that
   leans on it, and that list is what gets shown — not the paper.
3. **Say when it last looked.** `retraction_status IS NULL` means never
   checked, which is not the same as clean. Conflating those two is how a tool
   ends up reassuring you about a paper it has never examined, and it is the
   same distinction this codebase draws between `null` and `not reported`.

Sources
-------
Crossref is the primary signal: a work's ``update-to`` / ``updated-by``
relations name the retraction notice directly, and it covers every publisher
that deposits DOIs. PubMed's publication types are the fallback for anything
with a PMID, since ``Retracted Publication`` is assigned by NLM indexers and
sometimes lands before the publisher deposits the Crossref relation.

Neither is instant, and neither is complete. That is stated in the report
rather than papered over: this reduces the window in which you cite a retracted
paper, it does not close it.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any, Iterable

from . import db, journal
from .sources import http

CROSSREF = "https://api.crossref.org/works/"
PUBMED_SUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# Crossref `update-to` types, mapped to how loudly we should react. A retraction
# invalidates; an expression of concern warns; a correction means the numbers
# you extracted may have changed underneath you.
_CROSSREF_TYPES = {
    "retraction": "retracted",
    "withdrawal": "retracted",
    "removal": "retracted",
    "expression_of_concern": "concern",
    "concern": "concern",
    "correction": "corrected",
    "corrigendum": "corrected",
    "erratum": "corrected",
    "addendum": "corrected",
    "partial_retraction": "concern",
}

# PubMed publication types carry the same information for anything NLM indexes.
_PUBMED_TYPES = {
    "retracted publication": "retracted",
    "expression of concern": "concern",
    "corrected and republished article": "corrected",
}

# Ordered worst-first: a paper that is both corrected and retracted is retracted.
SEVERITY = ["retracted", "concern", "corrected", "clean"]

BLOCKING = {"retracted", "concern"}


def _worst(statuses: Iterable[str]) -> str:
    found = [s for s in statuses if s in SEVERITY]
    return min(found, key=SEVERITY.index) if found else "clean"


# ------------------------------------------------------------------ sources

def check_crossref(doi: str) -> tuple[str, str, str] | None:
    """(status, note, url) from Crossref, or None if the lookup failed."""
    if not doi:
        return None
    r = http.get(CROSSREF + doi.strip(), delay=0.2)
    if r is None:
        return None
    try:
        msg = r.json().get("message", {}) or {}
    except (ValueError, AttributeError):
        return None

    statuses, notes, url = [], [], ""
    for rel in msg.get("updated-by", []) or []:
        kind = str(rel.get("type", "")).lower().replace("-", "_")
        status = _CROSSREF_TYPES.get(kind)
        if status:
            statuses.append(status)
            when = (rel.get("updated", {}) or {}).get("date-time", "")[:10]
            notes.append(f"{kind.replace('_', ' ')}{f' ({when})' if when else ''}")
            url = url or (f"https://doi.org/{rel['DOI']}" if rel.get("DOI") else "")

    # Some publishers flag it on the work itself rather than through a relation.
    if str(msg.get("type", "")).lower() in ("retraction", "withdrawal"):
        statuses.append("retracted")
        notes.append("indexed as a retraction notice")

    return _worst(statuses), "; ".join(notes), url


def check_pubmed(pmid: str) -> tuple[str, str, str] | None:
    if not pmid:
        return None
    r = http.get(PUBMED_SUMMARY, {"db": "pubmed", "id": pmid, "retmode": "json"},
                 delay=0.34)
    if r is None:
        return None
    try:
        result = r.json().get("result", {}) or {}
    except (ValueError, AttributeError):
        return None
    record = result.get(str(pmid), {}) or {}

    statuses, notes = [], []
    for pubtype in record.get("pubtype", []) or []:
        status = _PUBMED_TYPES.get(str(pubtype).lower())
        if status:
            statuses.append(status)
            notes.append(f"PubMed: {pubtype}")
    return _worst(statuses), "; ".join(notes), ""


def check_paper(row: sqlite3.Row | dict) -> dict[str, str]:
    """Ask every source that can answer for this paper and take the worst answer.

    A source that cannot be reached is not evidence of cleanliness, so
    ``reachable`` is reported separately from ``status``.
    """
    doi = (row["doi"] if isinstance(row, sqlite3.Row) else row.get("doi")) or ""
    pmid = (row["pmid"] if isinstance(row, sqlite3.Row) else row.get("pmid")) or ""

    answers, notes, url, reachable = [], [], "", False
    for result in (check_crossref(doi), check_pubmed(pmid)):
        if result is None:
            continue
        reachable = True
        status, note, found_url = result
        answers.append(status)
        if note:
            notes.append(note)
        url = url or found_url

    return {
        "status": _worst(answers) if reachable else "",
        "note": "; ".join(notes),
        "url": url,
        "reachable": reachable,
    }


# -------------------------------------------------------------------- sweep

def _cited_paper_ids(con: sqlite3.Connection) -> set[str]:
    """Papers something in the brain actually leans on."""
    cited: set[str] = set()
    for sql in (
        "SELECT paper_id FROM belief_sources WHERE paper_id IS NOT NULL",
        "SELECT paper_id FROM evidence_links WHERE paper_id IS NOT NULL",
    ):
        cited |= {r["paper_id"] for r in con.execute(sql) if r["paper_id"]}
    for r in con.execute("SELECT sources FROM claim_checks WHERE sources IS NOT NULL"):
        try:
            for s in json.loads(r["sources"]) or []:
                pid = s.get("paper_id") if isinstance(s, dict) else None
                if pid:
                    cited.add(pid)
        except (ValueError, TypeError):
            continue
    return cited


def sweep(con: sqlite3.Connection, *, limit: int = 50,
          only_cited: bool = False, recheck_days: int = 90,
          progress=None) -> dict[str, Any]:
    """Check papers for retraction, most-depended-upon first.

    Cited papers are checked before uncited ones and re-checked more often,
    because a retraction only matters to the extent something rests on it.
    """
    emit = progress or (lambda m: None)
    cited = _cited_paper_ids(con)
    cutoff = (dt.datetime.now() - dt.timedelta(days=recheck_days)).isoformat(
        timespec="seconds")

    rows = con.execute(
        """SELECT id, title, doi, pmid, retraction_status, retraction_checked_at
           FROM papers
           WHERE (doi IS NOT NULL AND doi != '') OR (pmid IS NOT NULL AND pmid != '')"""
    ).fetchall()

    def priority(row) -> tuple:
        is_cited = row["id"] in cited
        never = not row["retraction_checked_at"]
        # cited-and-never-checked first, then cited, then never-checked, then oldest
        return (not is_cited, not never, row["retraction_checked_at"] or "")

    def is_due(row) -> bool:
        if only_cited and row["id"] not in cited:
            return False
        last = row["retraction_checked_at"]
        return not last or last < cutoff

    due = [r for r in rows if is_due(r)]
    due.sort(key=priority)
    due = due[:limit]

    checked = flagged = unreachable = 0
    found: list[dict] = []
    for row in due:
        result = check_paper(row)
        if not result["reachable"]:
            unreachable += 1
            continue
        checked += 1
        previous = row["retraction_status"]
        status = result["status"] or "clean"
        con.execute(
            """UPDATE papers SET retraction_status=?, retraction_note=?,
                                 retraction_url=?, retraction_checked_at=?
               WHERE id=?""",
            (status, result["note"], result["url"], db.now(), row["id"]))
        if status != "clean":
            flagged += 1
            found.append({"paper_id": row["id"], "title": row["title"],
                          "status": status, "note": result["note"],
                          "cited": row["id"] in cited, "new": previous != status})
            if previous != status:
                # The journal is append-only, so the day a paper turned is
                # recoverable even after the row is updated again later.
                journal.record(
                    con,
                    f"{row['id']} flagged {status}: {result['note'] or 'no detail'} "
                    f"— {row['title'] or 'untitled'}",
                    kind="observation", topic="retraction", source="retraction-watch",
                    importance=5 if status == "retracted" else 4)
        emit(f"  {row['id']}: {status}")
    con.commit()

    return {
        "considered": len(rows), "due": len(due), "checked": checked,
        "flagged": flagged, "unreachable": unreachable, "found": found,
        "cited_papers": len(cited),
        "note": ("Crossref and PubMed both lag the actual retraction notice, "
                 "often by weeks. This narrows the window in which you cite a "
                 "retracted paper; it does not close it."),
    }


# -------------------------------------------------------------- propagation

def affected(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Everything in the brain that rests on a flagged paper.

    This is the output that matters. A list of retracted papers is trivia; a
    list of *your beliefs that no longer have the support you thought* is a
    thing you have to act on before you write the next sentence.
    """
    flagged = {
        r["id"]: r for r in con.execute(
            "SELECT id, title, retraction_status, retraction_note, retraction_url "
            "FROM papers WHERE retraction_status IS NOT NULL "
            "AND retraction_status != 'clean'")
    }
    if not flagged:
        return []

    out: list[dict[str, Any]] = []

    for r in con.execute(
        """SELECT b.id, b.claim, b.confidence, bs.paper_id, bs.stance
           FROM beliefs b JOIN belief_sources bs ON bs.belief_id = b.id
           WHERE b.invalidated_at IS NULL"""
    ):
        paper = flagged.get(r["paper_id"])
        if paper:
            out.append({
                "kind": "belief", "id": r["id"], "text": r["claim"],
                "paper_id": r["paper_id"], "paper_title": paper["title"],
                "status": paper["retraction_status"], "note": paper["retraction_note"],
                "url": paper["retraction_url"], "stance": r["stance"],
                "detail": f"confidence {r['confidence']}",
            })

    for r in con.execute(
        """SELECT el.kind, el.item_id, el.paper_id, el.stance,
                  COALESCE(h.statement, e.title, d.decision, p.name) AS text
           FROM evidence_links el
           LEFT JOIN hypotheses  h ON el.kind='hypothesis' AND h.id = el.item_id
           LEFT JOIN experiments e ON el.kind='experiment' AND e.id = el.item_id
           LEFT JOIN decisions   d ON el.kind='decision'   AND d.id = el.item_id
           LEFT JOIN projects    p ON el.kind='project'    AND p.id = el.item_id
           WHERE el.paper_id IS NOT NULL"""
    ):
        paper = flagged.get(r["paper_id"])
        if paper:
            out.append({
                "kind": r["kind"], "id": r["item_id"], "text": r["text"] or "(untitled)",
                "paper_id": r["paper_id"], "paper_title": paper["title"],
                "status": paper["retraction_status"], "note": paper["retraction_note"],
                "url": paper["retraction_url"], "stance": r["stance"], "detail": "",
            })

    for r in con.execute(
        "SELECT id, claim, verdict, sources FROM claim_checks "
        "WHERE sources IS NOT NULL ORDER BY id DESC LIMIT 500"
    ):
        try:
            sources = json.loads(r["sources"]) or []
        except (ValueError, TypeError):
            continue
        for s in sources:
            pid = s.get("paper_id") if isinstance(s, dict) else None
            paper = flagged.get(pid)
            if paper:
                out.append({
                    "kind": "claim-check", "id": r["id"], "text": r["claim"],
                    "paper_id": pid, "paper_title": paper["title"],
                    "status": paper["retraction_status"],
                    "note": paper["retraction_note"], "url": paper["retraction_url"],
                    "stance": "", "detail": f"verdict {r['verdict']}",
                })

    # Worst status first, and within that, beliefs before everything else —
    # a belief is the thing most likely to end up in a manuscript unexamined.
    order = {"belief": 0, "hypothesis": 1, "claim-check": 2}
    out.sort(key=lambda a: (SEVERITY.index(a["status"]), order.get(a["kind"], 3)))
    return out


def blocking_count(con: sqlite3.Connection) -> int:
    """How many dependencies are on a retracted paper or one under concern."""
    return sum(1 for a in affected(con) if a["status"] in BLOCKING)


def coverage(con: sqlite3.Connection) -> dict[str, Any]:
    """How much of the library has actually been checked, and how recently.

    Reported alongside every retraction figure, because "0 retractions found"
    means nothing without "out of how many checked".
    """
    row = con.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN retraction_checked_at IS NOT NULL THEN 1 ELSE 0 END) checked,
                  SUM(CASE WHEN retraction_status IS NOT NULL
                            AND retraction_status != 'clean' THEN 1 ELSE 0 END) flagged,
                  MIN(retraction_checked_at) oldest,
                  MAX(retraction_checked_at) newest
           FROM papers"""
    ).fetchone()
    checkable = con.execute(
        "SELECT COUNT(*) n FROM papers WHERE (doi IS NOT NULL AND doi != '') "
        "OR (pmid IS NOT NULL AND pmid != '')"
    ).fetchone()["n"]
    total, checked = row["total"] or 0, row["checked"] or 0
    return {
        "papers": total,
        "checkable": checkable,
        "checked": checked,
        "unchecked": max(checkable - checked, 0),
        "flagged": row["flagged"] or 0,
        "oldest_check": row["oldest"],
        "newest_check": row["newest"],
        "fraction": round(checked / checkable, 3) if checkable else 0.0,
        "note": ("A paper with no DOI and no PMID cannot be checked at all — it is "
                 "counted in `papers` but not in `checkable`."),
    }


def status_of(con: sqlite3.Connection, paper_id: str) -> dict[str, Any]:
    """One paper's standing, with `unknown` kept distinct from `clean`."""
    row = con.execute(
        "SELECT retraction_status s, retraction_note n, retraction_url u, "
        "retraction_checked_at c FROM papers WHERE id=?", (paper_id,)).fetchone()
    if row is None:
        return {"status": "unknown", "checked_at": None, "note": "not in the corpus"}
    if not row["c"]:
        return {"status": "unknown", "checked_at": None,
                "note": "never checked — run `neobrain retractions --sweep`"}
    return {"status": row["s"] or "clean", "note": row["n"] or "",
            "url": row["u"] or "", "checked_at": row["c"]}
