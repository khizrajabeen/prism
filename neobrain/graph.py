"""Entity graph and graph-seeded retrieval.

The problem this solves
-----------------------
Ask "why did the CT26 vaccine result not replicate?" and neither keyword nor
vector search helps much. The answer lives in a chain: CT26 → gp70/AH1 →
immunodominance → masked neoantigen response. No single passage contains that
chain, and the words in the question appear in none of the useful passages.

This is the multi-hop retrieval problem, and the 2026 answer to it — HippoRAG,
GraphRAG and successors — is to build an entity graph over the corpus, seed it
with the entities in the question, spread activation across the graph, and
return the passages that the *graph* says are relevant rather than the ones
that merely share wording.

Why this implementation is deterministic
----------------------------------------
Most graph-RAG systems extract entities and relations with an LLM. That works,
and it also hallucinates edges — which in a research assistant means inventing
a relationship between a gene and a phenotype that no paper asserts. Here
extraction is a curated domain gazetteer plus regex over things with strict
formats (HLA alleles, mutation notation, cell lines, tool names). It gets
recall on the entities that matter in this field, and it cannot fabricate.

Edges are co-occurrence within a passage, weighted. That is a weaker relation
than "X inhibits Y", and it is honest about being one: the graph tells you
these things are discussed together, and the passage tells you how.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any, Iterable

from . import db

# --------------------------------------------------------------- gazetteer

# Things with strict formats — matched by pattern, so new ones are found
# automatically without anyone maintaining a list.
PATTERNS: list[tuple[str, re.Pattern]] = [
    # HLA-A*02:01, HLA-DRB1*15:01, HLA-C*08:02
    ("hla", re.compile(r"\bHLA-[A-Z]{1,4}\d?\*\d{2,3}:\d{2,3}\b")),
    # Serological / low-resolution forms that still carry meaning: HLA-A2, HLA-DR4
    ("hla", re.compile(r"\bHLA-(?:[ABC]|D[PQR][AB]?1?)\d{1,2}\b")),
    # Protein-level mutations: KRAS G12D, TP53 R175H, BRAF V600E
    ("mutation", re.compile(r"\b[A-Z][A-Z0-9]{1,7}\s+[A-Z]\d{1,4}[A-Z*]\b")),
    ("mutation", re.compile(r"\bp\.[A-Z][a-z]{2}\d+[A-Z][a-z]{2}\b")),
]

# Curated terms. Kept explicit because these are the concepts whose
# co-occurrence structure is the point — an automatic noun-phrase extractor
# would bury them in generic vocabulary.
GAZETTEER: dict[str, list[str]] = {
    "cell_line": [
        "MC38", "B16F10", "B16-OVA", "CT26", "4T1", "LLC1", "Lewis lung", "Panc02",
        "KPC", "EO771", "AT-3", "T2 cells", "RMA-S", "MCA sarcoma",
    ],
    "mouse": [
        "C57BL/6", "BALB/c", "NSG", "NSG-SGM3", "NOG-EXL", "MISTRG", "HHD",
        "HLA transgenic", "humanized mouse", "GEMM", "PDX", "OT-I", "OT-II",
        "BATF3", "nude mouse",
    ],
    "tool": [
        "NetMHCpan", "NetMHCIIpan", "MHCflurry", "BigMHC", "TransPHLA", "MixMHCpred",
        "PRIME", "DeepImmuno", "pVACtools", "pVACseq", "pVACbind", "pVACfuse",
        "pVACvector", "NeoPredPipe", "MuPeXI", "antigen.garnish", "vaxrank",
        "Neoepiscope", "NeoFuse", "OptiType", "arcasHLA", "HLA-HD", "Polysolver",
        "LOHHLA", "Mutect2", "Strelka2", "VarScan2", "VEP", "SnpEff", "STAR-Fusion",
        "Arriba", "salmon", "kallisto", "AlphaFold", "scanpy", "Seurat", "scirpy",
        "SpliceAI", "WhatsHap", "FACETS", "ASCAT", "PureCN",
    ],
    "assay": [
        "ELISpot", "ICS", "intracellular cytokine staining", "tetramer", "multimer",
        "AIM assay", "immunopeptidomics", "HLA immunoprecipitation", "LC-MS/MS",
        "mass spectrometry", "TCR sequencing", "scRNA-seq", "single-cell RNA-seq",
        "flow cytometry", "xCELLigence", "LDH release", "chromium release",
        "MHC stabilization", "WES", "whole exome sequencing", "RNA-seq", "Ribo-seq",
        "spatial transcriptomics", "ctDNA",
    ],
    "gene": [
        "KRAS", "TP53", "B2M", "JAK1", "JAK2", "TAP1", "TAP2", "ERAP1", "ERAP2",
        "HLA-A", "HLA-B", "HLA-C", "HLA-DRB1", "HLA-DQB1", "HLA-DPB1", "CD8", "CD4",
        "PD-1", "PD-L1", "CTLA-4", "LAG-3", "TIM-3", "TIGIT", "IFN-γ", "IFNG",
        "TNF-α", "IL-2", "IL-15", "TOX", "TCF1", "BRAF", "PTEN", "SF3B1", "U2AF1",
        "Adpgk", "Reps1", "Dpagt1", "gp70", "AH1", "gp100", "TRP2", "OVA", "SIINFEKL",
        "CD137", "4-1BB", "OX40", "CD40", "XCR1", "CLEC9A", "IDO1", "CD39", "CD73",
    ],
    "concept": [
        "neoantigen", "neoepitope", "cross-presentation", "immunopeptidome",
        "agretopicity", "differential agretopicity", "clonality", "cancer cell fraction",
        "tumor mutational burden", "microsatellite instability", "frameshift",
        "gene fusion", "splice variant", "endogenous retroelement", "immunoediting",
        "HLA loss of heterozygosity", "antigen presentation", "central tolerance",
        "peripheral tolerance", "T cell exhaustion", "immune exclusion",
        "tumor microenvironment", "checkpoint blockade", "adjuvant", "poly-ICLC",
        "montanide", "CpG", "STING agonist", "lipid nanoparticle", "mRNA vaccine",
        "synthetic long peptide", "dendritic cell vaccine", "viral vector",
        "junctional neoepitope", "prophylactic", "therapeutic", "orthotopic",
        "minimal residual disease", "TESLA", "polyfunctionality", "immunodominance",
        "proteasome", "immunoproteasome", "TAP transport", "peptide-MHC stability",
    ],
}

# Aliases. Without these the graph fragments: a paper writing "HLA LOH" and one
# writing "HLA loss of heterozygosity" would become two unconnected nodes, and
# the path between them — which is the whole point — would not exist. Every
# alias resolves to the canonical name on the right.
ALIASES: dict[str, str] = {
    "HLA LOH": "HLA loss of heterozygosity",
    "LOH at the HLA locus": "HLA loss of heterozygosity",
    "loss of heterozygosity": "HLA loss of heterozygosity",
    "β2m": "B2M",
    "beta-2 microglobulin": "B2M",
    "β2-microglobulin": "B2M",
    "beta2-microglobulin": "B2M",
    "TMB": "tumor mutational burden",
    "tumour mutational burden": "tumor mutational burden",
    "MSI": "microsatellite instability",
    "MSI-H": "microsatellite instability",
    "MSI-high": "microsatellite instability",
    "intracellular cytokine staining": "ICS",
    "AIM": "AIM assay",
    "activation-induced marker": "AIM assay",
    "SLP": "synthetic long peptide",
    "synthetic long peptides": "synthetic long peptide",
    "LNP": "lipid nanoparticle",
    "lipid nanoparticles": "lipid nanoparticle",
    "DAI": "differential agretopicity",
    "differential agretopicity index": "differential agretopicity",
    "agretopicity index": "differential agretopicity",
    "CCF": "cancer cell fraction",
    "MRD": "minimal residual disease",
    "TME": "tumor microenvironment",
    "tumour microenvironment": "tumor microenvironment",
    "cross presentation": "cross-presentation",
    "immunodominant": "immunodominance",
    "immunodominant response": "immunodominance",
    "checkpoint inhibitor": "checkpoint blockade",
    "immune checkpoint blockade": "checkpoint blockade",
    "ICB": "checkpoint blockade",
    "poly-IC": "poly-ICLC",
    "polyICLC": "poly-ICLC",
    "Hiltonol": "poly-ICLC",
    "exhaustion": "T cell exhaustion",
    "whole-exome sequencing": "whole exome sequencing",
    "WGS": "whole exome sequencing",
    "PDAC": "KPC",
    "tumour mutational load": "tumor mutational burden",
    "neo-antigen": "neoantigen",
    "neo-epitope": "neoepitope",
    "MHC-I": "antigen presentation",
    "class I": "antigen presentation",
    "TIL": "tumor infiltrating lymphocyte",
    "tumour infiltrating lymphocyte": "tumor infiltrating lymphocyte",
    "tumor-infiltrating lymphocyte": "tumor infiltrating lymphocyte",
}

# The kind each canonical term belongs to, so aliases inherit it.
_KIND_OF: dict[str, str] = {
    term: kind for kind, terms in GAZETTEER.items() for term in terms
}
_KIND_OF.setdefault("tumor infiltrating lymphocyte", "concept")

# Precompiled word-boundary matchers for the gazetteer and its aliases.
_GAZ_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    (kind, term, re.compile(rf"(?<![\w-]){re.escape(term)}(?![\w-])", re.IGNORECASE))
    for kind, terms in GAZETTEER.items()
    for term in terms
] + [
    (_KIND_OF.get(canonical, "concept"), canonical,
     re.compile(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", re.IGNORECASE))
    for alias, canonical in ALIASES.items()
]

# Sequences that look like "GENE V600E" but are not mutations.
_MUTATION_STOPWORDS = {"FIGURE", "TABLE", "PANEL", "SUPPLEMENTARY", "SECTION", "CHAPTER"}


def extract(text: str) -> list[tuple[str, str]]:
    """Return ``[(kind, canonical_name)]`` for one passage, deduplicated."""
    found: dict[tuple[str, str], None] = {}

    for kind, term, pattern in _GAZ_PATTERNS:
        if pattern.search(text):
            found[(kind, term)] = None

    for kind, pattern in PATTERNS:
        for match in pattern.findall(text):
            name = match.strip()
            if kind == "mutation" and name.split()[0].upper() in _MUTATION_STOPWORDS:
                continue
            found[(kind, name)] = None

    return list(found)


# ----------------------------------------------------------------- indexing

def _entity_id(con: sqlite3.Connection, kind: str, name: str) -> int:
    row = con.execute(
        "SELECT id FROM entities WHERE name=? AND kind=?", (name, kind)
    ).fetchone()
    if row:
        return int(row["id"])
    cur = con.execute(
        "INSERT INTO entities(name, kind, canonical) VALUES (?,?,?)",
        (name, kind, name.lower()),
    )
    return int(cur.lastrowid)


def index_chunks(con: sqlite3.Connection, chunk_ids: Iterable[int] | None = None,
                 *, batch_commit: int = 500) -> dict[str, int]:
    """Extract entities from chunks and build the co-occurrence graph.

    Idempotent: re-running only adds mentions for chunks that lack them.
    """
    if chunk_ids is None:
        rows = con.execute(
            """SELECT c.id, c.text FROM chunks c
               WHERE NOT EXISTS (SELECT 1 FROM mentions m WHERE m.chunk_id = c.id)"""
        ).fetchall()
    else:
        ids = list(chunk_ids)
        if not ids:
            return {"chunks": 0, "entities": 0, "mentions": 0}
        rows = con.execute(
            f"SELECT id, text FROM chunks WHERE id IN ({','.join('?' * len(ids))})", ids
        ).fetchall()

    n_mentions = 0
    edge_updates: dict[tuple[int, int], float] = defaultdict(float)

    for i, row in enumerate(rows):
        ents = extract(row["text"])
        if not ents:
            continue
        ids = []
        for kind, name in ents:
            eid = _entity_id(con, kind, name)
            ids.append(eid)
            try:
                con.execute(
                    "INSERT INTO mentions(entity_id, chunk_id) VALUES (?,?)",
                    (eid, row["id"]),
                )
                n_mentions += 1
            except sqlite3.IntegrityError:
                pass  # already recorded

        # Co-occurrence edges. Weight is 1/n so a passage listing forty entities
        # does not swamp a passage that ties two of them together tightly.
        if 1 < len(ids) <= 40:
            w = 1.0 / len(ids)
            for a in ids:
                for b in ids:
                    if a < b:
                        edge_updates[(a, b)] += w

        if i % batch_commit == 0:
            con.commit()

    for (a, b), w in edge_updates.items():
        con.execute(
            """INSERT INTO entity_edges(a, b, weight) VALUES (?,?,?)
               ON CONFLICT(a, b) DO UPDATE SET weight = weight + excluded.weight""",
            (a, b, w),
        )
    con.commit()

    return {
        "chunks": len(rows),
        "entities": con.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"],
        "mentions": n_mentions,
    }


# ----------------------------------------------------------------- querying

def seeds_for(con: sqlite3.Connection, query: str) -> list[int]:
    """Entity ids mentioned in the question itself."""
    out = []
    for kind, name in extract(query):
        row = con.execute(
            "SELECT id FROM entities WHERE name=? AND kind=?", (name, kind)
        ).fetchone()
        if row:
            out.append(int(row["id"]))
    return out


def neighbours(con: sqlite3.Connection, entity_id: int, limit: int = 15) -> list[dict]:
    rows = con.execute(
        """SELECT e.id, e.name, e.kind, x.weight FROM entity_edges x
           JOIN entities e ON e.id = CASE WHEN x.a=? THEN x.b ELSE x.a END
           WHERE x.a=? OR x.b=?
           ORDER BY x.weight DESC LIMIT ?""",
        (entity_id, entity_id, entity_id, limit),
    ).fetchall()
    return db.rows_to_dicts(rows)


# The whole edge table is pulled for every graph-ranked query, and a decomposed
# question runs five of them. Cache the adjacency list, keyed on the edge count
# and the max rowid so an ingest invalidates it without needing a callback.
_ADJ_CACHE: dict[str, Any] = {"key": None, "adj": None}


def _adjacency(con: sqlite3.Connection) -> dict[int, list[tuple[int, float]]]:
    row = con.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(weight), 0) w FROM entity_edges"
    ).fetchone()
    key = (row["n"], round(float(row["w"]), 4))
    if _ADJ_CACHE["key"] == key and _ADJ_CACHE["adj"] is not None:
        return _ADJ_CACHE["adj"]

    adjacency: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for e in con.execute("SELECT a, b, weight FROM entity_edges WHERE weight > 0"):
        adjacency[e["a"]].append((e["b"], e["weight"]))
        adjacency[e["b"]].append((e["a"], e["weight"]))

    _ADJ_CACHE["key"] = key
    _ADJ_CACHE["adj"] = adjacency
    return adjacency


def invalidate_cache() -> None:
    _ADJ_CACHE["key"] = None
    _ADJ_CACHE["adj"] = None


def personalized_pagerank(
    con: sqlite3.Connection,
    seeds: list[int],
    *,
    damping: float = 0.5,
    iterations: int = 12,
    top_n: int = 60,
) -> dict[int, float]:
    """Spread activation from the seed entities across the co-occurrence graph.

    Damping is deliberately low (0.5, against the classic 0.85). We want
    activation to stay close to the question — two hops of "what is this
    actually connected to", not a global popularity score that would surface
    `neoantigen` for every query because everything co-occurs with it.
    """
    if not seeds:
        return {}

    adjacency = _adjacency(con)
    if not adjacency:
        return {s: 1.0 for s in seeds}

    seed_mass = 1.0 / len(seeds)
    reset = {s: seed_mass for s in seeds}
    scores: dict[int, float] = dict(reset)

    for _ in range(iterations):
        nxt: dict[int, float] = defaultdict(float)
        for node, score in scores.items():
            edges = adjacency.get(node)
            if not edges:
                nxt[node] += score
                continue
            total = sum(w for _, w in edges) or 1.0
            for neighbour, w in edges:
                nxt[neighbour] += damping * score * (w / total)
        for node, mass in reset.items():
            nxt[node] += (1 - damping) * mass
        scores = dict(nxt)

    return dict(sorted(scores.items(), key=lambda kv: -kv[1])[:top_n])


def rank_chunks(
    con: sqlite3.Connection,
    query: str,
    k: int = 30,
    *,
    doc_kind: str | None = None,
) -> list[tuple[int, float]]:
    """Rank passages by the activation of the entities they mention.

    This is the third retrieval leg. It finds passages that are *connected* to
    the question rather than worded like it — which is exactly the case
    keyword and vector search both miss.
    """
    seeds = seeds_for(con, query)
    if not seeds:
        return []
    activation = personalized_pagerank(con, seeds)
    if not activation:
        return []

    ids = list(activation)
    placeholders = ",".join("?" * len(ids))
    sql = f"""SELECT m.chunk_id, m.entity_id FROM mentions m
              JOIN chunks c ON c.id = m.chunk_id
              WHERE m.entity_id IN ({placeholders})"""
    args: list[Any] = list(ids)
    if doc_kind:
        sql += " AND c.doc_kind = ?"
        args.append(doc_kind)

    per_chunk: dict[int, float] = defaultdict(float)
    hits: dict[int, int] = defaultdict(int)
    for row in con.execute(sql, args):
        per_chunk[row["chunk_id"]] += activation[row["entity_id"]]
        hits[row["chunk_id"]] += 1

    # Reward passages that connect several activated entities: a passage
    # mentioning both CT26 and AH1 is the bridge we are looking for; one
    # mentioning CT26 alone is not.
    scored = [(cid, score * (1 + 0.35 * (hits[cid] - 1))) for cid, score in per_chunk.items()]
    scored.sort(key=lambda t: -t[1])
    return scored[:k]


def explain_path(con: sqlite3.Connection, a_name: str, b_name: str,
                 max_depth: int = 3) -> list[str] | None:
    """Shortest co-occurrence path between two entities, for auditing the graph.

    A path is not a causal claim. It says these concepts are discussed together
    through these intermediates — a lead to follow, not a finding.
    """
    def find(name: str) -> int | None:
        row = con.execute(
            "SELECT id FROM entities WHERE canonical=? ORDER BY n_mentions DESC LIMIT 1",
            (name.lower(),),
        ).fetchone()
        return int(row["id"]) if row else None

    start, goal = find(a_name), find(b_name)
    if start is None or goal is None:
        return None
    if start == goal:
        return [a_name]

    frontier = [(start, [start])]
    seen = {start}
    for _ in range(max_depth):
        nxt = []
        for node, path in frontier:
            for nb in neighbours(con, node, limit=25):
                nid = int(nb["id"])
                if nid in seen:
                    continue
                if nid == goal:
                    full = path + [nid]
                    names = con.execute(
                        f"SELECT id, name FROM entities WHERE id IN ({','.join('?' * len(full))})",
                        full,
                    ).fetchall()
                    lookup = {r["id"]: r["name"] for r in names}
                    return [lookup[i] for i in full]
                seen.add(nid)
                nxt.append((nid, path + [nid]))
        frontier = nxt
        if not frontier:
            break
    return None


def stats(con: sqlite3.Connection) -> dict[str, Any]:
    top = con.execute(
        "SELECT name, kind, n_mentions FROM entities ORDER BY n_mentions DESC LIMIT 10"
    ).fetchall()
    return {
        "entities": con.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"],
        "mentions": con.execute("SELECT COUNT(*) c FROM mentions").fetchone()["c"],
        "edges": con.execute("SELECT COUNT(*) c FROM entity_edges").fetchone()["c"],
        "top": [dict(r) for r in top],
    }
