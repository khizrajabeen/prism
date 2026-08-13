"""Local embeddings and the chunk index.

Optional by design. Keyword search over this literature is genuinely strong —
gene symbols, HLA alleles, cell line names and tool names are exactly the rare
tokens BM25 handles well and dense vectors blur together. So NeoBrain works
fully without embeddings, and adding them upgrades retrieval from keyword-only
to hybrid rather than replacing it.

Backends
--------
``none``                  keyword retrieval only (default)
``sentence-transformers`` runs on your CPU/GPU, no network, ~80MB model
``ollama``                talks to a local Ollama server (e.g. nomic-embed-text)

Vectors are stored L2-normalized as float32 blobs, so cosine similarity is a
dot product and brute force over tens of thousands of chunks stays in the
low milliseconds.
"""

from __future__ import annotations

import array
import math
import sqlite3
import struct
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import config, db

try:  # numpy makes the scan ~20x faster but is not required
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None


# ------------------------------------------------------------------ backends

class EmbeddingBackend:
    name = "none"
    dim = 0

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError


class SentenceTransformersBackend(EmbeddingBackend):
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # type: ignore

        self.name = model_name
        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vecs = self._model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        return [list(map(float, v)) for v in vecs]


class OllamaBackend(EmbeddingBackend):
    def __init__(self, model_name: str, url: str):
        import requests

        self.name = model_name
        self._url = url.rstrip("/")
        self._requests = requests
        probe = self.encode(["dimension probe"])
        self.dim = len(probe[0]) if probe else 0

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            r = self._requests.post(
                f"{self._url}/api/embeddings",
                json={"model": self.name, "prompt": text},
                timeout=120,
            )
            r.raise_for_status()
            vec = r.json().get("embedding") or []
            out.append(_normalize(vec))
        return out


def get_backend(cfg: config.Config | None = None) -> EmbeddingBackend | None:
    cfg = cfg or config.load()
    kind = str(cfg.get("embeddings.backend", "none")).lower()
    model = cfg.get("embeddings.model", "sentence-transformers/all-MiniLM-L6-v2")
    if kind in ("none", "off", ""):
        return None
    if kind in ("sentence-transformers", "st", "local"):
        return SentenceTransformersBackend(model)
    if kind == "ollama":
        return OllamaBackend(model, cfg.get("embeddings.ollama_url", "http://localhost:11434"))
    raise ValueError(f"unknown embeddings.backend: {kind!r}")


# -------------------------------------------------------------- vector utils

def _normalize(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(float(v) * float(v) for v in vec)) or 1.0
    return [float(v) / norm for v in vec]


