"""PubMed via NCBI E-utilities.

Included alongside Europe PMC and OpenAlex because it is the source clinicians
and reviewers will name, it has MeSH indexing that the others lack, and its
publication-type tags (``Randomized Controlled Trial``, ``Meta-Analysis``) are
a cleaner study-design signal than inferring the design from prose.

No API key required, but the rate limit is 3 requests/second without one and 10
with. ``NCBI_API_KEY`` in the environment is used if present.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Any

from .http import get

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _key() -> dict[str, str]:
    k = os.environ.get("NCBI_API_KEY", "").strip()
    return {"api_key": k} if k else {}


def search(
    query: str,
    *,
    max_results: int = 100,
    from_year: int | None = None,
    to_year: int | None = None,
    publication_types: list[str] | None = None,
    timeout: float = 30,
) -> list[str]:
    """Return PMIDs for a query. Supports full PubMed query syntax."""
    term = query
    if publication_types:
        types = " OR ".join(f'"{t}"[Publication Type]' for t in publication_types)
        term = f"({term}) AND ({types})"
    if from_year or to_year:
        term += f" AND ({from_year or 1900}:{to_year or 3000}[dp])"

    r = get(f"{BASE}/esearch.fcgi",
            {"db": "pubmed", "term": term, "retmax": min(max_results, 500),
             "retmode": "json", "sort": "relevance", **_key()},
            timeout=timeout, delay=0.34)
    if r is None:
        return []
    try:
        return (r.json().get("esearchresult") or {}).get("idlist", []) or []
    except ValueError:
        return []


def fetch(pmids: list[str], *, timeout: float = 30) -> list[dict[str, Any]]:
    """Fetch full records for PMIDs, in batches."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(pmids), 100):
        batch = pmids[i : i + 100]
        r = get(f"{BASE}/efetch.fcgi",
                {"db": "pubmed", "id": ",".join(batch), "retmode": "xml", **_key()},
                timeout=timeout, delay=0.34, accept="application/xml")
        if r is None:
            continue
        out.extend(_parse(r.text))
    return out


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _parse(xml_text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    out = []
    for art in root.iter("PubmedArticle"):
        medline = art.find("MedlineCitation")
        if medline is None:
            continue
        pmid = _text(medline.find("PMID"))
        article = medline.find("Article")
        if article is None:
            continue

        authors = []
        for a in article.iter("Author"):
            last, initials = _text(a.find("LastName")), _text(a.find("Initials"))
            if last:
                authors.append(f"{last} {initials}".strip())

        doi = ""
        for aid in art.iter("ArticleId"):
            if aid.get("IdType") == "doi":
                doi = _text(aid)

        pub_types = [_text(pt) for pt in article.iter("PublicationType")]
        mesh = [_text(d.find("DescriptorName")) for d in medline.iter("MeshHeading")]

        journal = article.find("Journal")
        year = ""
        if journal is not None:
            year = _text(journal.find(".//PubDate/Year")) or _text(journal.find(".//PubDate/MedlineDate"))[:4]

        out.append({
            "id": f"MED:{pmid}" if pmid else "",
            "pmid": pmid,
            "doi": doi,
            "title": _text(article.find("ArticleTitle")).rstrip("."),
            "abstract": _text(article.find("Abstract")),
            "authors": ", ".join(authors),
            "n_authors": len(authors),
            "journal": _text(journal.find("Title")) if journal is not None else "",
            "pub_date": year,
            "year": int(year) if year.isdigit() else None,
            "type": ", ".join(pub_types),
            "mesh": [m for m in mesh if m][:12],
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "is_oa": False,       # PubMed does not say; OpenAlex/EPMC do
            "pdf_url": "",
            "retracted": any("Retracted" in t for t in pub_types),
            "provider": "pubmed",
        })
    return out


def search_and_fetch(query: str, **kw) -> list[dict[str, Any]]:
    pmids = search(query, **{k: v for k, v in kw.items()
                             if k in ("max_results", "from_year", "to_year",
                                      "publication_types", "timeout")})
    return fetch(pmids) if pmids else []
