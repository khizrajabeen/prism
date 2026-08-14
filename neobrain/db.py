"""SQLite storage: schema, migrations, and the small query helpers.

Design notes
------------
* One file, ``brain.db``. No server. Backed up by copying it.
* FTS5 virtual tables mirror ``papers`` and ``chunks`` through triggers, so
  keyword search is always in sync without an explicit reindex step.
* Embeddings live in a plain BLOB column. Brute-force cosine over a few tens of
  thousands of chunks takes milliseconds and saves a dependency; if the corpus
  ever outgrows that, swap :mod:`neobrain.retrieve` for a vector index and
  nothing else has to change.
* Every table that holds a claim also holds provenance. That is the whole point
  of the system: no assertion without a source you can open.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import config

SCHEMA_VERSION = 7

SCHEMA = """
-- ------------------------------------------------------------------ papers
CREATE TABLE IF NOT EXISTS papers (
    id          TEXT PRIMARY KEY,   -- e.g. "MED:39012345" or "PPR:PPR812345"
    source      TEXT,               -- journal | preprint | local
    provider    TEXT,               -- europepmc | pubmed | biorxiv | local
    title       TEXT,
    abstract    TEXT,
    authors     TEXT,
    journal     TEXT,
    pub_date    TEXT,
    doi         TEXT,
    pmid        TEXT,
    pmcid       TEXT,
    url         TEXT,
    is_oa       INTEGER DEFAULT 0,
    score       INTEGER DEFAULT 0,
    matched     TEXT,
    buckets     TEXT,               -- comma-separated interest buckets that hit
    first_seen  TEXT,
    last_seen   TEXT,
    read_state  TEXT DEFAULT 'new', -- new | queued | read | skimmed | rejected
    rating      INTEGER,            -- 1-5, set by you, used to tune scoring
    notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_papers_score ON papers(score DESC);
CREATE INDEX IF NOT EXISTS idx_papers_seen  ON papers(first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_papers_state ON papers(read_state);
CREATE INDEX IF NOT EXISTS idx_papers_doi   ON papers(doi);

-- Full text of open-access papers, split into sections. Methods sections are
-- where the protocol detail lives, which is most of why we bother.
CREATE TABLE IF NOT EXISTS fulltext (
    paper_id    TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    fetched_at  TEXT,
    license     TEXT,
    n_chars     INTEGER,
    origin      TEXT                -- epmc-xml | pdf | manual
);
CREATE TABLE IF NOT EXISTS sections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id    TEXT REFERENCES papers(id) ON DELETE CASCADE,
    ord         INTEGER,
    heading     TEXT,
    kind        TEXT,               -- abstract|intro|methods|results|discussion|other
    text        TEXT
);
CREATE INDEX IF NOT EXISTS idx_sections_paper ON sections(paper_id, ord);
CREATE INDEX IF NOT EXISTS idx_sections_kind  ON sections(kind);

-- ------------------------------------------------------------------ trials
CREATE TABLE IF NOT EXISTS trials (
    nct_id      TEXT PRIMARY KEY,
    title       TEXT,
    status      TEXT,
    phase       TEXT,
    conditions  TEXT,
    interventions TEXT,
    sponsor     TEXT,
    enrollment  INTEGER,
    start_date  TEXT,
    last_update TEXT,
    url         TEXT,
    summary     TEXT,
    first_seen  TEXT,
    last_seen   TEXT
);
-- Status transitions are the signal (recruiting -> active -> completed),
-- so we keep the history rather than overwriting.
CREATE TABLE IF NOT EXISTS trial_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nct_id      TEXT REFERENCES trials(nct_id) ON DELETE CASCADE,
    observed_on TEXT,
    field       TEXT,
    old_value   TEXT,
    new_value   TEXT
);

-- -------------------------------------------------------------- retrieval
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id    TEXT REFERENCES papers(id) ON DELETE CASCADE,
    doc_kind    TEXT,               -- paper | knowledge | note
    doc_ref     TEXT,               -- knowledge/foo.md when doc_kind='knowledge'
    heading     TEXT,
    ord         INTEGER,
    text        TEXT,
    n_chars     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chunks_paper ON chunks(paper_id);
