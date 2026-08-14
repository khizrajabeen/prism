"""The journal: append-only episodic memory.

This is the answer to "once it learns something, it must not forget it."

Every observation, correction, decision, preference and result is written here
**immediately, without approval**, from the very first run. The storage layer
enforces immutability — the `journal_no_update` and `journal_no_delete`
triggers abort any attempt to rewrite it — so nothing in this system, agent or
human, can quietly revise what was recorded.

Why this is separate from the approval gate
-------------------------------------------
Those two requirements look contradictory: capture everything automatically,
but never let the agent write to memory unreviewed. They are reconciled by
splitting *recording* from *believing*.

    journal   raw, immediate, immutable, never forgotten, not authoritative
    beliefs   curated, sourced, reviewed, authoritative, versioned not deleted
    knowledge prose you approved, the thing you would cite in a thesis

An unreviewed journal entry can never be cited as fact — it is a record that
something was said or seen, timestamped. Promotion from journal to belief is
where judgement enters, and that still goes through `neobrain review`.

The practical consequence: you can tell the agent "I don't use montanide, it
sequesters T cells at the injection site" once, and it is on disk forever, in
the exact words you used, retrievable in three years.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from . import db

KINDS = (
    "observation",   # something noticed in the literature or the data
    "correction",    # you corrected the agent, or the agent corrected itself
    "decision",      # a choice made, with its reason
    "preference",    # how you want to work
    "result",        # an experimental or analytical outcome
    "question",      # something to resolve later
    "error",         # something that went wrong, so it is not repeated
    "session",       # session boundaries and summaries
    "sweep",         # what the nightly job did
    "system",        # configuration and maintenance events
)


def record(
    con: sqlite3.Connection,
    text: str,
    *,
    kind: str = "observation",
    topic: str = "",
    source: str = "user",
    session_id: int | None = None,
    entities: str = "",
    importance: int = 1,
) -> int:
    """Append one entry. Never fails on a duplicate — repetition is signal."""
    if not text or not text.strip():
        raise ValueError("refusing to journal an empty entry")
    if kind not in KINDS:
        kind = "observation"
    cur = con.execute(
        """INSERT INTO journal(at, kind, topic, text, source, session_id, entities, importance)
           VALUES (?,?,?,?,?,?,?,?)""",
        (db.now(), kind, topic, text.strip(), source, session_id, entities,
         max(1, min(5, int(importance)))),
    )
    con.commit()
    return int(cur.lastrowid)


def record_many(con: sqlite3.Connection, entries: Iterable[dict[str, Any]]) -> int:
    n = 0
    for e in entries:
        try:
            record(con, e.pop("text"), **e)
            n += 1
        except (ValueError, KeyError):
            continue
    return n


def recall(
    con: sqlite3.Connection,
    query: str = "",
    *,
    kind: str | None = None,
    topic: str | None = None,
    since: str | None = None,
    limit: int = 25,
) -> list[dict]:
    """Search episodic memory. Empty query returns the most recent entries."""
    if query.strip():
        sql = """SELECT j.* FROM journal_fts
                 JOIN journal j ON j.id = journal_fts.rowid
                 WHERE journal_fts MATCH ?"""
        args: list[Any] = [db.fts_escape(query)]
        order = " ORDER BY bm25(journal_fts), j.at DESC"
    else:
        sql = "SELECT j.* FROM journal j WHERE 1=1"
        args = []
        order = " ORDER BY j.at DESC"

    if kind:
        sql += " AND j.kind = ?"
        args.append(kind)
    if topic:
        sql += " AND j.topic LIKE ?"
        args.append(f"%{topic}%")
    if since:
        sql += " AND j.at >= ?"
        args.append(since)

    args.append(limit)
    try:
        rows = con.execute(sql + order + " LIMIT ?", args).fetchall()
    except sqlite3.OperationalError:
        return []
    return db.rows_to_dicts(rows)


def timeline(con: sqlite3.Connection, limit: int = 20, min_importance: int = 2) -> list[dict]:
    """The entries worth re-reading: corrections, decisions, preferences, errors."""
    return db.rows_to_dicts(con.execute(
        """SELECT * FROM journal
           WHERE importance >= ? OR kind IN ('correction','decision','preference','error')
           ORDER BY at DESC LIMIT ?""",
        (min_importance, limit),
    ).fetchall())


def stats(con: sqlite3.Connection) -> dict[str, Any]:
    rows = con.execute(
        "SELECT kind, COUNT(*) n FROM journal GROUP BY kind ORDER BY n DESC"
    ).fetchall()
    first = con.execute("SELECT MIN(at) a FROM journal").fetchone()
    return {
        "total": sum(r["n"] for r in rows),
        "by_kind": {r["kind"]: r["n"] for r in rows},
        "since": first["a"] if first else None,
    }


def format_entry(e: dict, width: int = 100) -> str:
    head = f"[{e['at'][:16]}] {e['kind']}"
    if e.get("topic"):
        head += f" · {e['topic']}"
    if e.get("source") and e["source"] != "user":
        head += f" · {e['source']}"
    body = " ".join(e["text"].split())
    if len(body) > width * 3:
        body = body[: width * 3] + "…"
    return f"{head}\n    {body}"
