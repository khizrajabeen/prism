"""Guided literature discovery: keywords → questions → federated search → screening.

This is the *discovery* workflow, distinct from both the nightly sweep (which
watches a standing interest profile) and `Ask` (which answers from what you
already have). Here you start from a few keywords and end with a screened,
deduplicated, exportable list of publications across every journal — with free
full text fetched where it legally exists.

The shape, and why
------------------
**1. Clarify before searching.** Two keywords are almost never a query. The
gap between "neoantigen vaccine" and what you actually want — mouse or human,
class I or II, therapeutic or prophylactic, last three years or all time — is
where a search either becomes useful or returns four thousand rows. So the
first step asks, and the questions are generated from what the keywords
actually contain rather than from a fixed script.

**2. Search everything at once.** OpenAlex (~250M works, every journal),
PubMed (MeSH indexing and clean publication-type tags), Europe PMC (preprints
and OA full text), bioRxiv/medRxiv. Deduplicated by DOI, then by normalized
title, because the same paper appears in all four.

**3. Snowball.** Backward through references and forward through citations, via
OpenAlex's citation graph. This is how a real review is built — keyword search
finds the seed set, and the citation network finds what keyword search missed
because the authors used different words.

**4. Screen.** Inclusion and exclusion criteria applied to a result set, with
the counts kept, so a systematic search is reproducible and reportable rather
than a pile of tabs.

**5. Fetch.** Open-access full text where a legal copy exists — OpenAlex
carries Unpaywall, so the free-copy location comes back with the metadata at no
extra call. Paywalled papers are never scraped; they get a link to open through
your institution.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config, db
from .sources import europepmc, openalex, preprints, pubmed
from .sources.http import get

SOURCES = ("openalex", "pubmed", "europepmc", "preprints")


# ------------------------------------------------------------ clarification

@dataclass
class Facet:
    id: str
    question: str
    options: list[dict[str, str]]
    multi: bool = False
    why: str = ""


# Detectors decide which questions are worth asking for these keywords. Asking
# "human or mouse?" when the user already typed "MC38" wastes their time.
_ORGANISM_HINTS = re.compile(r"\b(mouse|mice|murine|MC38|B16|CT26|4T1|C57BL|BALB|in vivo|patient|human|clinical)\b", re.I)
_CLASS_HINTS = re.compile(r"\bclass (I|II|1|2)\b|\bCD[48]\b|\bMHC-?I{1,2}\b", re.I)
_DESIGN_HINTS = re.compile(r"\b(trial|randomi|phase|cohort|review|meta-analys)\b", re.I)
_METHOD_HINTS = re.compile(r"\b(ELISpot|tetramer|immunopeptidom|sequencing|prediction|assay|flow)\b", re.I)


def clarify(keywords: str) -> list[Facet]:
    """Generate the questions worth asking for these keywords.

    Only asks what the keywords have not already answered. The point is to
    narrow, not to interrogate.
    """
    kw = keywords or ""
    facets: list[Facet] = []

    if not _ORGANISM_HINTS.search(kw):
        facets.append(Facet(
            id="system", question="Which system are you interested in?",
            why="This is the single biggest filter — mouse and human literatures barely overlap.",
            multi=True,
            options=[
                {"value": "human", "label": "Human / clinical", "query": "(human OR patient OR clinical)"},
                {"value": "mouse", "label": "Mouse / preclinical in vivo", "query": "(mouse OR murine OR mice)"},
                {"value": "invitro", "label": "In vitro / cell lines", "query": '("in vitro" OR "cell line")'},
                {"value": "computational", "label": "Computational / prediction", "query": "(computational OR prediction OR algorithm OR benchmark)"},
            ]))

    if not _DESIGN_HINTS.search(kw):
        facets.append(Facet(
            id="design", question="What kind of study do you need?",
            why="Evidence tier follows study design; this also decides how many results you get.",
            multi=True,
            options=[
                {"value": "trials", "label": "Clinical trials only", "query": '("clinical trial" OR "phase 1" OR "phase 2" OR randomized)',
                 "pubmed_type": "Clinical Trial"},
                {"value": "primary", "label": "Primary research (no reviews)", "query": ""},
                {"value": "reviews", "label": "Reviews and meta-analyses", "query": "(review OR meta-analysis)",
                 "pubmed_type": "Review"},
                {"value": "methods", "label": "Methods / benchmarks", "query": "(method OR benchmark OR pipeline OR tool)"},
                {"value": "any", "label": "Anything", "query": ""},
            ]))

    if not _CLASS_HINTS.search(kw) and re.search(r"\b(neoantigen|epitope|MHC|HLA|vaccine|T cell)\b", kw, re.I):
        facets.append(Facet(
            id="mhc_class", question="Class I, class II, or both?",
            why="Class II is a much smaller and differently-shaped literature; leaving it implicit usually means you only see class I.",
            multi=True,
            options=[
                {"value": "i", "label": "Class I / CD8", "query": '("class I" OR CD8 OR "MHC-I")'},
                {"value": "ii", "label": "Class II / CD4", "query": '("class II" OR CD4 OR "MHC-II")'},
                {"value": "both", "label": "Both / don't mind", "query": ""},
            ]))

    facets.append(Facet(
        id="recency", question="How far back should I look?",
        why="This field moves fast; anything older than about five years needs checking against current practice.",
        options=[
            {"value": "2", "label": "Last 2 years"},
            {"value": "5", "label": "Last 5 years"},
            {"value": "10", "label": "Last 10 years"},
            {"value": "0", "label": "All time"},
        ]))

    facets.append(Facet(
        id="access", question="Do you need full text you can actually read?",
        why="Open access means the PDF can be fetched here; otherwise you get a link to open through your library.",
        options=[
            {"value": "oa", "label": "Open access only"},
            {"value": "any", "label": "Everything (paywalled included)"},
        ]))

    if not _METHOD_HINTS.search(kw):
        facets.append(Facet(
            id="extra", question="Any specific technique or model to require? (optional)",
            why="A named assay or cell line is the most effective single narrowing term there is.",
            options=[{"value": "", "label": "(free text)"}]))

    return facets


def build_query(keywords: str, answers: dict[str, Any]) -> dict[str, Any]:
    """Turn keywords + answers into a query plan, shown to the user before it runs."""
    kw = (keywords or "").strip()
    parts = [f"({kw})"] if kw else []
    facets = {f.id: f for f in clarify(keywords)}
    notes: list[str] = []
    pubmed_types: list[str] = []
    exclude_reviews = False

    for fid, answer in (answers or {}).items():
        if fid in ("recency", "access") or answer in (None, "", []):
            continue
        values = answer if isinstance(answer, list) else [answer]
        facet = facets.get(fid)
        if fid == "extra":
            free = " ".join(str(v) for v in values).strip()
            if free:
                parts.append(f"({free})")
                notes.append(f"requiring: {free}")
            continue
        if facet is None:
            continue
        clauses = []
        for v in values:
            opt = next((o for o in facet.options if o.get("value") == v), None)
            if not opt:
                continue
            if opt.get("query"):
                clauses.append(opt["query"])
            if opt.get("pubmed_type"):
                pubmed_types.append(opt["pubmed_type"])
            if v == "primary":
                exclude_reviews = True
        if clauses:
            parts.append("(" + " OR ".join(clauses) + ")")
            notes.append(f"{facet.question} → {', '.join(str(v) for v in values)}")

    recency = str((answers or {}).get("recency", "5"))
    from_year = None
    if recency.isdigit() and int(recency) > 0:
        import datetime as _dt
        from_year = _dt.date.today().year - int(recency)

    return {
        "keywords": kw,
        "query": " AND ".join(parts) if parts else kw,
        "from_year": from_year,
        "open_access": (answers or {}).get("access") == "oa",
        "pubmed_types": pubmed_types,
        "exclude_reviews": exclude_reviews,
        "narrowing": notes,
    }


# ------------------------------------------------------------------- search

def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())[:120]


def deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge the same paper arriving from several sources.

    DOI first, then normalized title. Merged records keep the union of the
    identifiers and the best available full-text link, and record which sources
    found them — agreement across sources is itself a small quality signal.
    """
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    title_index: dict[str, str] = {}   # normalized title -> key

    def _merge_into(target: dict[str, Any], rec: dict[str, Any]) -> None:
        target["found_by"] = sorted(set(target.get("found_by", []) + [rec.get("provider", "?")]))
        for f in ("doi", "pmid", "pmcid", "abstract", "authors", "journal",
                  "pub_date", "type", "pdf_url", "url", "openalex_id", "year"):
            if not target.get(f) and rec.get(f):
                target[f] = rec[f]
        target["is_oa"] = bool(target.get("is_oa") or rec.get("is_oa"))
        target["cited_by"] = max(target.get("cited_by") or 0, rec.get("cited_by") or 0)
        if rec.get("mesh") and not target.get("mesh"):
            target["mesh"] = rec["mesh"]

    # Two passes. DOI-bearing records first, so that a record arriving without a
    # DOI can still be recognised as the same paper by title — the single most
    # common merge case, since Europe PMC and PubMed often omit the DOI that
    # OpenAlex carries. A one-pass version keys them differently and silently
    # reports the same paper twice.
    for rec in sorted(records, key=lambda r: 0 if (r.get("doi") or "").strip() else 1):
        doi = (rec.get("doi") or "").lower().strip()
        norm_title = _norm_title(rec.get("title", ""))

        key = None
        if doi:
            key = f"doi:{doi}"
        elif norm_title and norm_title in title_index:
            key = title_index[norm_title]
        elif norm_title:
            key = f"ti:{norm_title}"
        if not key:
            continue

        if key in by_key:
            _merge_into(by_key[key], rec)
        else:
            fresh = dict(rec)
            fresh["found_by"] = [rec.get("provider", "?")]
            fresh["is_oa"] = bool(fresh.get("is_oa"))
            by_key[key] = fresh
            order.append(key)

        if norm_title:
            title_index.setdefault(norm_title, key)

    return [by_key[k] for k in order]