CREATE INDEX IF NOT EXISTS idx_chunks_ref   ON chunks(doc_ref);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id    INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model       TEXT,
    dim         INTEGER,
    vec         BLOB,
    created_at  TEXT
);

-- ------------------------------------------------------------------ memory
-- Semantic memory with explicit confidence and provenance.
CREATE TABLE IF NOT EXISTS beliefs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    claim       TEXT NOT NULL,
    confidence  TEXT,               -- high | moderate | low | contested
    status      TEXT DEFAULT 'active',  -- active | superseded | retracted
    topic       TEXT,
    rationale   TEXT,
    created_at  TEXT,
    updated_at  TEXT,
    review_on   TEXT,               -- when to re-check this against the literature
    superseded_by INTEGER REFERENCES beliefs(id)
);
CREATE TABLE IF NOT EXISTS belief_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    belief_id   INTEGER REFERENCES beliefs(id) ON DELETE CASCADE,
    paper_id    TEXT,
    citation    TEXT,               -- free text when not in papers table
    url         TEXT,
    stance      TEXT DEFAULT 'supports'  -- supports | contradicts | qualifies
);
CREATE INDEX IF NOT EXISTS idx_belief_sources ON belief_sources(belief_id);

-- Episodic memory.
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    ended_at    TEXT,
    topic       TEXT,
    summary     TEXT,
    decisions   TEXT,
    open_threads TEXT
);

-- The approval gate. The agent never edits memory directly; it writes a
-- proposal, you review the diff, and only then is it applied.
CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT,
    kind        TEXT,               -- core | knowledge | belief | belief_update
    target      TEXT,               -- file path or belief id
    rationale   TEXT,
    payload     TEXT,               -- JSON: {"mode": "...", ...}
    evidence    TEXT,
    status      TEXT DEFAULT 'pending',  -- pending | applied | rejected
    decided_at  TEXT,
    decided_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);

-- ------------------------------------------------------------------- tutor
CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    front       TEXT NOT NULL,
    back        TEXT NOT NULL,
    topic       TEXT,
    source      TEXT,
    created_at  TEXT,
    -- SM-2 scheduling state
    ease        REAL DEFAULT 2.5,
    interval    INTEGER DEFAULT 0,  -- days
    due_on      TEXT,
    reps        INTEGER DEFAULT 0,
    lapses      INTEGER DEFAULT 0,
    last_grade  INTEGER,
    suspended   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(due_on);

-- ---------------------------------------------------------------- bookkeeping
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      TEXT,
    kind        TEXT,               -- sweep | embed | fulltext
    new_papers  INTEGER DEFAULT 0,
    new_trials  INTEGER DEFAULT 0,
    changed_trials INTEGER DEFAULT 0,
    fulltext_added INTEGER DEFAULT 0,
    embedded    INTEGER DEFAULT 0,
    errors      TEXT,
    duration_s  REAL
);
CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT
);

-- ============================================================ THE WORK
-- The scientific method as a data model, not a note-taking one.
--
--   question → evidence → hypothesis → prediction → experiment → result
--            → belief update → next question
--
-- Everything else in this database attaches to a node of that loop. A feature
-- that attaches to none of them is a utility, not part of the brain.
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    question    TEXT,               -- the one-sentence question this project answers
    status      TEXT DEFAULT 'active',   -- active | paused | done | abandoned
    started_at  TEXT,
    updated_at  TEXT,
    deadline    TEXT,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    statement   TEXT NOT NULL,
    rationale   TEXT,
    -- open | supported | refuted | inconclusive | abandoned
    status      TEXT DEFAULT 'open',
    -- What observation would make you abandon this? A hypothesis without a
    -- falsifier is a belief, and belongs in the beliefs table instead.
    falsifier   TEXT,
    prior       TEXT,               -- your stated confidence before testing
    posterior   TEXT,               -- after the evidence came in
    created_at  TEXT,
    resolved_at TEXT,
    resolution  TEXT
);
CREATE INDEX IF NOT EXISTS idx_hyp_project ON hypotheses(project_id, status);