def pack(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(blob)
    return list(a)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product; both sides are stored normalized."""
    return sum(x * y for x, y in zip(a, b))


# -------------------------------------------------------------- chunking

def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Split on paragraph/sentence boundaries where possible, not mid-word."""
    text = " ".join((text or "").split())
    if len(text) <= size:
        return [text] if text else []

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            for sep in (". ", "; ", ", ", " "):
                cut = window.rfind(sep)
                if cut > size * 0.5:
                    end = start + cut + len(sep)
                    break
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def rebuild_chunks_for_paper(con: sqlite3.Connection, paper_id: str, cfg: config.Config) -> int:
    """(Re)build chunk rows for one paper from its abstract + stored sections."""
    size = int(cfg.get("embeddings.chunk_chars", 1400))
    overlap = int(cfg.get("embeddings.chunk_overlap", 200))

    paper = con.execute(
        "SELECT title, abstract FROM papers WHERE id=?", (paper_id,)
    ).fetchone()
    if paper is None:
        return 0

    con.execute("DELETE FROM chunks WHERE paper_id=? AND doc_kind='paper'", (paper_id,))

    rows: list[tuple] = []
    ord_ = 0
    header = f"{paper['title']}\n\n"

    if paper["abstract"]:
        for piece in chunk_text(paper["abstract"], size, overlap):
            rows.append((paper_id, "paper", None, "Abstract", ord_, header + piece, len(piece)))
            ord_ += 1

    sections = con.execute(
        "SELECT heading, kind, text FROM sections WHERE paper_id=? ORDER BY ord", (paper_id,)
    ).fetchall()
    for sec in sections:
        if sec["kind"] == "abstract":
            continue
        for piece in chunk_text(sec["text"], size, overlap):
            heading = f"{sec['heading']} [{sec['kind']}]"
            rows.append((paper_id, "paper", None, heading, ord_, header + piece, len(piece)))
            ord_ += 1

    con.executemany(
        """INSERT INTO chunks(paper_id, doc_kind, doc_ref, heading, ord, text, n_chars)
           VALUES (?,?,?,?,?,?,?)""",
        rows,
    )
    return len(rows)


def index_knowledge_files(con: sqlite3.Connection, cfg: config.Config) -> int:
    """Chunk the curated knowledge notes so retrieval covers your own writing too."""
    size = int(cfg.get("embeddings.chunk_chars", 1400))
    overlap = int(cfg.get("embeddings.chunk_overlap", 200))
    total = 0

    for path in sorted(config.KNOWLEDGE_DIR.glob("*.md")):
        ref = f"knowledge/{path.name}"
        con.execute("DELETE FROM chunks WHERE doc_ref=?", (ref,))
        text = path.read_text(encoding="utf-8")

        # Split on level-2 headings so each chunk carries its own topic label.
        blocks: list[tuple[str, str]] = []
        current_heading = path.stem.replace("_", " ")
        buf: list[str] = []
        for line in text.splitlines():
            if line.startswith("## "):
                if buf:
                    blocks.append((current_heading, "\n".join(buf)))
                current_heading = line[3:].strip()
                buf = []
            else:
                buf.append(line)
        if buf:
            blocks.append((current_heading, "\n".join(buf)))

        ord_ = 0
        rows = []
        for heading, body in blocks:
            for piece in chunk_text(body, size, overlap):
                rows.append((None, "knowledge", ref, f"{path.stem} — {heading}", ord_,
                             piece, len(piece)))
                ord_ += 1
        con.executemany(
            """INSERT INTO chunks(paper_id, doc_kind, doc_ref, heading, ord, text, n_chars)
               VALUES (?,?,?,?,?,?,?)""",
            rows,
        )
        total += len(rows)

    con.commit()
    return total


# ----------------------------------------------------------------- indexing

def index_new(
    con: sqlite3.Connection,
    cfg: config.Config | None = None,
    *,
    backend: EmbeddingBackend | None = None,
    limit: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> int:
    """Chunk anything unchunked, then embed any chunk without a vector."""
    cfg = cfg or config.load()
    emit = progress or (lambda m: None)

    # 1. chunk papers that have content but no chunks
    todo = con.execute(
        """SELECT p.id FROM papers p
           WHERE (p.abstract IS NOT NULL AND length(p.abstract) > 100
                  OR EXISTS (SELECT 1 FROM sections s WHERE s.paper_id = p.id))
             AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.paper_id = p.id)
           ORDER BY p.score DESC
           LIMIT ?""",
        (limit or 5000,),
    ).fetchall()
    for row in todo:
        rebuild_chunks_for_paper(con, row["id"], cfg)
    con.commit()

    # 2. chunk knowledge notes if they are missing
    have_knowledge = con.execute(
        "SELECT COUNT(*) c FROM chunks WHERE doc_kind='knowledge'"
    ).fetchone()["c"]
    if not have_knowledge:
        index_knowledge_files(con, cfg)

    backend = backend or get_backend(cfg)
    if backend is None:
        return 0

    batch_size = int(cfg.get("embeddings.batch_size", 32))
    pending = con.execute(
        """SELECT c.id, c.text FROM chunks c
           LEFT JOIN embeddings e ON e.chunk_id = c.id
           WHERE e.chunk_id IS NULL
           ORDER BY c.id
           LIMIT ?""",
        (limit or 100000,),
    ).fetchall()

    done = 0
    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        try:
            vecs = backend.encode([r["text"] for r in batch])
        except Exception as e:  # noqa: BLE001
            emit(f"  ! embedding batch failed: {e}")
            break
        con.executemany(
            "INSERT OR REPLACE INTO embeddings(chunk_id, model, dim, vec, created_at)"
            " VALUES (?,?,?,?,?)",
            [
                (r["id"], backend.name, len(v), pack(v), db.now())
                for r, v in zip(batch, vecs)
            ],
        )
        con.commit()
        done += len(batch)
        if done % (batch_size * 10) == 0:
            emit(f"  embedded {done}/{len(pending)}")
    return done


def search_vectors(
    con: sqlite3.Connection,
    query_vec: Sequence[float],
    k: int = 30,
    *,
    doc_kind: str | None = None,
) -> list[tuple[int, float]]:
    """Brute-force cosine scan. Returns [(chunk_id, similarity)] best first."""
    sql = """SELECT e.chunk_id, e.vec FROM embeddings e
             JOIN chunks c ON c.id = e.chunk_id"""
    args: list[Any] = []
    if doc_kind:
        sql += " WHERE c.doc_kind = ?"
        args.append(doc_kind)
    rows = con.execute(sql, args).fetchall()
    if not rows:
        return []

    if _np is not None:
        mat = _np.frombuffer(b"".join(r["vec"] for r in rows), dtype=_np.float32)
        dim = len(query_vec)
        mat = mat.reshape(-1, dim)
        sims = mat @ _np.asarray(query_vec, dtype=_np.float32)
        order = _np.argsort(-sims)[:k]
        return [(rows[int(i)]["chunk_id"], float(sims[int(i)])) for i in order]

    scored = [(r["chunk_id"], cosine(unpack(r["vec"]), query_vec)) for r in rows]
    scored.sort(key=lambda t: -t[1])
    return scored[:k]