def federated_search(
    plan: dict[str, Any],
    *,
    sources: tuple[str, ...] = SOURCES,
    per_source: int = 60,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the plan against every source and merge the results."""
    emit = progress or (lambda m: None)
    query = plan["query"]
    records: list[dict[str, Any]] = []
    per_source_counts: dict[str, int] = {}
    errors: list[str] = []

    if "openalex" in sources:
        try:
            raw = openalex.search(query, max_results=per_source,
                                  from_year=plan.get("from_year"),
                                  open_access=plan.get("open_access") or None)
            got = [openalex.normalize(w) for w in raw]
            records += got
            per_source_counts["openalex"] = len(got)
            emit(f"  openalex: {len(got)}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"openalex: {e}")

    if "pubmed" in sources:
        try:
            got = pubmed.search_and_fetch(
                query, max_results=per_source, from_year=plan.get("from_year"),
                publication_types=plan.get("pubmed_types") or None)
            records += got
            per_source_counts["pubmed"] = len(got)
            emit(f"  pubmed: {len(got)}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"pubmed: {e}")

    if "europepmc" in sources:
        try:
            since = f"{plan['from_year']}-01-01" if plan.get("from_year") else "1900-01-01"
            import datetime as _dt
            raw = europepmc.search(query, since, _dt.date.today().isoformat(),
                                   max_results=per_source)
            got = []
            for rec in europepmc.iter_normalized(raw):
                rec["provider"] = "europepmc"
                rec["cited_by"] = 0
                rec["pdf_url"] = ""
                got.append(rec)
            records += got
            per_source_counts["europepmc"] = len(got)
            emit(f"  europepmc: {len(got)}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"europepmc: {e}")

    merged = deduplicate(records)

    if plan.get("exclude_reviews"):
        before = len(merged)
        merged = [m for m in merged
                  if not re.search(r"review|meta-analys|editorial|comment",
                                   f"{m.get('type','')} {m.get('title','')}", re.I)]
        emit(f"  excluded {before - len(merged)} reviews/editorials")

    merged = [m for m in merged if not m.get("retracted")]
    merged.sort(key=lambda m: (-(m.get("cited_by") or 0), m.get("pub_date") or ""), reverse=False)
    merged.sort(key=lambda m: -(len(m.get("found_by", [])) * 2 + (1 if m.get("is_oa") else 0)))

    return {
        "plan": plan,
        "results": merged,
        "counts": {
            "retrieved": len(records),
            "unique": len(merged),
            "open_access": sum(1 for m in merged if m.get("is_oa")),
            "with_pdf": sum(1 for m in merged if m.get("pdf_url")),
            "per_source": per_source_counts,
        },
        "errors": errors,
    }


# --------------------------------------------------------------- snowballing

def snowball(
    doi_or_id: str,
    *,
    direction: str = "both",
    limit: int = 40,
) -> dict[str, Any]:
    """Expand from a seed paper through the citation graph.

    Backward (`references`) finds the foundations the authors built on; forward
    (`citations`) finds who has since built on, replicated, or contradicted it.
    Forward citations are where you discover that your seed paper was refuted
    in 2025 — which keyword search will never tell you.
    """
    work = openalex.by_doi(doi_or_id) if "/" in doi_or_id or doi_or_id.startswith("10.") else None
    if work is None:
        found = openalex.search(doi_or_id, max_results=1)
        work = found[0] if found else None
    if work is None:
        return {"error": f"could not resolve {doi_or_id!r} in OpenAlex", "seed": None}

    seed = openalex.normalize(work)
    out: dict[str, Any] = {"seed": seed, "backward": [], "forward": []}

    if direction in ("both", "backward"):
        out["backward"] = deduplicate(
            [openalex.normalize(w) for w in openalex.references(work, limit=limit)])
    if direction in ("both", "forward"):
        out["forward"] = deduplicate(
            [openalex.normalize(w) for w in openalex.citations(work.get("id", ""), limit=limit)])

    out["counts"] = {"backward": len(out["backward"]), "forward": len(out["forward"])}
    out["note"] = (
        "Backward citations are what this paper stood on; forward citations are "
        "who has since built on, replicated, or contradicted it. Check the "
        "forward list before treating the seed as current."
    )
    return out


# ----------------------------------------------------------------- screening

INCLUDE, EXCLUDE, MAYBE = "include", "exclude", "maybe"


def auto_screen(
    results: list[dict[str, Any]],
    *,
    include_terms: list[str] | None = None,
    exclude_terms: list[str] | None = None,
    min_year: int | None = None,
    require_oa: bool = False,
) -> dict[str, Any]:
    """Apply explicit criteria and report the counts, PRISMA-style.

    This is a *triage*, not a judgement: everything is kept with a reason
    attached, so you can audit and override. A screening step that silently
    discards records is not reproducible, and reproducibility is the entire
    point of screening.
    """
    include_terms = [t.lower() for t in (include_terms or []) if t.strip()]
    exclude_terms = [t.lower() for t in (exclude_terms or []) if t.strip()]

    screened = []
    counts = {INCLUDE: 0, EXCLUDE: 0, MAYBE: 0}

    for r in results:
        blob = f"{r.get('title','')} {r.get('abstract','')}".lower()
        reasons: list[str] = []
        verdict = MAYBE

        hit_exclude = [t for t in exclude_terms if t in blob]
        hit_include = [t for t in include_terms if t in blob]

        if min_year and (r.get("year") or 0) and r["year"] < min_year:
            verdict = EXCLUDE
            reasons.append(f"published {r['year']}, before {min_year}")
        elif require_oa and not r.get("is_oa"):
            verdict = EXCLUDE
            reasons.append("not open access")
        elif hit_exclude:
            verdict = EXCLUDE
            reasons.append("matched exclusion: " + ", ".join(hit_exclude))
        elif include_terms and hit_include:
            verdict = INCLUDE
            reasons.append("matched inclusion: " + ", ".join(hit_include))
        elif include_terms:
            verdict = MAYBE
            reasons.append("no inclusion term matched — read the abstract")
        else:
            verdict = MAYBE
            reasons.append("no criteria set")

        if not r.get("abstract"):
            reasons.append("no abstract available — cannot screen on content")

        item = dict(r)
        item["screen"] = verdict
        item["screen_reasons"] = reasons
        screened.append(item)
        counts[verdict] += 1

    return {
        "results": screened,
        "counts": counts,
        "criteria": {
            "include_terms": include_terms, "exclude_terms": exclude_terms,
            "min_year": min_year, "require_oa": require_oa,
        },
        "note": ("Nothing was discarded — excluded records are kept with their "
                 "reason so the screen is auditable and reversible."),
    }


# ------------------------------------------------------------------- saving

def save_search(con: sqlite3.Connection, name: str, plan: dict, result: dict) -> int:
    cur = con.execute(
        """INSERT INTO searches(name, created_at, keywords, plan, counts, n_results)
           VALUES (?,?,?,?,?,?)""",
        (name, db.now(), plan.get("keywords", ""), json.dumps(plan),
         json.dumps(result.get("counts", {})), len(result.get("results", []))),
    )
    sid = int(cur.lastrowid)
    con.executemany(
        """INSERT INTO search_results(search_id, doi, pmid, title, authors, journal,
                                      pub_date, abstract, url, pdf_url, is_oa, cited_by,
                                      found_by, screen, screen_reasons)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(sid, r.get("doi", ""), r.get("pmid", ""), r.get("title", ""),
          r.get("authors", ""), r.get("journal", ""), r.get("pub_date", ""),
          (r.get("abstract") or "")[:8000], r.get("url", ""), r.get("pdf_url", ""),
          int(bool(r.get("is_oa"))), r.get("cited_by") or 0,
          ",".join(r.get("found_by", [])), r.get("screen", ""),
          "; ".join(r.get("screen_reasons", [])))
         for r in result.get("results", [])],
    )
    con.commit()
    return sid