CREATE TABLE IF NOT EXISTS experiments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    hypothesis_id INTEGER REFERENCES hypotheses(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    design      TEXT,               -- model, groups, n, endpoints
    -- Recorded BEFORE the result exists. This is the whole point: with the
    -- prediction on the record, hindsight bias becomes visible instead of
    -- invisible, and a surprising result stays surprising.
    prediction  TEXT,
    predicted_at TEXT,
    -- planned | running | done | abandoned
    status      TEXT DEFAULT 'planned',
    started_at  TEXT,
    ended_at    TEXT,
    result      TEXT,
    outcome     TEXT,               -- as_predicted | contradicted | ambiguous | failed
    surprise    INTEGER,            -- 0-5, recorded at result time
    tools       TEXT,               -- JSON: {tool: version} captured at run time
    notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_exp_project ON experiments(project_id, status);

-- Decisions are not beliefs. "We will use MC38 rather than CT26" is a choice
-- with a rationale and a review date, not a claim about the world.
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    decision    TEXT NOT NULL,
    rationale   TEXT,
    alternatives TEXT,              -- what was considered and rejected
    would_revisit_if TEXT,          -- the trigger to reopen this
    decided_at  TEXT,
    review_on   TEXT,
    status      TEXT DEFAULT 'standing'   -- standing | revisited | reversed
);

-- Links any work item to the evidence behind it. One table rather than five
-- join tables, because the shapes are identical and the queries are simple.
CREATE TABLE IF NOT EXISTS evidence_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT,               -- hypothesis | experiment | decision | project
    item_id     INTEGER,
    paper_id    TEXT,
    chunk_id    INTEGER,
    belief_id   INTEGER,
    stance      TEXT DEFAULT 'supports',   -- supports | contradicts | informs
    note        TEXT,
    linked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_evlinks ON evidence_links(kind, item_id);

-- Answers we have given, with the evidence and the verdict, so a claim in a
-- draft can be traced back to what the corpus actually said at the time.
CREATE TABLE IF NOT EXISTS claim_checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at  TEXT,
    claim       TEXT,
    verdict     TEXT,               -- supported | unsupported | contradicted | mixed
    confidence  TEXT,
    n_support   INTEGER DEFAULT 0,
    n_contra    INTEGER DEFAULT 0,
    top_tier    INTEGER,
    sources     TEXT,               -- JSON
    note        TEXT
);

-- ------------------------------------------------ structured extraction
-- One row per extracted value, each carrying the sentence it came from.
-- The provenance column is not decoration: it is what separates an extraction
-- you can defend in a methods section from a number in a spreadsheet.
CREATE TABLE IF NOT EXISTS extractions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id    TEXT REFERENCES papers(id) ON DELETE CASCADE,
    field       TEXT,
    value       TEXT,
    unit        TEXT,
    evidence    TEXT,        -- the source sentence, verbatim
    section     TEXT,
    confidence  TEXT,
    extracted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_extractions ON extractions(paper_id, field);
CREATE INDEX IF NOT EXISTS idx_extractions_field ON extractions(field);

