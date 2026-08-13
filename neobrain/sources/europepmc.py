"""Europe PMC — the primary source.

Europe PMC is the right default for this domain: it indexes PubMed/MEDLINE
*and* preprints (bioRxiv, medRxiv, Research Square) in one query language,
marks open-access status, and needs no API key. The preprint coverage matters
more here than in most fields — neoantigen prediction methods routinely appear
on bioRxiv six to twelve months before the journal version.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from .http import get

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_TAGS = re.compile(r"<[^>]+>")


def clean(text: str | None) -> str:
    return _TAGS.sub("", text or "").replace("\xa0", " ").strip()


def search(
    query: str,
    since: str,
    until: str,
    *,
    max_results: int = 300,
    page_size: int = 100,
    timeout: float = 30,
    delay: float = 0.34,
    date_field: str = "FIRST_PDATE",
) -> list[dict[str, Any]]:
    """Run one date-bounded query, following cursorMark pagination."""
    full = f"({query}) AND ({date_field}:[{since} TO {until}])"
    params = {
        "query": full,
        "format": "json",
        "pageSize": min(page_size, 1000),
        "resultType": "core",
        "cursorMark": "*",
    }
    out: list[dict[str, Any]] = []
    seen_cursors: set[str] = set()

    while len(out) < max_results:
        r = get(BASE, params, timeout=timeout, delay=delay)
        if r is None:
            break
        try:
            data = r.json()
        except ValueError:
            break
        batch = data.get("resultList", {}).get("result", []) or []
        out.extend(batch)
        cursor = data.get("nextCursorMark")
        if not batch or not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
        params["cursorMark"] = cursor

    return out[:max_results]


def normalize(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a Europe PMC record onto the `papers` row shape (unscored)."""
    src = rec.get("source", "")
    ident = rec.get("pmid") or rec.get("pmcid") or rec.get("id") or ""
    uid = f"{src}:{ident}"

    pmcid = rec.get("pmcid")
    doi = rec.get("doi")
    if pmcid:
        url = f"https://europepmc.org/article/PMC/{pmcid}"
    elif doi:
        url = f"https://doi.org/{doi}"
    elif rec.get("pmid"):
        url = f"https://pubmed.ncbi.nlm.nih.gov/{rec['pmid']}/"
    else:
        url = f"https://europepmc.org/search?query={uid}"

    is_oa = rec.get("isOpenAccess") == "Y" or rec.get("inEPMC") == "Y"

    return {
        "id": uid,
        "source": "preprint" if src == "PPR" else "journal",
        "provider": "europepmc",
        "title": clean(rec.get("title")).rstrip("."),
        "abstract": clean(rec.get("abstractText")),
        "authors": rec.get("authorString", ""),
        "journal": (
            rec.get("journalTitle")
            or rec.get("bookOrReportDetails", {}).get("publisher", "")
            if isinstance(rec.get("bookOrReportDetails"), dict)
            else rec.get("journalTitle") or "preprint"
        ) or "preprint",
        "pub_date": rec.get("firstPublicationDate", "") or rec.get("pubYear", ""),
        "doi": doi or "",
        "pmid": rec.get("pmid", "") or "",
        "pmcid": pmcid or "",
        "url": url,
        "is_oa": is_oa,
        "pub_type": ", ".join(rec.get("pubTypeList", {}).get("pubType", []) or [])
        if isinstance(rec.get("pubTypeList"), dict)
        else "",
    }


def iter_normalized(records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for rec in records:
        try:
            yield normalize(rec)
        except Exception:  # a single malformed record must not kill the sweep
            continue
