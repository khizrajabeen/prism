"""The teaching layer: a curriculum plus spaced repetition over your own corpus.

Two distinct things, deliberately separated:

**Curriculum** (`config/curriculum.yaml`) — an ordered path through the field,
from immunology fundamentals to running your own pipeline. Each module names
the knowledge file it draws on, what you should be able to *do* at the end, and
a checkpoint that is a task rather than a quiz question. Reading a module is
not learning it; producing the artifact is.

**Spaced repetition** (`cards` table) — SM-2 scheduling for the facts that have
to be instantly available: HLA nomenclature, peptide length ranges, which mouse
line is on which background, what an agretopicity index is. These are the
things you cannot look up mid-conversation with a supervisor.

The agent adds cards as you work — when you get something wrong, or when a
paper introduces a fact you will need again — so the deck grows out of your
actual research rather than a generic list.
"""

from __future__ import annotations

import datetime as dt
import random
import sqlite3
from pathlib import Path
from typing import Any

from . import config, db, retrieve

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


# --------------------------------------------------------------------- cards

def add_card(
    con: sqlite3.Connection,
    front: str,
    back: str,
    *,
    topic: str = "",
    source: str = "",
    due_today: bool = True,
) -> int:
    existing = con.execute(
        "SELECT id FROM cards WHERE front=? AND topic=?", (front.strip(), topic)
    ).fetchone()
    if existing:
        return int(existing["id"])
    cur = con.execute(
        """INSERT INTO cards(front, back, topic, source, created_at, due_on)
           VALUES (?,?,?,?,?,?)""",
        (front.strip(), back.strip(), topic, source, db.now(),
         db.today() if due_today else None),
    )
    con.commit()
    return int(cur.lastrowid)


def due_cards(con: sqlite3.Connection, limit: int = 20, topic: str | None = None) -> list[dict]:
    sql = """SELECT * FROM cards
             WHERE suspended=0 AND (due_on IS NULL OR due_on<=?)"""
    args: list[Any] = [db.today()]
    if topic:
        sql += " AND topic LIKE ?"
        args.append(f"%{topic}%")
    sql += " ORDER BY COALESCE(due_on,'0000'), RANDOM() LIMIT ?"
    args.append(limit)
    return db.rows_to_dicts(con.execute(sql, args).fetchall())


def grade_card(con: sqlite3.Connection, card_id: int, grade: int) -> dict[str, Any]:
    """SM-2 scheduling. grade 0-5; below 3 is a lapse and resets the interval.

    The intervals are the standard SuperMemo-2 ones: 1 day, 6 days, then
    interval × ease. Ease drifts down for cards you keep failing, so hard
    material comes back more often without you managing it.
    """
    row = con.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    if row is None:
        raise ValueError(f"no card #{card_id}")
    grade = max(0, min(5, int(grade)))

    ease = float(row["ease"] or 2.5)
    interval = int(row["interval"] or 0)
    reps = int(row["reps"] or 0)
    lapses = int(row["lapses"] or 0)

    if grade < 3:
        reps = 0
        interval = 1
        lapses += 1
    else:
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            interval = max(1, round(interval * ease))
    ease = max(1.3, ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02)))

    due = (dt.date.today() + dt.timedelta(days=interval)).isoformat()
    con.execute(
        """UPDATE cards SET ease=?, interval=?, due_on=?, reps=?, lapses=?, last_grade=?
           WHERE id=?""",
        (round(ease, 3), interval, due, reps, lapses, grade, card_id),
    )
    con.commit()
    return {"id": card_id, "interval": interval, "due_on": due, "ease": round(ease, 2),
            "lapses": lapses}


def deck_stats(con: sqlite3.Connection) -> dict[str, Any]:
    rows = con.execute(
        """SELECT topic, COUNT(*) n,
                  SUM(CASE WHEN due_on IS NULL OR due_on<=? THEN 1 ELSE 0 END) due,
                  AVG(ease) ease, SUM(lapses) lapses
           FROM cards WHERE suspended=0 GROUP BY topic ORDER BY n DESC""",
        (db.today(),),
    ).fetchall()
    return {
        "by_topic": [dict(r) for r in rows],
        "total": sum(r["n"] for r in rows),
        "due": sum(r["due"] or 0 for r in rows),
        "weakest": [r["topic"] for r in sorted(rows, key=lambda r: r["ease"] or 2.5)[:3]],
    }


# ---------------------------------------------------------------- curriculum

CURRICULUM_PATH = config.CONFIG_DIR / "curriculum.yaml"