-- ------------------------------------------------- guided discovery
-- Saved searches from the Research workflow. Keeping the plan alongside the
-- results is what makes a literature search reproducible: six months later you
-- can see not just what you found but what you asked for.
CREATE TABLE IF NOT EXISTS searches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    created_at  TEXT,
    keywords    TEXT,
    plan        TEXT,        -- JSON: the query plan, including the narrowing choices
    counts      TEXT,        -- JSON: retrieved / unique / OA, per source
    n_results   INTEGER
);
CREATE TABLE IF NOT EXISTS search_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id   INTEGER REFERENCES searches(id) ON DELETE CASCADE,
    doi         TEXT,
    pmid        TEXT,
    title       TEXT,
    authors     TEXT,
    journal     TEXT,
    pub_date    TEXT,
    abstract    TEXT,
    url         TEXT,
    pdf_url     TEXT,
    is_oa       INTEGER DEFAULT 0,
    cited_by    INTEGER DEFAULT 0,
    found_by    TEXT,        -- which sources returned it; agreement is a weak quality signal
    screen      TEXT,        -- include | exclude | maybe
    screen_reasons TEXT
);
CREATE INDEX IF NOT EXISTS idx_sr_search ON search_results(search_id, screen);

-- ------------------------------------------------------------- journal
-- Append-only episodic memory. Everything the brain ever learns lands here
-- first, immediately, with no approval step — because the cost of losing an
-- observation is higher than the cost of storing a bad one. Curation into
-- beliefs/knowledge is a separate, reviewed process.
--
-- The triggers below make this table genuinely immutable at the storage layer:
-- UPDATE and DELETE abort. Nothing in this system, including a confused agent
-- and including me, can rewrite history here.
CREATE TABLE IF NOT EXISTS journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    kind        TEXT,      -- observation|correction|decision|preference|result|
                           -- question|error|session|sweep|system
    topic       TEXT,
    text        TEXT NOT NULL,
    source      TEXT,      -- paper id, URL, 'user', 'sweep', 'agent'
    session_id  INTEGER,
    entities    TEXT,
    importance  INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_journal_at    ON journal(at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_kind  ON journal(kind);
CREATE INDEX IF NOT EXISTS idx_journal_topic ON journal(topic);

-- --------------------------------------------------- procedural memory
-- The third memory scope: learned behaviours and rules, as distinct from
-- facts (semantic) and events (episodic). "Always check for an adjuvant-alone
-- arm" is not a fact about the world — it is a rule about how to work.
CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger     TEXT NOT NULL,   -- when this applies
    action      TEXT NOT NULL,   -- what to do
    scope       TEXT,            -- domain area, for retrieval
    source      TEXT,            -- how we learned it
    created_at  TEXT,
    active      INTEGER DEFAULT 1,
    fired       INTEGER DEFAULT 0,
    last_fired  TEXT
);

