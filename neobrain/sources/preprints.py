"""bioRxiv / medRxiv direct feed.

Europe PMC indexes preprints, but with a lag of a few days and occasional
gaps. For a field where the method you need may have been posted on Tuesday,
polling the bioRxiv API directly and filtering locally closes that window.

The API returns everything posted in a date range — no server-side search — so
we filter client-side against the same interest terms used everywhere else.
"""

from __future__ import annotations

from typing import Any

from .http import get

BASE = "https://api.biorxiv.org/details"


def recent(server: str, since: str, until: str, *, max_results: int = 2000,
           timeout: float = 30, delay: float = 0.5) -> list[dict[str, Any]]:
    """Fetch every preprint posted on `server` ('biorxiv'|'medrxiv') in a range."""
    out: list[dict[str, Any]] = []
    cursor = 0
    while len(out) < max_results:
        url = f"{BASE}/{server}/{since}/{until}/{cursor}"
        r = get(url, timeout=timeout, delay=delay)
        if r is None:
            break
        try:
            data = r.json()
        except ValueError:
            break
        batch = data.get("collection", []) or []
        if not batch:
            break
        out.extend(batch)
        cursor += len(batch)
        total = (data.get("messages") or [{}])[0].get("total", 0)
        if cursor >= int(total or 0):
            break
    return out[:max_results]


def matches_interest(rec: dict[str, Any], terms: list[str]) -> bool:
    blob = f"{rec.get('title', '')} {rec.get('abstract', '')}".lower()
    return any(t.lower() in blob for t in terms)


def normalize(rec: dict[str, Any], server: str) -> dict[str, Any]:
    doi = rec.get("doi", "")
    return {
        "id": f"DOI:{doi}",
        "source": "preprint",
        "provider": server,
        "title": (rec.get("title") or "").strip().rstrip("."),
        "abstract": (rec.get("abstract") or "").strip(),
        "authors": rec.get("authors", ""),
        "journal": f"{server} preprint",
        "pub_date": rec.get("date", ""),
        "doi": doi,
        "pmid": "",
        "pmcid": "",
        "url": f"https://doi.org/{doi}" if doi else "",
        "is_oa": True,
        "pub_type": "preprint",
    }