def load_curriculum() -> dict[str, Any]:
    if yaml is None or not CURRICULUM_PATH.exists():
        return {}
    return yaml.safe_load(CURRICULUM_PATH.read_text(encoding="utf-8")) or {}


def module_by_id(mid: str) -> dict[str, Any] | None:
    cur = load_curriculum()
    for m in cur.get("modules", []) or []:
        if str(m.get("id")) == str(mid):
            return m
    return None


def lesson_plan(
    topic_or_module: str,
    *,
    con: sqlite3.Connection | None = None,
    cfg: config.Config | None = None,
    k: int = 8,
) -> str:
    """Assemble a teaching packet: the module, your notes, and current evidence.

    This is not the lesson — the model writes the lesson. This is the grounded
    material it must teach *from*, so the teaching is anchored in your corpus
    and every claim in it stays traceable.
    """
    cfg = cfg or config.load()
    close_after = con is None
    con = con or db.connect()

    module = module_by_id(topic_or_module)
    topic = module["title"] if module else topic_or_module

    parts = [f"# Teaching packet — {topic}", ""]

    if module:
        parts += [
            f"**Module {module.get('id')}: {module.get('title')}** "
            f"(level: {module.get('level', 'n/a')})",
            "",
            f"Goal: {module.get('goal', '')}",
            "",
            "You should be able to, at the end:",
            *[f"- {o}" for o in module.get("outcomes", []) or []],
            "",
            f"Checkpoint (a task, not a quiz): {module.get('checkpoint', '')}",
            "",
        ]
        for note in module.get("reads", []) or []:
            path = config.KNOWLEDGE_DIR / note
            if path.exists():
                text = path.read_text(encoding="utf-8")
                parts += [f"## From `knowledge/{note}`", "", text[:6000], ""]

    parts += ["## Current evidence from the corpus", ""]
    hits = retrieve.search(topic, k=k, cfg=cfg, con=con)
    if hits:
        for i, h in enumerate(hits, 1):
            parts += [
                f"### [{i}] {h.title or h.doc_ref}",
                f"{h.citation()}" + (f" · {h.url}" if h.url else ""),
                "",
                h.text[:1200],
                "",
            ]
    else:
        parts += [
            "_Nothing in the corpus on this topic yet._ Say so plainly rather "
            "than teaching from general knowledge as if it were sourced, and "
            "offer to widen `config/interests.yaml`.",
            "",
        ]

    parts += [
        "---",
        "## How to teach this",
        "",
        "1. Start from what I already know — ask one diagnostic question first.",
        "2. Teach the *mechanism*, not the vocabulary. I should be able to "
        "predict what happens when a variable changes.",
        "3. Use the evidence above; cite it. Flag where the field disagrees.",
        "4. Give me the checkpoint task. Do not accept a verbal answer as proof.",
        "5. Propose 3–8 spaced-repetition cards for the facts I will need again "
        "(`neobrain card add`), and say which ones you added.",
    ]

    if close_after:
        con.close()
    return "\n".join(parts)


def progress(con: sqlite3.Connection) -> str:
    cur = load_curriculum()
    modules = cur.get("modules", []) or []
    if not modules:
        return "No curriculum found at config/curriculum.yaml."

    lines = ["# Curriculum progress", ""]
    stats = deck_stats(con)
    by_topic = {r["topic"]: r for r in stats["by_topic"]}

    for m in modules:
        topic = m.get("topic") or m.get("id")
        row = by_topic.get(topic, {})
        n, due = row.get("n", 0), row.get("due", 0)
        ease = row.get("ease")
        state = "not started" if not n else f"{n} cards, {due} due" + (
            f", ease {ease:.2f}" if ease else ""
        )
        lines.append(f"- **{m.get('id')} {m.get('title')}** — {state}")
    lines += ["", f"Deck: {stats['total']} cards, {stats['due']} due.",
              f"Weakest topics: {', '.join(t for t in stats['weakest'] if t) or '—'}"]
    return "\n".join(lines)


def seed_from_curriculum(con: sqlite3.Connection) -> int:
    """Load the starter cards shipped with the curriculum file."""
    cur = load_curriculum()
    added = 0
    for m in cur.get("modules", []) or []:
        topic = m.get("topic") or m.get("id")
        for card in m.get("cards", []) or []:
            if isinstance(card, dict) and card.get("q") and card.get("a"):
                before = con.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"]
                add_card(con, card["q"], card["a"], topic=topic,
                         source=f"curriculum:{m.get('id')}")
                after = con.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"]
                added += after - before
    return added