def get_searches(con: sqlite3.Connection, limit: int = 25) -> list[dict]:
    return db.rows_to_dicts(con.execute(
        "SELECT * FROM searches ORDER BY id DESC LIMIT ?", (limit,)).fetchall())


def get_results(con: sqlite3.Connection, search_id: int) -> list[dict]:
    return db.rows_to_dicts(con.execute(
        "SELECT * FROM search_results WHERE search_id=? ORDER BY cited_by DESC",
        (search_id,)).fetchall())


def set_screen(con: sqlite3.Connection, result_id: int, verdict: str, note: str = "") -> None:
    con.execute("UPDATE search_results SET screen=?, screen_reasons=? WHERE id=?",
                (verdict, note, result_id))
    con.commit()


# ------------------------------------------------------------------ fetching

def download_pdf(record: dict[str, Any], dest_dir: Path | None = None) -> dict[str, Any]:
    """Fetch an open-access PDF.

    Only follows the OA location the metadata already provides. Paywalled
    papers are never scraped: that gets institutional IP ranges blocked, and
    the `inbox/` route exists for papers you open through your library.
    """
    dest_dir = dest_dir or (config.HOME / "library")
    dest_dir.mkdir(parents=True, exist_ok=True)

    url = record.get("pdf_url") or ""
    if not url:
        return {
            "ok": False,
            "reason": "no open-access copy known for this record",
            "next": ("Open it through your institution, save the PDF into "
                     f"{config.INBOX_DIR}, then run `neobrain ingest-pdf --inbox`."),
            "url": record.get("url", ""),
        }

    safe = re.sub(r"[^A-Za-z0-9._-]", "_", (record.get("doi") or record.get("title", "paper"))[:80])
    path = dest_dir / f"{safe}.pdf"
    if path.exists():
        return {"ok": True, "path": str(path), "cached": True}

    r = get(url, timeout=60, delay=0.5, accept="application/pdf")
    if r is None:
        return {"ok": False, "reason": f"download failed from {url}", "url": url}
    if not r.content[:5].startswith(b"%PDF"):
        return {"ok": False, "reason": "the OA link did not return a PDF (often a landing page)",
                "url": url}

    path.write_bytes(r.content)
    return {"ok": True, "path": str(path), "bytes": len(r.content), "cached": False}