-- --------------------------------------------------- entity graph
-- Entity-mediated retrieval, HippoRAG-style: seed the graph with the entities
-- in a question, spread activation, and surface passages that are connected to
-- the question through the literature rather than merely similar to its wording.
CREATE TABLE IF NOT EXISTS entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    kind        TEXT,       -- gene|hla|cell_line|mouse|tool|assay|mutation|concept
    canonical   TEXT,
    n_mentions  INTEGER DEFAULT 0,
    UNIQUE(name, kind)
);
CREATE TABLE IF NOT EXISTS mentions (
    entity_id   INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    chunk_id    INTEGER REFERENCES chunks(id) ON DELETE CASCADE,
    PRIMARY KEY (entity_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_mentions_chunk ON mentions(chunk_id);
CREATE TABLE IF NOT EXISTS entity_edges (
    a           INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    b           INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    weight      REAL DEFAULT 0,
    PRIMARY KEY (a, b)
);

-- --------------------------------------------------- conflict queue
-- Detected tension between a stored belief and newly ingested evidence.
-- Surfaced for review rather than resolved automatically: the system's job is
-- to notice the contradiction, yours is to adjudicate it.
CREATE TABLE IF NOT EXISTS conflicts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT,
    belief_id   INTEGER REFERENCES beliefs(id) ON DELETE CASCADE,
    paper_id    TEXT,
    chunk_id    INTEGER,
    cue         TEXT,       -- what tripped the detector
    passage     TEXT,
    status      TEXT DEFAULT 'open',   -- open | resolved | dismissed
    note        TEXT,
    UNIQUE(belief_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status);

-- --------------------------------------------------- answer provenance
-- What evidence was in front of the model when it said something. Six months
-- later, "why did it tell me that?" is an answerable question.
CREATE TABLE IF NOT EXISTS answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at    TEXT,
    question    TEXT,
    chunk_ids   TEXT,
    paper_ids   TEXT,
    session_id  INTEGER,
    note        TEXT
);

-- ---------------------------------------------------------------- full text search
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, abstract, authors, journal,
    content='papers', content_rowid='rowid', tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, heading,
    content='chunks', content_rowid='id', tokenize='porter unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts USING fts5(
    text, topic,
    content='journal', content_rowid='id', tokenize='porter unicode61'
);
"""

TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract, authors, journal)
    VALUES (new.rowid, new.title, new.abstract, new.authors, new.journal);
END;
CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, authors, journal)
    VALUES ('delete', old.rowid, old.title, old.abstract, old.authors, old.journal);
END;
CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, authors, journal)
    VALUES ('delete', old.rowid, old.title, old.abstract, old.authors, old.journal);
    INSERT INTO papers_fts(rowid, title, abstract, authors, journal)
    VALUES (new.rowid, new.title, new.abstract, new.authors, new.journal);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, heading)
    VALUES ('delete', old.id, old.text, old.heading);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, heading)
    VALUES ('delete', old.id, old.text, old.heading);
    INSERT INTO chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
END;

-- The journal is append-only, enforced here rather than by convention. An
-- agent that can quietly revise what it observed last month is not a memory,
-- it is a rumour mill.
CREATE TRIGGER IF NOT EXISTS journal_ai AFTER INSERT ON journal BEGIN
    INSERT INTO journal_fts(rowid, text, topic) VALUES (new.id, new.text, new.topic);
END;
CREATE TRIGGER IF NOT EXISTS journal_no_update BEFORE UPDATE ON journal BEGIN
    SELECT RAISE(ABORT, 'the journal is append-only: correct an entry by adding a new one');
END;
CREATE TRIGGER IF NOT EXISTS journal_no_delete BEFORE DELETE ON journal BEGIN
    SELECT RAISE(ABORT, 'the journal is append-only: entries are never deleted');
END;

-- Mentions must not outlive their chunk, but the entity survives so its
-- history and edges remain queryable.
CREATE TRIGGER IF NOT EXISTS mentions_count_ai AFTER INSERT ON mentions BEGIN
    UPDATE entities SET n_mentions = n_mentions + 1 WHERE id = new.entity_id;
END;
"""


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return dt.date.today().isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open (and if needed create) the brain."""
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")
    _migrate(con)
    return con


# Columns added after v3. CREATE TABLE IF NOT EXISTS does nothing for a table
# that already exists, so new columns need explicit ALTERs on an existing brain.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # (table, column, definition)
    ("beliefs", "valid_from", "TEXT"),      # when the claim became true in the world
    ("beliefs", "valid_until", "TEXT"),     # when it stopped being true (NULL = still holds)
    ("beliefs", "asserted_at", "TEXT"),     # when we came to believe it
    ("beliefs", "invalidated_at", "TEXT"),  # when we stopped believing it
    ("beliefs", "invalidated_reason", "TEXT"),
    ("beliefs", "root_id", "INTEGER"),      # first version in this claim's lineage
    ("beliefs", "version", "INTEGER DEFAULT 1"),
    ("papers", "study_type", "TEXT"),
    ("papers", "evidence_tier", "INTEGER"),
]


def _add_missing_columns(con: sqlite3.Connection) -> None:
    for table, column, definition in _ADDED_COLUMNS:
        cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate(con: sqlite3.Connection) -> None:
    # Fast path. The web server opens a connection per request and the CLI opens
    # one per command; running ~200 DDL statements every time to discover that
    # nothing has changed is pure overhead. One cheap SELECT decides.
    try:
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is not None and int(row["value"]) == SCHEMA_VERSION:
            return
    except sqlite3.OperationalError:
        pass  # meta table does not exist yet — this is a fresh database

    con.executescript(SCHEMA)
    _add_missing_columns(con)
    con.executescript(TRIGGERS)

    cur = con.execute("SELECT value FROM meta WHERE key='schema_version'")
    row = cur.fetchone()
    if row is None:
        con.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        con.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('created_at', ?)", (now(),)
        )
    elif int(row["value"]) < SCHEMA_VERSION:
        # Backfill the bitemporal fields for beliefs recorded before v4, so
        # "what did I believe on date X?" works over the whole history.
        con.execute(
            "UPDATE beliefs SET asserted_at = COALESCE(asserted_at, created_at),"
            " valid_from = COALESCE(valid_from, created_at),"
            " root_id = COALESCE(root_id, id) WHERE asserted_at IS NULL OR root_id IS NULL"
        )
        con.execute(
            "UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),)
        )
    con.commit()


# --------------------------------------------------------------- small helpers

def fts_escape(query: str) -> str:
    """Make arbitrary user text safe for an FTS5 MATCH.

    FTS5 treats a pile of punctuation as syntax. Rather than trying to preserve
    the user's boolean intent, quote every bare token — the retrieval layer is
    doing rank fusion anyway, so a bag of terms is the honest input.
    """
    tokens = [t for t in "".join(c if c.isalnum() or c in "-*" else " " for c in query).split() if t]
    if not tokens:
        return '""'
    return " OR ".join(f'"{t}"' if not t.endswith("*") else f'"{t[:-1]}"*' for t in tokens)


def upsert_paper(con: sqlite3.Connection, p: dict[str, Any]) -> bool:
    """Insert a paper, or refresh score/last_seen if we have seen it.

    Returns True when the paper is new to the brain.
    """
    existing = con.execute("SELECT id, buckets FROM papers WHERE id=?", (p["id"],)).fetchone()
    stamp = today()
    if existing:
        buckets = {b for b in (existing["buckets"] or "").split(",") if b}
        buckets |= {b for b in (p.get("buckets") or "").split(",") if b}
        con.execute(
            "UPDATE papers SET last_seen=?, score=MAX(score, ?), buckets=? WHERE id=?",
            (stamp, p.get("score", 0), ",".join(sorted(buckets)), p["id"]),
        )
        return False
    con.execute(
        """INSERT INTO papers
           (id, source, provider, title, abstract, authors, journal, pub_date,
            doi, pmid, pmcid, url, is_oa, score, matched, buckets,
            first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            p["id"], p.get("source"), p.get("provider"), p.get("title"),
            p.get("abstract"), p.get("authors"), p.get("journal"), p.get("pub_date"),
            p.get("doi"), p.get("pmid"), p.get("pmcid"), p.get("url"),
            int(bool(p.get("is_oa"))), p.get("score", 0), p.get("matched"),
            p.get("buckets"), stamp, stamp,
        ),
    )
    return True


