"""OpenAlex — the discovery backbone.

OpenAlex is the right primary source for a *discovery* workflow, as distinct
from the nightly sweep's Europe PMC:

* ~250M works, spanning every journal rather than the biomedical subset
* it carries the **citation graph** (``referenced_works`` and cited-by), which
  is what makes snowballing possible — the ResearchRabbit / Connected Papers
  capability
* it absorbs **Unpaywall**, so ``best_oa_location`` gives a legal free full-text
  URL directly, with no separate API call

No key required. Supplying a contact email joins the "polite pool" and gets
noticeably better latency, so `config.USER_AGENT` carries one.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .http import get

BASE = "https://api.openalex.org"


def _mailto() -> dict[str, str]:
    from .. import config

    m = re.search(r"mailto:([^)\s]+)", config.USER_AGENT)
    return {"mailto": m.group(1)} if m else {}


def search(
    query: str,
    *,
    per_page: int = 50,
    max_results: int = 200,
    from_year: int | None = None,
    to_year: int | None = None,
    open_access: bool | None = None,
    types: list[str] | None = None,
    timeout: float = 30,
    delay: float = 0.2,
) -> list[dict[str, Any]]:
    """Full-text search across OpenAlex works."""
    filters = []
    if from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")
    if to_year:
        filters.append(f"to_publication_date:{to_year}-12-31")
    if open_access is True:
        filters.append("is_oa:true")
    if types:
        filters.append("type:" + "|".join(types))

    out: list[dict[str, Any]] = []
    cursor = "*"
    while len(out) < max_results:
        params: dict[str, Any] = {
            "search": query,
            "per-page": min(per_page, 200),
            "cursor": cursor,
            **_mailto(),
        }
        if filters:
            params["filter"] = ",".join(filters)

        r = get(f"{BASE}/works", params, timeout=timeout, delay=delay)
        if r is None:
            break
        try:
            data = r.json()
        except ValueError:
            break
        results = data.get("results") or []
        out.extend(results)
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not results or not cursor:
            break
    return out[:max_results]


def by_doi(doi: str, *, timeout: float = 30) -> dict[str, Any] | None:
    doi = (doi or "").strip().replace("https://doi.org/", "")
    if not doi:
        return None
    r = get(f"{BASE}/works/doi:{doi}", _mailto(), timeout=timeout, delay=0.2)
    if r is None:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def references(work: dict[str, Any], *, limit: int = 60, timeout: float = 30
               ) -> list[dict[str, Any]]:
    """Backward snowballing: the works this paper cites."""
    ids = (work.get("referenced_works") or [])[:limit]
    return _fetch_ids(ids, timeout=timeout)


def citations(work_id: str, *, limit: int = 60, timeout: float = 30
              ) -> list[dict[str, Any]]:
    """Forward snowballing: the works that cite this paper."""
    short = (work_id or "").rsplit("/", 1)[-1]
    if not short:
        return []
    r = get(f"{BASE}/works",
            {"filter": f"cites:{short}", "per-page": min(limit, 200), **_mailto()},
            timeout=timeout, delay=0.2)
    if r is None:
        return []
    try:
        return (r.json().get("results") or [])[:limit]
    except ValueError:
        return []


def _fetch_ids(ids: Iterable[str], *, timeout: float = 30) -> list[dict[str, Any]]:
    """Batch-fetch works by OpenAlex id, 50 at a time (the API's OR limit)."""
    ids = [i.rsplit("/", 1)[-1] for i in ids if i]
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        r = get(f"{BASE}/works",
                {"filter": "openalex_id:" + "|".join(batch), "per-page": 50, **_mailto()},
                timeout=timeout, delay=0.2)
        if r is None:
            continue
        try:
            out.extend(r.json().get("results") or [])
        except ValueError:
            continue
    return out


def _abstract(work: dict[str, Any]) -> str:
    """OpenAlex stores abstracts as an inverted index; rebuild the text."""
    inv = work.get("abstract_inverted_index")
    if not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def normalize(work: dict[str, Any]) -> dict[str, Any]:
    """Map an OpenAlex work onto the shape the discovery layer uses."""
    doi = (work.get("doi") or "").replace("https://doi.org/", "")
    ids = work.get("ids") or {}
    pmid = (ids.get("pmid") or "").rsplit("/", 1)[-1]
    pmcid = (ids.get("pmcid") or "").rsplit("/", 1)[-1]

    authorships = work.get("authorships") or []
    authors = [a.get("author", {}).get("display_name", "") for a in authorships]

    loc = work.get("best_oa_location") or work.get("primary_location") or {}
    source = (loc.get("source") or {}) if isinstance(loc, dict) else {}

    oa = work.get("open_access") or {}
    pdf_url = (loc.get("pdf_url") if isinstance(loc, dict) else None) or oa.get("oa_url") or ""

    return {
        "id": f"DOI:{doi}" if doi else (work.get("id") or ""),
        "openalex_id": work.get("id", ""),
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "title": (work.get("display_name") or work.get("title") or "").strip(),
        "abstract": _abstract(work),
        "authors": ", ".join(a for a in authors if a),
        "n_authors": len(authors),
        "journal": source.get("display_name", "") or work.get("type", ""),
        "pub_date": work.get("publication_date", "") or str(work.get("publication_year", "")),
        "year": work.get("publication_year"),
        "type": work.get("type", ""),
        "cited_by": work.get("cited_by_count", 0),
        "is_oa": bool(oa.get("is_oa")),
        "oa_status": oa.get("oa_status", ""),
        "pdf_url": pdf_url,
        "url": (f"https://doi.org/{doi}" if doi else work.get("id", "")),
        "referenced_works": work.get("referenced_works") or [],
        "concepts": [c.get("display_name", "") for c in (work.get("topics") or [])][:6],
        "retracted": bool(work.get("is_retracted")),
        "provider": "openalex",
    }