# ------------------------------------------------------------------- export

def export(results: list[dict[str, Any]], fmt: str = "bibtex") -> str:
    """Export a result set as BibTeX, RIS, CSV, or Markdown."""
    fmt = fmt.lower()
    if fmt == "bibtex":
        out = []
        for r in results:
            key = re.sub(r"[^A-Za-z0-9]", "", (r.get("doi") or r.get("title", "ref"))[:40])
            out.append(
                f"@article{{{key},\n  title   = {{{r.get('title','')}}},\n"
                f"  author  = {{{r.get('authors','')}}},\n"
                f"  journal = {{{r.get('journal','')}}},\n"
                f"  year    = {{{str(r.get('pub_date',''))[:4]}}},\n"
                + (f"  doi     = {{{r['doi']}}},\n" if r.get("doi") else "")
                + f"  url     = {{{r.get('url','')}}}\n}}")
        return "\n\n".join(out)

    if fmt == "ris":
        out = []
        for r in results:
            out.append("\n".join(filter(None, [
                "TY  - JOUR",
                f"TI  - {r.get('title','')}",
                *[f"AU  - {a.strip()}" for a in (r.get("authors") or "").split(",") if a.strip()],
                f"JO  - {r.get('journal','')}",
                f"PY  - {str(r.get('pub_date',''))[:4]}",
                f"DO  - {r['doi']}" if r.get("doi") else None,
                f"UR  - {r.get('url','')}",
                f"AB  - {(r.get('abstract') or '')[:2000]}",
                "ER  - ",
            ])))
        return "\n\n".join(out)

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["title", "authors", "journal", "year", "doi", "url", "pdf_url",
                    "open_access", "cited_by", "found_by", "screen"])
        for r in results:
            w.writerow([r.get("title", ""), r.get("authors", ""), r.get("journal", ""),
                        str(r.get("pub_date", ""))[:4], r.get("doi", ""), r.get("url", ""),
                        r.get("pdf_url", ""), "yes" if r.get("is_oa") else "no",
                        r.get("cited_by", 0), ",".join(r.get("found_by", [])),
                        r.get("screen", "")])
        return buf.getvalue()

    lines = []
    for i, r in enumerate(results, 1):
        oa = " · **OA**" if r.get("is_oa") else ""
        lines.append(f"{i}. **{r.get('title','')}**  \n"
                     f"   {r.get('authors','')}  \n"
                     f"   *{r.get('journal','')}* {str(r.get('pub_date',''))[:4]}{oa} · "
                     f"[link]({r.get('url','')})"
                     + (f" · [PDF]({r['pdf_url']})" if r.get("pdf_url") else ""))
    return "\n\n".join(lines)
