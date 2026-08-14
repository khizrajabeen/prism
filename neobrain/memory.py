"""Three-tier memory, with an approval gate on every write.

The tiers
---------
**Core** (`memory/CORE.md`) — who you are, what you are working on, standing
preferences, and the handful of beliefs that shape every answer. Loaded in full
at every session start. Hard-capped, because everything you put here you pay
for in every single conversation.

**Semantic** (`knowledge/*.md` + the `beliefs` table) — durable domain
knowledge, retrieved on demand rather than always loaded. Beliefs carry an
explicit confidence, sources, and a review date, so a claim that was true in
2026 gets re-examined instead of quietly hardening into dogma.

**Episodic** (`brain.db`: papers, sessions, digests) — everything ever seen.
Searched, never loaded wholesale.

Why "read all my memory at startup" is the wrong design
-------------------------------------------------------
It works for a week. Then the context window fills with stale material, the
signal-to-noise ratio collapses, and answer quality drops in a way that is hard
to attribute. What you actually want — and what `brief()` gives you — is a
bounded startup payload: core memory in full, the latest digest, what changed,
what is awaiting your decision, and pointers to everything else. Retrieval
handles the rest, on demand, with sources attached.

The approval gate
-----------------
The agent never writes to core or knowledge directly. It writes a *proposal*;
you see a diff; you approve or reject. An agent that silently rewrites its own
beliefs will drift, and you will not notice until it confidently tells you
something wrong in a paper draft. `neobrain review` is the whole safeguard.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import config, db, digest, journal

VALID_CONFIDENCE = ("high", "moderate", "low", "contested")


# ------------------------------------------------------------------ core file

def read_core() -> str:
    if config.CORE_MEMORY.exists():
        return config.CORE_MEMORY.read_text(encoding="utf-8")
    return ""


def core_token_estimate() -> int:
    """Rough token count (chars/4). Good enough to warn you before it hurts."""
    return len(read_core()) // 4


def list_knowledge() -> list[tuple[str, int]]:
    if not config.KNOWLEDGE_DIR.exists():
        return []
    return [
        (p.name, len(p.read_text(encoding="utf-8")) // 4)
        for p in sorted(config.KNOWLEDGE_DIR.glob("*.md"))
    ]


# -------------------------------------------------------------------- beliefs

def add_belief(
    con: sqlite3.Connection,
    claim: str,
    *,
    confidence: str = "moderate",
    topic: str = "",
    rationale: str = "",
    sources: list[dict[str, str]] | None = None,
    review_days: int | None = None,
    valid_from: str | None = None,
    root_id: int | None = None,
    cfg: config.Config | None = None,
) -> int:
    cfg = cfg or config.load()
    if confidence not in VALID_CONFIDENCE:
        raise ValueError(f"confidence must be one of {VALID_CONFIDENCE}")
    if not sources:
        raise ValueError(
            "a belief without a source is a rumour — pass at least one "
            "paper id, PMID, DOI or URL"
        )
    days = review_days if review_days is not None else int(cfg.get("memory.belief_review_days", 120))
    review_on = (dt.date.today() + dt.timedelta(days=days)).isoformat()
    stamp = db.now()

    cur = con.execute(
        """INSERT INTO beliefs(claim, confidence, status, topic, rationale,
                               created_at, updated_at, review_on,
                               asserted_at, valid_from, version)
           VALUES (?,?,'active',?,?,?,?,?,?,?,1)""",
        (claim.strip(), confidence, topic, rationale, stamp, stamp, review_on,
         stamp, valid_from or stamp),
    )
    belief_id = int(cur.lastrowid)
    con.execute("UPDATE beliefs SET root_id=COALESCE(root_id, ?) WHERE id=?",
                (root_id or belief_id, belief_id))
    con.executemany(
        "INSERT INTO belief_sources(belief_id, paper_id, citation, url, stance)"
        " VALUES (?,?,?,?,?)",
        [
            (belief_id, s.get("paper_id"), s.get("citation", ""), s.get("url", ""),
             s.get("stance", "supports"))
            for s in sources
        ],
    )
    con.commit()
    journal.record(
        con,
        f"Belief #{belief_id} recorded [{confidence}]: {claim.strip()}",
        kind="decision", topic=topic or "beliefs", source="memory", importance=3,
    )
    return belief_id


def supersede_belief(con: sqlite3.Connection, old_id: int, new_id: int, note: str = "") -> None:
    """Invalidate a belief without deleting it.

    Bitemporal, in the sense the temporal-knowledge-graph literature uses:
    `valid_until` records when the claim stopped being true of the world;
    `invalidated_at` records when we found that out. The row itself survives,
    so `as_of()` can still answer "what did I believe last March?" — which is
    the question you need when a reviewer asks why your methods section says
    what it says.
    """
    stamp = db.now()
    con.execute(
        """UPDATE beliefs SET status='superseded', superseded_by=?, updated_at=?,
           invalidated_at=?, valid_until=COALESCE(valid_until, ?), invalidated_reason=?
           WHERE id=?""",
        (new_id, stamp, stamp, stamp, note or "superseded by a later belief", old_id),
    )
    con.commit()
    journal.record(
        con,
        f"Belief #{old_id} superseded by #{new_id}: {note}",
        kind="correction", topic="beliefs", source="memory", importance=4,
    )


def revise_belief(
    con: sqlite3.Connection,
    belief_id: int,
    new_claim: str,
    *,
    confidence: str | None = None,
    sources: list[dict[str, str]] | None = None,
    reason: str = "",
    cfg: config.Config | None = None,
) -> int:
    """Record a changed understanding as a new version, keeping the old one.

    This is the operation that makes "it must never forget" compatible with
    "it must be able to change its mind". Nothing is edited in place: the old
    belief is invalidated, a new version is inserted, and both share a
    `root_id` so the lineage is walkable in either direction.
    """
    old = con.execute("SELECT * FROM beliefs WHERE id=?", (belief_id,)).fetchone()
    if old is None:
        raise ValueError(f"no belief #{belief_id}")

    inherited = [
        {"paper_id": s["paper_id"], "citation": s["citation"], "url": s["url"],
         "stance": s["stance"]}
        for s in con.execute(
            "SELECT paper_id, citation, url, stance FROM belief_sources WHERE belief_id=?",
            (belief_id,),
        ).fetchall()
    ]
    merged = inherited + list(sources or [])
    if not merged:
        raise ValueError("a revision still needs at least one source")

    new_id = add_belief(
        con, new_claim,
        confidence=confidence or old["confidence"],
        topic=old["topic"] or "",
        rationale=f"revision of #{belief_id}: {reason}",
        sources=merged,
        root_id=old["root_id"] or belief_id,
        cfg=cfg,
    )
    con.execute("UPDATE beliefs SET version=? WHERE id=?",
                ((old["version"] or 1) + 1, new_id))
    supersede_belief(con, belief_id, new_id, reason)
    con.commit()
    return new_id


def belief_history(con: sqlite3.Connection, belief_id: int) -> list[dict]:
    """Every version of a claim, oldest first."""
    row = con.execute("SELECT root_id, id FROM beliefs WHERE id=?", (belief_id,)).fetchone()
    if row is None:
        return []
    root = row["root_id"] or row["id"]
    return db.rows_to_dicts(con.execute(
        "SELECT * FROM beliefs WHERE root_id=? OR id=? ORDER BY id", (root, root)
    ).fetchall())


def as_of(con: sqlite3.Connection, when: str, *, topic: str | None = None) -> list[dict]:
    """What this brain believed at a past moment.

    Answers "what did I think in March, and on what evidence?" — which is the
    question that comes up when you have to defend a decision made months ago.
    """
    sql = """SELECT * FROM beliefs
             WHERE asserted_at <= ?
               AND (invalidated_at IS NULL OR invalidated_at > ?)"""
    args: list[Any] = [when, when]
    if topic:
        sql += " AND (topic LIKE ? OR claim LIKE ?)"
        args += [f"%{topic}%", f"%{topic}%"]
    return db.rows_to_dicts(con.execute(sql + " ORDER BY id", args).fetchall())


def get_beliefs(
    con: sqlite3.Connection,
    *,
    topic: str | None = None,
    status: str = "active",
    due_only: bool = False,
    limit: int = 200,
) -> list[dict]:
    sql = "SELECT * FROM beliefs WHERE status=?"
    args: list[Any] = [status]
    if topic:
        sql += " AND (topic LIKE ? OR claim LIKE ?)"
        args += [f"%{topic}%", f"%{topic}%"]
    if due_only:
        sql += " AND review_on IS NOT NULL AND review_on<=?"
        args.append(db.today())
    sql += " ORDER BY COALESCE(review_on,'9999'), id LIMIT ?"
    args.append(limit)

    out = []
    for row in con.execute(sql, args).fetchall():
        b = dict(row)
        b["sources"] = [
            dict(s) for s in con.execute(
                "SELECT paper_id, citation, url, stance FROM belief_sources WHERE belief_id=?",
                (row["id"],),
            ).fetchall()
        ]
        out.append(b)
    return out


def format_belief(b: dict) -> str:
    srcs = "; ".join(
        s.get("paper_id") or s.get("citation") or s.get("url") or "?" for s in b.get("sources", [])
    )
    stance_flags = {s.get("stance") for s in b.get("sources", [])}
    flag = " ⚠ contradicted" if "contradicts" in stance_flags else ""
    return (
        f"#{b['id']} [{b['confidence']}]{flag} {b['claim']}\n"
        f"    topic: {b.get('topic') or '—'} · review: {b.get('review_on') or '—'}\n"
        f"    sources: {srcs or 'NONE — this should not happen'}"
    )


# ------------------------------------------------------- procedural memory

def add_rule(
    con: sqlite3.Connection,
    trigger: str,
    action: str,
    *,
    scope: str = "",
    source: str = "",
) -> int:
    """Record a learned way of working.

    The third memory scope, alongside episodic (journal) and semantic
    (beliefs). "When I design a mouse vaccine study, check for an
    adjuvant-alone arm" is not a fact about immunology and does not belong in
    `beliefs` — it is a procedure, and procedures are what turn a corrected
    mistake into a mistake that does not recur.
    """
    existing = con.execute(
        "SELECT id FROM rules WHERE trigger=? AND action=?", (trigger.strip(), action.strip())
    ).fetchone()
    if existing:
        return int(existing["id"])
    cur = con.execute(
        "INSERT INTO rules(trigger, action, scope, source, created_at) VALUES (?,?,?,?,?)",
        (trigger.strip(), action.strip(), scope, source, db.now()),
    )
    con.commit()
    journal.record(
        con, f"Rule learned — when {trigger.strip()}: {action.strip()}",
        kind="preference", topic=scope or "procedure", source=source or "user", importance=4,
    )
    return int(cur.lastrowid)


def get_rules(con: sqlite3.Connection, scope: str | None = None,
              active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM rules WHERE 1=1"
    args: list[Any] = []
    if active_only:
        sql += " AND active=1"
    if scope:
        sql += " AND (scope LIKE ? OR trigger LIKE ? OR action LIKE ?)"
        args += [f"%{scope}%"] * 3
    return db.rows_to_dicts(con.execute(sql + " ORDER BY id", args).fetchall())


def fire_rule(con: sqlite3.Connection, rule_id: int) -> None:
    """Mark a rule as applied, so unused rules can be pruned honestly."""
    con.execute(
        "UPDATE rules SET fired = fired + 1, last_fired=? WHERE id=?", (db.now(), rule_id)
    )
    con.commit()


def retire_rule(con: sqlite3.Connection, rule_id: int, reason: str = "") -> None:
    con.execute("UPDATE rules SET active=0 WHERE id=?", (rule_id,))
    con.commit()
    journal.record(con, f"Rule #{rule_id} retired: {reason}", kind="decision",
                   topic="procedure", source="user", importance=2)


# ------------------------------------------------------------------ proposals

def propose(
    con: sqlite3.Connection,
    kind: str,
    target: str,
    payload: dict[str, Any],
    *,
    rationale: str,
    evidence: str = "",
) -> int:
    """Queue a memory edit for your approval.

    kind: ``core`` | ``knowledge`` | ``belief`` | ``belief_update``
    payload modes for file kinds:
        {"mode": "append", "text": "..."}
        {"mode": "replace_section", "heading": "## X", "text": "..."}
        {"mode": "replace_file", "text": "..."}
    """
    if kind not in ("core", "knowledge", "belief", "belief_update", "rule", "belief_revision"):
        raise ValueError(f"unknown proposal kind: {kind}")
    if not rationale.strip():
        raise ValueError("a proposal needs a rationale — why should this be believed?")

    cur = con.execute(
        """INSERT INTO proposals(created_at, kind, target, rationale, payload, evidence, status)
           VALUES (?,?,?,?,?,?, 'pending')""",
        (db.now(), kind, target, rationale, json.dumps(payload), evidence),
    )
    con.commit()
    return int(cur.lastrowid)


def _target_path(kind: str, target: str) -> Path:
    if kind == "core":
        return config.CORE_MEMORY
    name = Path(target).name
    if not name.endswith(".md"):
        name += ".md"
    return config.KNOWLEDGE_DIR / name


def _apply_to_text(original: str, payload: dict[str, Any]) -> str:
    mode = payload.get("mode", "append")
    text = payload.get("text", "").rstrip() + "\n"

    if mode == "replace_file":
        return text

    if mode == "append":
        stamp = f"\n<!-- added {db.today()} -->\n"
        return original.rstrip() + "\n" + stamp + text

    if mode == "replace_section":
        heading = payload.get("heading", "").strip()
        if not heading:
            raise ValueError("replace_section needs a 'heading'")
        level = len(heading) - len(heading.lstrip("#"))
        pattern = re.compile(
            rf"^{re.escape(heading)}\s*$.*?(?=^#{{1,{max(level, 1)}}} |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        if pattern.search(original):
            return pattern.sub(heading + "\n\n" + text + "\n", original, count=1)
        return original.rstrip() + f"\n\n{heading}\n\n{text}"

    raise ValueError(f"unknown payload mode: {mode!r}")


def proposal_diff(con: sqlite3.Connection, proposal_id: int) -> str:
    row = con.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        return f"no proposal #{proposal_id}"
    payload = json.loads(row["payload"] or "{}")

    if row["kind"] in ("belief", "belief_update", "rule", "belief_revision"):
        return json.dumps(payload, indent=2)

    path = _target_path(row["kind"], row["target"])
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        updated = _apply_to_text(original, payload)
    except ValueError as e:
        return f"! invalid payload: {e}"

    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{path.name}", tofile=f"b/{path.name}", n=3,
    )
    return "".join(diff) or "(no change)"


def apply_proposal(con: sqlite3.Connection, proposal_id: int, note: str = "") -> dict[str, Any]:
    row = con.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise ValueError(f"no proposal #{proposal_id}")
    if row["status"] != "pending":
        raise ValueError(f"proposal #{proposal_id} is already {row['status']}")

    payload = json.loads(row["payload"] or "{}")
    result: dict[str, Any] = {"id": proposal_id, "kind": row["kind"], "target": row["target"]}

    if row["kind"] == "belief":
        belief_id = add_belief(
            con,
            payload["claim"],
            confidence=payload.get("confidence", "moderate"),
            topic=payload.get("topic", ""),
            rationale=row["rationale"],
            sources=payload.get("sources") or [{"citation": row["evidence"] or "see proposal"}],
        )
        result["belief_id"] = belief_id
        if payload.get("supersedes"):
            supersede_belief(con, int(payload["supersedes"]), belief_id, row["rationale"])
            result["superseded"] = payload["supersedes"]

    elif row["kind"] == "belief_revision":
        result["belief_id"] = revise_belief(
            con, int(row["target"]), payload["claim"],
            confidence=payload.get("confidence"),
            sources=payload.get("sources"),
            reason=row["rationale"],
        )
        result["superseded"] = int(row["target"])

    elif row["kind"] == "rule":
        result["rule_id"] = add_rule(
            con, payload["trigger"], payload["action"],
            scope=payload.get("scope", ""), source=f"proposal #{proposal_id}",
        )

    elif row["kind"] == "belief_update":
        fields, args = [], []
        for f in ("claim", "confidence", "topic", "rationale", "review_on", "status"):
            if f in payload:
                fields.append(f"{f}=?")
                args.append(payload[f])
        if fields:
            args += [db.now(), int(row["target"])]
            con.execute(
                f"UPDATE beliefs SET {', '.join(fields)}, updated_at=? WHERE id=?", args
            )
        for s in payload.get("add_sources", []) or []:
            con.execute(
                "INSERT INTO belief_sources(belief_id, paper_id, citation, url, stance)"
                " VALUES (?,?,?,?,?)",
                (int(row["target"]), s.get("paper_id"), s.get("citation", ""),
                 s.get("url", ""), s.get("stance", "supports")),
            )

    else:  # core | knowledge
        path = _target_path(row["kind"], row["target"])
        path.parent.mkdir(parents=True, exist_ok=True)
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = _apply_to_text(original, payload)
        path.write_text(updated, encoding="utf-8")
        result["path"] = str(path)
        result["bytes"] = len(updated)
        # Knowledge changed, so its chunks are stale.
        con.execute("DELETE FROM chunks WHERE doc_ref=?", (f"knowledge/{path.name}",))

    con.execute(
        "UPDATE proposals SET status='applied', decided_at=?, decided_note=? WHERE id=?",
        (db.now(), note, proposal_id),
    )
    con.commit()
    return result


def reject_proposal(con: sqlite3.Connection, proposal_id: int, note: str = "") -> None:
    con.execute(
        "UPDATE proposals SET status='rejected', decided_at=?, decided_note=? WHERE id=?",
        (db.now(), note, proposal_id),
    )
    con.commit()


def pending_proposals(con: sqlite3.Connection) -> list[dict]:
    return db.rows_to_dicts(
        con.execute("SELECT * FROM proposals WHERE status='pending' ORDER BY id").fetchall()
    )


# ------------------------------------------------------------------ sessions

def start_session(con: sqlite3.Connection, topic: str = "") -> int:
    cur = con.execute(
        "INSERT INTO sessions(started_at, topic) VALUES (?,?)", (db.now(), topic)
    )
    con.commit()
    sid = int(cur.lastrowid)
    journal.record(con, f"Session #{sid} started: {topic or 'untitled'}",
                   kind="session", topic=topic, source="agent", session_id=sid)
    return sid


def end_session(
    con: sqlite3.Connection,
    session_id: int,
    *,
    summary: str,
    decisions: str = "",
    open_threads: str = "",
) -> None:
    con.execute(
        """UPDATE sessions SET ended_at=?, summary=?, decisions=?, open_threads=?
           WHERE id=?""",
        (db.now(), summary, decisions, open_threads, session_id),
    )
    con.commit()
    # Mirror the session record into the journal so it survives in the
    # append-only tier too — the sessions table is editable, the journal is not.
    journal.record(con, f"Session #{session_id} summary: {summary}",
                   kind="session", source="agent", session_id=session_id, importance=3)
    if decisions:
        journal.record(con, decisions, kind="decision", source="agent",
                       session_id=session_id, importance=4)
    if open_threads:
        journal.record(con, open_threads, kind="question", source="agent",
                       session_id=session_id, importance=3)


def recent_sessions(con: sqlite3.Connection, n: int = 5) -> list[dict]:
    return db.rows_to_dicts(
        con.execute(
            "SELECT * FROM sessions WHERE summary IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
    )


# --------------------------------------------------------------------- brief

def brief(
    con: sqlite3.Connection | None = None,
    cfg: config.Config | None = None,
    *,
    include_core: bool = True,
    digest_chars: int = 3500,
    max_sessions: int = 3,
) -> str:
    """The startup payload. Bounded on purpose.

    Everything the agent needs to resume work, and nothing it can retrieve on
    demand. If this ever grows past a few thousand tokens, something belongs in
    `knowledge/` instead.
    """
    cfg = cfg or config.load()
    close_after = con is None
    con = con or db.connect()

    parts: list[str] = [f"# NeoBrain session brief — {db.today()}", ""]

    if include_core:
        core = read_core()
        if core.strip():
            parts += ["## Core memory (loaded in full)", "", core.strip(), ""]
        else:
            parts += [
                "## Core memory",
                "",
                "`memory/CORE.md` is empty. Ask me the questions in it before "
                "giving advice that depends on my level, lab access, or compute.",
                "",
            ]

    s = db.stats(con)
    parts += [
        "## Corpus state",
        "",
        f"- {s['papers']:,} papers ({s['papers_oa']:,} open access, {s['fulltext']:,} with full text), "
        f"{s['chunks']:,} indexed passages"
        + (f", {s['embeddings']:,} embedded" if s["embeddings"] else " (keyword search only)"),
        f"- {s['entities']:,} entities, {s['entity_edges']:,} graph edges",
        f"- {s['trials']:,} trials tracked, {s['trial_changes']:,} recorded status changes",
        f"- {s['beliefs']:,} active beliefs ({s['beliefs_due']} due for re-check), "
        f"{s['journal']:,} journal entries since {s['journal_since'] or 'today'}",
        f"- last sweep: {s['last_sweep'] or 'never — run `neobrain sweep --days 30`'}",
        "",
    ]

    # Procedural memory comes before everything else: these are the standing
    # instructions I learned from you, and they should shape the whole session.
    rules = get_rules(con)
    if rules:
        parts += ["## Rules I have learned from you (procedural memory)", ""]
        for r in rules[:15]:
            parts.append(f"- **When** {r['trigger']} → {r['action']}")
        parts.append("")

    marks = journal.timeline(con, limit=8)
    if marks:
        parts += [
            "## Recent corrections, decisions and preferences (episodic)",
            "",
        ]
        for m in marks:
            parts.append(f"- _{m['at'][:10]}_ **{m['kind']}**: {' '.join(m['text'].split())[:220]}")
        parts += ["", "These are recorded verbatim and are never deleted. "
                      "Search further back with the `recall` tool.", ""]

    open_conflicts = con.execute(
        """SELECT c.id, c.cue, b.claim FROM conflicts c
           JOIN beliefs b ON b.id=c.belief_id
           WHERE c.status='open' ORDER BY c.id DESC LIMIT 8"""
    ).fetchall()
    if open_conflicts:
        parts += [
            "## Evidence that may contradict what we believe",
            "",
        ]
        for c in open_conflicts:
            parts.append(f"- `#{c['id']}` on _{c['claim'][:110]}_ — {c['cue'][:150]}")
        parts += ["", "These are **candidate** contradictions from an imprecise "
                      "detector, not verdicts. Read the passage before acting.", ""]

    latest = digest.latest(1)
    if latest:
        text = latest[0].read_text(encoding="utf-8")
        if len(text) > digest_chars:
            text = text[:digest_chars] + f"\n\n…(truncated — full digest: {latest[0]})"
        parts += [f"## Latest digest ({latest[0].stem})", "", text, ""]

    pend = pending_proposals(con)
    if pend:
        parts += ["## Awaiting my approval", ""]
        for p in pend[:10]:
            parts.append(f"- `#{p['id']}` {p['kind']} → {p['target']}: {p['rationale'][:160]}")
        parts += ["", "Do not treat these as accepted knowledge until I approve them.", ""]

    due = get_beliefs(con, due_only=True, limit=10)
    if due:
        parts += ["## Beliefs due for re-check", ""]
        for b in due:
            parts.append(f"- `#{b['id']}` [{b['confidence']}] {b['claim'][:180]}")
        parts.append("")

    sessions = recent_sessions(con, max_sessions)
    if sessions:
        parts += ["## Where we left off", ""]
        for sess in sessions:
            parts.append(f"**{sess['started_at'][:10]} — {sess.get('topic') or 'untitled'}**")
            if sess.get("summary"):
                parts.append(sess["summary"])
            if sess.get("open_threads"):
                parts.append(f"_Open:_ {sess['open_threads']}")
            parts.append("")

    files = list_knowledge()
    if files:
        parts += [
            "## Knowledge files (retrieve on demand — do not read all of them)",
            "",
            ", ".join(f"`{n}`" for n, _ in files),
            "",
        ]

    cards_due = s["cards_due"]
    if cards_due:
        parts += [f"## Teaching", "", f"{cards_due} review cards are due (`neobrain quiz`).", ""]

    parts += [
        "---",
        "## Operating rules for this session",
        "",
        "1. Every factual claim gets a source I can open. If the corpus does not "
        "have it, say so — do not fill the gap from memory.",
        "2. Label each claim: established consensus / contested / single-paper.",
        "3. Retrieve before answering domain questions: `neobrain ask \"...\"` or "
        "the `search` tool. Do not answer from this brief alone.",
        "4. Propose memory edits at the end of the session; never write to "
        "`memory/` or `knowledge/` directly.",
        "5. Push back when I am about to do something methodologically weak.",
    ]

    if close_after:
        con.close()
    return "\n".join(parts)