def upsert_trial(con: sqlite3.Connection, t: dict[str, Any]) -> str:
    """Insert or update a trial. Returns 'new', 'changed', or 'same'."""
    stamp = today()
    row = con.execute("SELECT * FROM trials WHERE nct_id=?", (t["nct_id"],)).fetchone()
    if row is None:
        con.execute(
            """INSERT INTO trials
               (nct_id, title, status, phase, conditions, interventions, sponsor,
                enrollment, start_date, last_update, url, summary, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t["nct_id"], t.get("title"), t.get("status"), t.get("phase"),
                t.get("conditions"), t.get("interventions"), t.get("sponsor"),
                t.get("enrollment"), t.get("start_date"), t.get("last_update"),
                t.get("url"), t.get("summary"), stamp, stamp,
            ),
        )
        return "new"

    changed = []
    for field in ("status", "phase", "enrollment", "last_update", "title"):
        old, new = row[field], t.get(field)
        if new is not None and str(old) != str(new):
            changed.append((field, old, new))
    for field, old, new in changed:
        con.execute(
            "INSERT INTO trial_history(nct_id, observed_on, field, old_value, new_value)"
            " VALUES (?,?,?,?,?)",
            (t["nct_id"], stamp, field, str(old), str(new)),
        )
    con.execute(
        """UPDATE trials SET title=?, status=?, phase=?, conditions=?, interventions=?,
           sponsor=?, enrollment=?, start_date=?, last_update=?, url=?, summary=?, last_seen=?
           WHERE nct_id=?""",
        (
            t.get("title"), t.get("status"), t.get("phase"), t.get("conditions"),
            t.get("interventions"), t.get("sponsor"), t.get("enrollment"),
            t.get("start_date"), t.get("last_update"), t.get("url"), t.get("summary"),
            stamp, t["nct_id"],
        ),
    )
    return "changed" if changed else "same"


def record_run(con: sqlite3.Connection, kind: str, **counts: Any) -> None:
    con.execute(
        """INSERT INTO runs(run_at, kind, new_papers, new_trials, changed_trials,
                            fulltext_added, embedded, errors, duration_s)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            now(), kind, counts.get("new_papers", 0), counts.get("new_trials", 0),
            counts.get("changed_trials", 0), counts.get("fulltext_added", 0),
            counts.get("embedded", 0),
            json.dumps(counts.get("errors", []))[:4000], counts.get("duration_s"),
        ),
    )
    con.commit()


