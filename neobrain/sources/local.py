"""Local document ingestion — the legal route to paywalled papers.

Open the PDF through your institutional proxy in a browser, save it into
``inbox/``, and this pulls it into the same tables as everything else so it is
searchable and citable alongside the OA corpus.

Text extraction tries, in order: pypdf (pure python, no system deps), then
``pdftotext`` from poppler if it happens to be installed. Both are optional;
without either, ingestion tells you what to install rather than failing
silently.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .. import db
from . import fulltext


# Below this, extraction has almost certainly failed (a scan, a cover page, or
# a PDF whose text layer is images) rather than found a short paper.
MIN_CHARS = 200


def _extract_pypdf(path: Path) -> str | None:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return None
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return None


def _extract_poppler(path: Path) -> str | None:
    if not shutil.which("pdftotext"):
        return None
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True, text=True, timeout=120,
        )
        return out.stdout or None
    except Exception:
        return None


def extract_text(path: Path) -> str | None:
    if path.suffix.lower() in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    return _extract_pypdf(path) or _extract_poppler(path)


# Headings in a PDF-extracted text stream: a short line, mostly title case or
# caps, that matches one of the canonical section names.
_HEADING = re.compile(
    r"^\s*(?:\d+\.?\s*)?(abstract|introduction|background|results?|"
    r"materials and methods|methods?|experimental procedures|discussion|"
    r"conclusions?|references|acknowledg\w*|supplementary\s+\w*)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _whole_document(text: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    return [{"ord": 0, "heading": "(full text)", "kind": "other", "text": cleaned}]


def split_sections(text: str) -> list[dict[str, Any]]:
    """Best-effort sectioning of extracted document text.

    Falls back to a single whole-document block whenever sectioning would lose
    content — no headings found, or every candidate section too short to keep.
    Losing a short paper silently is far worse than storing it unsectioned.
    """
    marks = [(m.start(), m.group(1).strip()) for m in _HEADING.finditer(text)]
    if not marks:
        return _whole_document(text)

    sections = []
    for i, (start, heading) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = re.sub(r"\s+", " ", text[start:end]).strip()
        if len(body) < 120:
            continue
        if heading.lower().startswith("reference"):
            continue  # bibliography is noise for retrieval
        sections.append({
            "ord": len(sections),
            "heading": heading.title(),
            "kind": fulltext.classify_heading(heading),
            "text": body,
        })

    return sections or _whole_document(text)


def _guess_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if 20 < len(line) < 250 and not line.lower().startswith(("doi", "http", "www")):
            return line
    return fallback


def _find_doi(text: str) -> str:
    m = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b", text[:6000])
    return m.group(0).rstrip(".,;)") if m else ""


def _index(con: sqlite3.Connection, paper_id: str) -> int:
    """Chunk a paper for retrieval. Imported lazily to keep this module light."""
    from .. import config, embeddings

    return embeddings.rebuild_chunks_for_paper(con, paper_id, config.load())


def ingest_pdf(con: sqlite3.Connection, path: Path, *, title: str | None = None,
               tag: str = "local") -> dict[str, Any]:
    """Ingest one local document. Returns a small report dict."""
    text = extract_text(path)
    if not text or not text.strip():
        return {
            "ok": False,
            "path": str(path),
            "error": "no extractable text — install pypdf (`pip install pypdf`) "
                     "or poppler-utils, or the PDF is a scan needing OCR (try ocrmypdf)",
        }
    if len(text.strip()) < MIN_CHARS:
        return {
            "ok": False,
            "path": str(path),
            "error": f"only {len(text.strip())} characters extracted (minimum "
                     f"{MIN_CHARS}) — likely a cover page, a scan, or a failed "
                     f"extraction rather than a paper",
        }

    doi = _find_doi(text)
    digest = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    paper_id = f"DOI:{doi}" if doi else f"LOCAL:{digest}"

    sections = split_sections(text)
    abstract = next((s["text"] for s in sections if s["kind"] == "abstract"), "")
    if not abstract:
        abstract = re.sub(r"\s+", " ", text[:1500]).strip()

    row = {
        "id": paper_id,
        "source": "local",
        "provider": "local",
        "title": title or _guess_title(text, path.stem),
        "abstract": abstract[:6000],
        "authors": "",
        "journal": tag,
        "pub_date": "",
        "doi": doi,
        "pmid": "",
        "pmcid": "",
        "url": path.resolve().as_uri(),
        "is_oa": 0,
        "score": 0,
        "matched": "",
        "buckets": "local",
    }
    is_new = db.upsert_paper(con, row)
    n_chars = fulltext.store(con, paper_id, sections, licence="local file", origin="pdf")
    # Index it now. A paper you ingested but cannot find is worse than one you
    # never ingested, because you will assume it is in there.
    n_chunks = _index(con, paper_id)
    con.commit()
    return {
        "ok": True,
        "id": paper_id,
        "new": is_new,
        "title": row["title"],
        "sections": len(sections),
        "chars": n_chars,
        "chunks": n_chunks,
        "path": str(path),
    }


def ingest_dir(con: sqlite3.Connection, directory: Path) -> list[dict[str, Any]]:
    reports = []
    for path in sorted(directory.glob("**/*")):
        if path.suffix.lower() in (".pdf", ".txt", ".md") and path.is_file():
            reports.append(ingest_pdf(con, path))
    return reports
