"""Hybrid retrieval: BM25 keyword search fused with vector similarity.

Why fusion rather than picking one: in this literature the two modes fail in
opposite directions. Keyword search nails `HLA-A*02:01`, `NetMHCIIpan-4.3`,
`Adpgk`, `NSG-SGM3` — the rare tokens that carry the meaning — and misses
paraphrase. Vector search finds "peptide presentation on class II" when you
asked about "CD4 epitope display" and blurs the alleles. Reciprocal-rank
fusion takes the union without needing the two score scales to be comparable,
which they are not.

The output of ``context_pack`` is meant to be pasted (or handed by the MCP
server) straight into a model's context: every passage carries its paper id,
title, and link, so the answer above it can be checked line by line.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import config, db, embeddings


@dataclass
class Hit:
    chunk_id: int | None
    paper_id: str | None
    doc_kind: str
    doc_ref: str | None
    heading: str | None
    text: str
    title: str | None = None
    url: str | None = None
    journal: str | None = None
    pub_date: str | None = None
    score: float = 0.0
    how: list[str] = field(default_factory=list)

    def citation(self) -> str:
        if self.doc_kind == "knowledge":
            return f"{self.doc_ref} § {self.heading or ''}".strip(" §")
        bits = [b for b in (self.title, self.journal, self.pub_date) if b]
        return " · ".join(bits) + (f" · {self.paper_id}" if self.paper_id else "")


# --------------------------------------------------------------- keyword leg

def keyword_chunks(con: sqlite3.Connection, query: str, k: int = 30,
                   doc_kind: str | None = None) -> list[tuple[int, float]]:
    match = db.fts_escape(query)
    sql = """SELECT c.id AS chunk_id, bm25(chunks_fts) AS rank
             FROM chunks_fts
             JOIN chunks c ON c.id = chunks_fts.rowid
             WHERE chunks_fts MATCH ?"""
    args: list[Any] = [match]
    if doc_kind:
        sql += " AND c.doc_kind = ?"
        args.append(doc_kind)
    sql += " ORDER BY rank LIMIT ?"
    args.append(k)
    try:
        rows = con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []
    # bm25 returns lower-is-better; invert so bigger is better for reporting.
    return [(r["chunk_id"], -float(r["rank"])) for r in rows]


def keyword_papers(con: sqlite3.Connection, query: str, k: int = 20) -> list[dict]:
    """Search titles/abstracts directly — used when you want papers, not passages."""
    match = db.fts_escape(query)
    try:
        rows = con.execute(
            """SELECT p.*, bm25(papers_fts) AS rank
               FROM papers_fts
               JOIN papers p ON p.rowid = papers_fts.rowid
               WHERE papers_fts MATCH ?
               ORDER BY rank LIMIT ?""",
            (match, k),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


# ------------------------------------------------------------------- fusion

# Question words and connectives carry no retrieval signal but match everything
# in a LIKE query, which is how "why is X important?" ends up returning every
# trial in the database.
_STOPWORDS = {
    "what", "which", "when", "where", "why", "how", "does", "do", "did", "is",
    "are", "was", "were", "the", "a", "an", "and", "or", "for", "of", "in", "on",
    "to", "with", "from", "that", "this", "these", "those", "it", "its", "be",
    "can", "could", "should", "would", "about", "than", "then", "there", "any",
    "all", "more", "most", "much", "many", "study", "studies", "paper", "papers",
}


def keywords(query: str, limit: int = 4) -> list[str]:
    """The content-bearing terms of a question, longest first."""
    tokens = [
        t.strip(".,;:!?()[]\"'").lower()
        for t in query.split()
    ]
    kept = [t for t in tokens if len(t) > 3 and t not in _STOPWORDS]
    return sorted(set(kept), key=len, reverse=True)[:limit]


def matching_trials(con: sqlite3.Connection, query: str, limit: int = 8) -> list[sqlite3.Row]:
    """Trials whose title, condition, or intervention mentions a query keyword."""
    terms = keywords(query)
    if not terms:
        return []
    clauses, args = [], []
    for term in terms:
        clauses.append("(t.title LIKE ? OR t.conditions LIKE ? OR t.interventions LIKE ?)")
        args += [f"%{term}%"] * 3
    args.append(limit)
    return con.execute(
        f"""SELECT t.nct_id, t.title, t.status, t.phase, t.sponsor, t.url
            FROM trials t WHERE {' OR '.join(clauses)}
            ORDER BY t.last_update DESC LIMIT ?""",
        args,
    ).fetchall()


def _rrf(rankings: list[list[tuple[int, float]]], weights: list[float], rrf_k: int = 60
         ) -> dict[int, float]:
    """Reciprocal rank fusion. Scale-free: only the ordering of each list matters."""
    fused: dict[int, float] = {}
    for ranking, weight in zip(rankings, weights):
        for rank, (item_id, _score) in enumerate(ranking, start=1):
            fused[item_id] = fused.get(item_id, 0.0) + weight / (rrf_k + rank)
    return fused


def search(
    query: str,
    *,
    k: int | None = None,
    doc_kind: str | None = None,
    cfg: config.Config | None = None,
    con: sqlite3.Connection | None = None,
    use_vectors: bool = True,
) -> list[Hit]:
    """Hybrid search over chunks. Falls back to keyword-only without embeddings."""
    cfg = cfg or config.load()
    close_after = con is None
    con = con or db.connect()
    k = k or int(cfg.get("retrieval.k", 12))
    pool = max(k * 4, 40)

    kw = keyword_chunks(con, query, pool, doc_kind)
    rankings = [kw]
    weights = [float(cfg.get("retrieval.keyword_weight", 1.0))]
    modes = {cid: ["keyword"] for cid, _ in kw}

    if use_vectors and cfg.get("embeddings.backend", "none") != "none":
        try:
            backend = embeddings.get_backend(cfg)
            if backend is not None:
                qvec = backend.encode([query])[0]
                vec_hits = embeddings.search_vectors(con, qvec, pool, doc_kind=doc_kind)
                rankings.append(vec_hits)
                weights.append(float(cfg.get("retrieval.vector_weight", 1.0)))
                for cid, _ in vec_hits:
                    modes.setdefault(cid, []).append("vector")
        except Exception:
            # An unavailable embedding model must degrade to keyword search,
            # not break retrieval entirely.
            pass

    fused = _rrf(rankings, weights, int(cfg.get("retrieval.rrf_k", 60)))
    if not fused:
        if close_after:
            con.close()
        return []

    top_ids = sorted(fused, key=lambda cid: -fused[cid])[:k]
    placeholders = ",".join("?" * len(top_ids))
    rows = con.execute(
        f"""SELECT c.id, c.paper_id, c.doc_kind, c.doc_ref, c.heading, c.text,
                   p.title, p.url, p.journal, p.pub_date
            FROM chunks c LEFT JOIN papers p ON p.id = c.paper_id
            WHERE c.id IN ({placeholders})""",
        top_ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}

    hits = []
    for cid in top_ids:
        r = by_id.get(cid)
        if r is None:
            continue
        hits.append(Hit(
            chunk_id=cid, paper_id=r["paper_id"], doc_kind=r["doc_kind"],
            doc_ref=r["doc_ref"], heading=r["heading"], text=r["text"],
            title=r["title"], url=r["url"], journal=r["journal"],
            pub_date=r["pub_date"], score=round(fused[cid], 5),
            how=modes.get(cid, []),
        ))

    if close_after:
        con.close()
    return hits


def context_pack(
    query: str,
    *,
    k: int | None = None,
    max_chars: int = 12000,
    cfg: config.Config | None = None,
    con: sqlite3.Connection | None = None,
    include_trials: bool = True,
) -> str:
    """Build a citable evidence block for a question.

    This is what the agent should ground an answer on: passages with sources
    attached, plus the relevant trials, plus an explicit statement of what the
    corpus does *not* contain — so "no evidence found" is distinguishable from
    "did not look".
    """
    cfg = cfg or config.load()
    close_after = con is None
    con = con or db.connect()

    hits = search(query, k=k or int(cfg.get("retrieval.k", 12)), cfg=cfg, con=con)
    parts: list[str] = [f"# Evidence for: {query}", ""]

    if not hits:
        parts += [
            "**Nothing in the local corpus matches this query.**",
            "",
            "That means one of: the sweep has not covered this topic (check "
            "`config/interests.yaml`), the corpus is too young, or the phrasing "
            "does not match the literature's vocabulary. Do not answer from "
            "memory alone — say the corpus is empty on this and offer to widen "
            "the sweep.",
            "",
        ]
    else:
        used = 0
        for i, h in enumerate(hits, 1):
            block = [
                f"## [{i}] {h.title or h.doc_ref}",
                f"source: {h.citation()}" + (f" · {h.url}" if h.url else ""),
                f"section: {h.heading or 'n/a'} · retrieved via {'+'.join(h.how)}",
                "",
                h.text.strip(),
                "",
            ]
            text = "\n".join(block)
            if used + len(text) > max_chars:
                break
            parts.append(text)
            used += len(text)

    if include_trials:
        trial_rows = matching_trials(con, query)
        if trial_rows:
            parts += ["## Related trials in the local corpus", ""]
            for t in trial_rows:
                parts.append(
                    f"- {t['nct_id']} ({t['status']}, {t['phase'] or 'n/a'}) — "
                    f"{t['title']} · {t['sponsor']} · {t['url']}"
                )
            parts.append("")

    parts += [
        "---",
        "Ground the answer in the passages above. Cite by paper id or `[n]`. "
        "If the passages do not settle the question, say so explicitly and name "
        "what evidence would.",
    ]

    if close_after:
        con.close()
    return "\n".join(parts)