def stats(con: sqlite3.Connection) -> dict[str, Any]:
    def one(sql: str, *args: Any) -> Any:
        r = con.execute(sql, args).fetchone()
        return r[0] if r else 0

    return {
        "papers": one("SELECT COUNT(*) FROM papers"),
        "papers_oa": one("SELECT COUNT(*) FROM papers WHERE is_oa=1"),
        "papers_read": one("SELECT COUNT(*) FROM papers WHERE read_state IN ('read','skimmed')"),
        "fulltext": one("SELECT COUNT(*) FROM fulltext"),
        "sections": one("SELECT COUNT(*) FROM sections"),
        "trials": one("SELECT COUNT(*) FROM trials"),
        "trial_changes": one("SELECT COUNT(*) FROM trial_history"),
        "chunks": one("SELECT COUNT(*) FROM chunks"),
        "embeddings": one("SELECT COUNT(*) FROM embeddings"),
        "beliefs": one("SELECT COUNT(*) FROM beliefs WHERE status='active'"),
        "beliefs_due": one(
            "SELECT COUNT(*) FROM beliefs WHERE status='active' AND review_on IS NOT NULL AND review_on<=?",
            today(),
        ),
        "proposals_pending": one("SELECT COUNT(*) FROM proposals WHERE status='pending'"),
        "cards": one("SELECT COUNT(*) FROM cards WHERE suspended=0"),
        "cards_due": one(
            "SELECT COUNT(*) FROM cards WHERE suspended=0 AND (due_on IS NULL OR due_on<=?)",
            today(),
        ),
        "sessions": one("SELECT COUNT(*) FROM sessions"),
        "journal": one("SELECT COUNT(*) FROM journal"),
        "journal_since": one("SELECT MIN(at) FROM journal"),
        "rules": one("SELECT COUNT(*) FROM rules WHERE active=1"),
        "entities": one("SELECT COUNT(*) FROM entities"),
        "entity_edges": one("SELECT COUNT(*) FROM entity_edges"),
        "conflicts_open": one("SELECT COUNT(*) FROM conflicts WHERE status='open'"),
        "graded": one("SELECT COUNT(*) FROM papers WHERE evidence_tier IS NOT NULL"),
        "last_sweep": one("SELECT run_at FROM runs WHERE kind='sweep' ORDER BY id DESC LIMIT 1"),
        "db_path": str(config.DB_PATH),
        "db_mb": round(config.DB_PATH.stat().st_size / 1e6, 2) if config.DB_PATH.exists() else 0,
    }


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def executemany(con: sqlite3.Connection, sql: str, seq: Sequence[Sequence[Any]]) -> None:
    if seq:
        con.executemany(sql, seq)
