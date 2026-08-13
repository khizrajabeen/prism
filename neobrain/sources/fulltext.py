"""Open-access full-text acquisition and section parsing.

Abstracts are not enough. The thing you actually want from a neoantigen paper
is buried in Methods: which predictor version, which HLA typing tool, what
peptide length, how many mice per group, which adjuvant at what dose. So for
open-access papers we pull the JATS XML from Europe PMC and split it into
labelled sections.

What we do *not* do is scrape publisher paywalls. That gets institutional IP
ranges blocked and is not worth it. For paywalled papers: open them through
your library proxy in a browser, save the PDF into ``inbox/``, and
``neobrain ingest-pdf --inbox`` will pull the text in through the same
section pipeline.
"""

from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from typing import Any

from .. import db
from .http import get

XML_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

# Map the wild variety of real section headings onto a small, useful vocabulary.
_KIND_PATTERNS = [
    ("abstract", r"^abstract|^summary$"),
    ("intro", r"^introduction|^background"),
    ("methods", r"method|material|experimental|protocol|procedure|statistical analys"),
    ("results", r"^result|^findings"),
    ("discussion", r"^discussion|^conclusion|^interpretation"),
    ("data", r"data availability|code availability|supplementary"),
    ("funding", r"funding|acknowledg|competing interest|conflict of interest|author contribution"),
]


def classify_heading(heading: str) -> str:
    h = (heading or "").strip().lower()
    # Journals number their sections ("2. Results", "3.1 Methods"). Strip the
    # numbering before matching, or every anchored pattern below misses.
    h = re.sub(r"^[\divxlc]+(\.\d+)*[.)]?\s+", "", h)
    for kind, pattern in _KIND_PATTERNS:
        if re.search(pattern, h):
            return kind
    return "other"


def _text_of(el: ET.Element) -> str:
    """Flatten an element to text, dropping tables/figures but keeping captions."""
    parts: list[str] = []
    for node in el.iter():
        if node.tag in ("table", "table-wrap-foot", "xref"):
            continue
        if node.text:
            parts.append(node.text)
        if node.tail:
            parts.append(node.tail)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def fetch_xml(pmcid: str, *, timeout: float = 30, delay: float = 0.34) -> str | None:
    """Fetch JATS XML for an OA article. Returns None if not in the OA subset."""
    if not pmcid:
        return None
    pmcid = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
    r = get(
        f"{XML_BASE}/{pmcid}/fullTextXML",
        timeout=timeout,
        delay=delay,
        accept="application/xml",
    )
    if r is None or not r.text or "<article" not in r.text[:2000]:
        return None
    return r.text


def parse_sections(xml_text: str) -> tuple[list[dict[str, Any]], str]:
    """Split JATS XML into ``[{ord, heading, kind, text}]`` plus a licence string."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], ""

    licence = ""
    for lic in root.iter("license"):
        licence = (lic.get("{http://www.w3.org/1999/xlink}href") or _text_of(lic))[:200]
        break

    sections: list[dict[str, Any]] = []
    order = 0

    for abstract in root.iter("abstract"):
        text = _text_of(abstract)
        if len(text) > 80:
            sections.append({"ord": order, "heading": "Abstract", "kind": "abstract", "text": text})
            order += 1
        break

    body = root.find(".//body")
    if body is not None:
        for sec in body.findall(".//sec"):
            # Skip nested sections whose parent we already captured wholesale.
            title_el = sec.find("title")
            heading = (title_el.text or "").strip() if title_el is not None else ""
            paras = [_text_of(p) for p in sec.findall("./p")]
            text = " ".join(t for t in paras if t)
            if len(text) < 120:
                continue
            sections.append({
                "ord": order,
                "heading": heading or "(untitled)",
                "kind": classify_heading(heading),
                "text": text,
            })
            order += 1

    return sections, licence


def store(con: sqlite3.Connection, paper_id: str, sections: list[dict[str, Any]],
          licence: str, origin: str = "epmc-xml") -> int:
    """Replace stored full text for a paper. Returns character count."""
    n_chars = sum(len(s["text"]) for s in sections)
    con.execute("DELETE FROM sections WHERE paper_id=?", (paper_id,))
    con.executemany(
        "INSERT INTO sections(paper_id, ord, heading, kind, text) VALUES (?,?,?,?,?)",
        [(paper_id, s["ord"], s["heading"], s["kind"], s["text"]) for s in sections],
    )
    con.execute(
        """INSERT INTO fulltext(paper_id, fetched_at, license, n_chars, origin)
           VALUES (?,?,?,?,?)
           ON CONFLICT(paper_id) DO UPDATE SET
             fetched_at=excluded.fetched_at, license=excluded.license,
             n_chars=excluded.n_chars, origin=excluded.origin""",
        (paper_id, db.now(), licence, n_chars, origin),
    )
    return n_chars


def fetch_for_paper(con: sqlite3.Connection, paper_id: str, *, timeout: float = 30,
                    delay: float = 0.34) -> int:
    """Fetch + store OA full text for one paper. Returns chars stored (0 = none)."""
    row = con.execute("SELECT pmcid, is_oa FROM papers WHERE id=?", (paper_id,)).fetchone()
    if row is None or not row["pmcid"]:
        return 0
    xml_text = fetch_xml(row["pmcid"], timeout=timeout, delay=delay)
    if not xml_text:
        return 0
    sections, licence = parse_sections(xml_text)
    if not sections:
        return 0
    n_chars = store(con, paper_id, sections, licence)
    # The paper's existing chunks cover the abstract only; rebuild them so the
    # newly fetched methods are actually retrievable.
    from .. import config as _config
    from .. import embeddings as _embeddings

    _embeddings.rebuild_chunks_for_paper(con, paper_id, _config.load())
    return n_chars


def backfill(con: sqlite3.Connection, *, limit: int = 25, min_score: int = 0,
             timeout: float = 30, delay: float = 0.34) -> tuple[int, int]:
    """Pull full text for the best OA papers that do not have it yet."""
    rows = con.execute(
        """SELECT p.id FROM papers p
           LEFT JOIN fulltext f ON f.paper_id = p.id
           WHERE f.paper_id IS NULL AND p.is_oa=1 AND p.pmcid != '' AND p.pmcid IS NOT NULL
             AND p.score >= ?
           ORDER BY p.score DESC, p.first_seen DESC
           LIMIT ?""",
        (min_score, limit),
    ).fetchall()
    added = chars = 0
    for r in rows:
        n = fetch_for_paper(con, r["id"], timeout=timeout, delay=delay)
        if n:
            added += 1
            chars += n
    con.commit()
    return added, chars
