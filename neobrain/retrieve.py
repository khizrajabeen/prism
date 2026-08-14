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

import re
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
    evidence_tier: int | None = None
    study_type: str | None = None

    def citation(self) -> str:
        if self.doc_kind == "knowledge":
            return f"{self.doc_ref} § {self.heading or ''}".strip(" §")
        bits = [b for b in (self.title, self.journal, self.pub_date) if b]
        cite = " · ".join(bits) + (f" · {self.paper_id}" if self.paper_id else "")
        if self.study_type:
            cite += f" · {self.study_type} (tier {self.evidence_tier})"
        return cite

    def snippet(self, query: str = "", width: int = 420) -> str:
        """The part of the passage that actually matched.

        Chunks run to ~1400 characters. Showing the first 400 of a long chunk
        routinely displays a table header while the sentence that answered the
        question sits below the fold, which makes correct retrieval look wrong.
        This centres the window on the densest cluster of query terms.
        """
        text = " ".join(self.text.split())
        if len(text) <= width or not query:
            return text[:width]

        terms = [t for t in re.findall(r"[a-z0-9]{4,}", query.lower())]
        if not terms:
            return text[:width]

        low = text.lower()
        positions = [low.find(t) for t in terms if low.find(t) >= 0]
        if not positions:
            return text[:width]

        # Densest cluster: the position with the most other hits nearby.
        best = max(positions, key=lambda p: sum(1 for q in positions if abs(q - p) < width))
        start = max(0, best - width // 3)
        # Do not cut mid-word at the left edge.
        if start > 0:
            space = text.find(" ", start)
            start = space + 1 if 0 <= space < start + 30 else start
        out = text[start : start + width]
        return ("…" if start > 0 else "") + out + ("…" if start + width < len(text) else "")


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


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", (text or "").lower())}


def _mmr(hits: list[Hit], k: int, lambda_: float = 0.72) -> list[Hit]:
    """Maximal marginal relevance: trade a little relevance for coverage.

    Without this, the top ten passages are routinely ten near-copies of the
    same paragraph from the same paper — technically the best matches, and
    useless as evidence, because they collapse to a single source. MMR keeps
    the best hit and then prefers hits that add something new.
    """
    if len(hits) <= 2:
        return hits[:k]

    pools = {id(h): _tokens(h.text) for h in hits}
    selected: list[Hit] = [hits[0]]
    remaining = hits[1:]

    while remaining and len(selected) < k:
        best, best_score = None, -1e9
        for h in remaining:
            overlap = 0.0
            for s in selected:
                a, b = pools[id(h)], pools[id(s)]
                if a and b:
                    overlap = max(overlap, len(a & b) / len(a | b))
            # Two passages from the same paper are near-duplicates for the
            # purpose of "how many independent sources support this".
            if any(h.paper_id and h.paper_id == s.paper_id for s in selected):
                overlap = max(overlap, 0.6)
            score = lambda_ * h.score - (1 - lambda_) * overlap
            if score > best_score:
                best, best_score = h, score
        selected.append(best)  # type: ignore[arg-type]
        remaining.remove(best)  # type: ignore[arg-type]

    return selected


def search(
    query: str,
    *,
    k: int | None = None,
    doc_kind: str | None = None,
    cfg: config.Config | None = None,
    con: sqlite3.Connection | None = None,
    use_vectors: bool = True,
    use_graph: bool = True,
    diversify: bool = True,
) -> list[Hit]:
    """Hybrid search: keyword + vectors + entity graph, fused by rank.

    Three legs, because they fail in different directions:

    * **keyword** nails the rare tokens that carry meaning here — `HLA-A*02:01`,
      `NetMHCIIpan`, `Adpgk` — and misses paraphrase.
    * **vectors** find "CD4 epitope display" when you asked about "class II
      presentation", and blur the alleles together.
    * **graph** finds passages connected to the question through the
      literature's own entity structure rather than through wording, which is
      the only one of the three that can answer a multi-hop question.

    All three degrade independently: no embeddings and no graph still leaves a
    working keyword search.
    """
    cfg = cfg or config.load()
    close_after = con is None
    con = con or db.connect()
    k = k or int(cfg.get("retrieval.k", 12))
    pool = max(k * 4, 40)

    kw = keyword_chunks(con, query, pool, doc_kind)
    rankings = [kw]
    weights = [float(cfg.get("retrieval.keyword_weight", 1.0))]
    modes: dict[int, list[str]] = {cid: ["keyword"] for cid, _ in kw}

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

    if use_graph:
        try:
            from . import graph as graph_mod

            graph_hits = graph_mod.rank_chunks(con, query, pool, doc_kind=doc_kind)
            if graph_hits:
                rankings.append(graph_hits)
                weights.append(float(cfg.get("retrieval.graph_weight", 0.8)))
                for cid, _ in graph_hits:
                    modes.setdefault(cid, []).append("graph")
        except Exception:
            pass

    fused = _rrf(rankings, weights, int(cfg.get("retrieval.rrf_k", 60)))
    if not fused:
        if close_after:
            con.close()
        return []

    # Take a wider slice than requested, then let MMR pick the final k so the
    # diversity step has something to choose between.
    candidate_ids = sorted(fused, key=lambda cid: -fused[cid])[: max(k * 3, 24)]
    placeholders = ",".join("?" * len(candidate_ids))
    rows = con.execute(
        f"""SELECT c.id, c.paper_id, c.doc_kind, c.doc_ref, c.heading, c.text,
                   p.title, p.url, p.journal, p.pub_date, p.evidence_tier, p.study_type
            FROM chunks c LEFT JOIN papers p ON p.id = c.paper_id
            WHERE c.id IN ({placeholders})""",
        candidate_ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}

    hits = []
    for cid in candidate_ids:
        r = by_id.get(cid)
        if r is None:
            continue
        hits.append(Hit(
            chunk_id=cid, paper_id=r["paper_id"], doc_kind=r["doc_kind"],
            doc_ref=r["doc_ref"], heading=r["heading"], text=r["text"],
            title=r["title"], url=r["url"], journal=r["journal"],
            pub_date=r["pub_date"], score=round(fused[cid], 5),
            how=modes.get(cid, []),
            evidence_tier=r["evidence_tier"], study_type=r["study_type"],
        ))

    hits = _mmr(hits, k) if diversify else hits[:k]

    if close_after:
        con.close()
    return hits


def subquestions(query: str, con: sqlite3.Connection | None = None) -> list[str]:
    """Decompose a question into retrieval-friendly sub-queries.

    Deliberately mechanical rather than model-driven: split on explicit
    conjunctions, and add one entity-focused query per named entity. A question
    like "does class II inclusion improve MC38 responses and does it change
    escape?" retrieves badly as one string, because no passage discusses all of
    it — but retrieves well as three.
    """
    parts = [p.strip(" ?.,") for p in re.split(r"\band\b|\bor\b|;|\?", query) if len(p.strip()) > 12]
    out = [query] + [p for p in parts if p.lower() != query.lower().strip(" ?.,")]

    if con is not None:
        try:
            from . import graph as graph_mod

            for kind, name in graph_mod.extract(query)[:3]:
                out.append(name)
        except Exception:
            pass

    seen, unique = set(), []
    for q in out:
        key = q.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(q)
    return unique[:5]


def multi_search(
    query: str,
    *,
    k: int = 12,
    cfg: config.Config | None = None,
    con: sqlite3.Connection | None = None,
) -> list[Hit]:
    """Decomposed retrieval: run the sub-questions and fuse their results.

    This is the multi-hop path. Each sub-query retrieves independently and the
    results are fused by rank, so a passage that answers one hop well is not
    buried by passages that partially match the whole question.
    """
    cfg = cfg or config.load()
    close_after = con is None
    con = con or db.connect()

    queries = subquestions(query, con)
    per_query: list[list[tuple[int, float]]] = []
    lookup: dict[int, Hit] = {}

    for i, q in enumerate(queries):
        hits = search(q, k=max(k, 10), cfg=cfg, con=con, diversify=False)
        ranked = []
        for h in hits:
            if h.chunk_id is None:
                continue
            ranked.append((h.chunk_id, h.score))
            existing = lookup.get(h.chunk_id)
            if existing is None:
                lookup[h.chunk_id] = h
            else:
                for mode in h.how:
                    if mode not in existing.how:
                        existing.how.append(mode)
        per_query.append(ranked)

    # The original question carries more weight than its derived parts.
    weights = [1.0] + [0.6] * (len(per_query) - 1)
    fused = _rrf(per_query, weights, int(cfg.get("retrieval.rrf_k", 60)))

    ordered = []
    for cid in sorted(fused, key=lambda c: -fused[c]):
        hit = lookup.get(cid)
        if hit is None:
            continue
        hit.score = round(fused[cid], 5)
        ordered.append(hit)

    result = _mmr(ordered, k)
    if close_after:
        con.close()
    return result


def context_pack(
    query: str,
    *,
    k: int | None = None,
    max_chars: int = 12000,
    cfg: config.Config | None = None,
    con: sqlite3.Connection | None = None,
    include_trials: bool = True,
    decompose: bool = True,
    record: bool = True,
) -> str:
    """Build a citable evidence block for a question.

    What the agent grounds an answer on: passages with sources and evidence
    tiers attached, the beliefs already held on the topic, the relevant trials,
    a read on how strong the evidence collectively is, and an explicit
    statement of what the corpus does *not* contain — so "no evidence found" is
    distinguishable from "did not look".

    The retrieval is recorded in `answers`, so months later you can ask why the
    assistant said what it said and get the actual evidence set back.
    """
    cfg = cfg or config.load()
    close_after = con is None
    con = con or db.connect()
    k = k or int(cfg.get("retrieval.k", 12))

    hits = (multi_search(query, k=k, cfg=cfg, con=con) if decompose
            else search(query, k=k, cfg=cfg, con=con))
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
        from . import evidence as evidence_mod

        tiers = [h.evidence_tier for h in hits if h.evidence_tier is not None]
        sources = {h.paper_id for h in hits if h.paper_id}
        parts += [
            f"**Evidence strength:** {evidence_mod.consensus_language(tiers, len(sources))}. "
            f"{len(hits)} passages from {len(sources) or 'the knowledge notes'} "
            f"{'distinct sources' if sources else ''}.",
            "",
        ]

        used = 0
        for i, h in enumerate(hits, 1):
            tier = f" · evidence tier {h.evidence_tier}/5" if h.evidence_tier is not None else ""
            block = [
                f"## [{i}] {h.title or h.doc_ref}",
                f"source: {h.citation()}" + (f" · {h.url}" if h.url else ""),
                f"section: {h.heading or 'n/a'} · retrieved via {'+'.join(h.how)}{tier}",
                "",
                h.text.strip(),
                "",
            ]
            text = "\n".join(block)
            if used + len(text) > max_chars:
                break
            parts.append(text)
            used += len(text)

    # What we already believe about this — so the agent can notice when the
    # retrieved evidence disagrees with the stored position instead of
    # cheerfully asserting both.
    beliefs = con.execute(
        """SELECT b.id, b.claim, b.confidence FROM beliefs b
           WHERE b.status='active' AND (b.claim LIKE ? OR b.topic LIKE ?) LIMIT 6""",
        (f"%{keywords(query)[0] if keywords(query) else query}%",
         f"%{keywords(query)[0] if keywords(query) else query}%"),
    ).fetchall()
    if beliefs:
        parts += ["## What this brain already believes on this topic", ""]
        for b in beliefs:
            parts.append(f"- `#{b['id']}` [{b['confidence']}] {b['claim']}")
        parts += ["", "If the passages above contradict any of these, say so "
                      "explicitly and propose a revision — do not assert both.", ""]

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

    if record and hits:
        con.execute(
            "INSERT INTO answers(asked_at, question, chunk_ids, paper_ids) VALUES (?,?,?,?)",
            (db.now(), query,
             ",".join(str(h.chunk_id) for h in hits if h.chunk_id),
             ",".join(sorted({h.paper_id for h in hits if h.paper_id}))),
        )
        con.commit()

    parts += [
        "---",
        "Ground the answer in the passages above. Cite by paper id or `[n]`, and "
        "carry the evidence tier into how you phrase the claim: tier 4–5 may be "
        "stated as established, tier 1–2 must be marked provisional. If the "
        "passages do not settle the question, say so explicitly and name what "
        "evidence would.",
    ]

    if close_after:
        con.close()
    return "\n".join(parts)
