"""The work ledger: projects, hypotheses, experiments, decisions.

This is the spine the rest of the system hangs from.

Everything else NeoBrain does — sweeping, retrieval, the graph, the belief
store — is in service of a loop that researchers actually run:

    question → evidence → hypothesis → prediction → experiment → result
             → belief update → next question

Without this module, NeoBrain was a very good literature tool with a memory. A
literature tool does not know what you are trying to find out, so it cannot
tell you that the paper it just surfaced bears on hypothesis #3, or that you
have three experiments running and none of them tests the thing you say is your
main question.

Two design choices carry most of the value:

**Predictions are recorded before results.** ``experiments.prediction`` is
written at planning time and the schema keeps ``predicted_at`` separate from
``ended_at``. This is pre-registration at the scale of one bench scientist. It
costs thirty seconds and it is the difference between "we found what we
expected" and knowing whether you actually did.

**Hypotheses require a falsifier.** A hypothesis you cannot imagine
disconfirming is not a hypothesis, it is a belief, and it belongs in the belief
store where it will be reviewed against the literature instead of quietly
steering your experiments.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import db, journal

HYPOTHESIS_STATUS = ("open", "supported", "refuted", "inconclusive", "abandoned")
EXPERIMENT_STATUS = ("planned", "running", "done", "abandoned")
OUTCOMES = ("as_predicted", "contradicted", "ambiguous", "failed")


# ---------------------------------------------------------------- projects

def create_project(con: sqlite3.Connection, name: str, question: str = "",
                   deadline: str = "") -> int:
    existing = con.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()
    if existing:
        return int(existing["id"])
    cur = con.execute(
        """INSERT INTO projects(name, question, status, started_at, updated_at, deadline)
           VALUES (?,?, 'active', ?,?,?)""",
        (name, question, db.now(), db.now(), deadline or None),
    )
    con.commit()
    pid = int(cur.lastrowid)
    journal.record(con, f"Project '{name}' opened: {question}", kind="decision",
                   topic=name, source="science", importance=3)
    return pid


def get_projects(con: sqlite3.Connection, status: str | None = "active") -> list[dict]:
    sql = "SELECT * FROM projects"
    args: list[Any] = []
    if status:
        sql += " WHERE status=?"
        args.append(status)
    return db.rows_to_dicts(con.execute(sql + " ORDER BY updated_at DESC", args).fetchall())


def project_by_name(con: sqlite3.Connection, name: str) -> dict | None:
    row = con.execute("SELECT * FROM projects WHERE name=? OR id=?",
                      (name, name if str(name).isdigit() else -1)).fetchone()
    return dict(row) if row else None


def _touch(con: sqlite3.Connection, project_id: int | None) -> None:
    if project_id:
        con.execute("UPDATE projects SET updated_at=? WHERE id=?", (db.now(), project_id))


# -------------------------------------------------------------- hypotheses

def add_hypothesis(
    con: sqlite3.Connection,
    statement: str,
    *,
    project_id: int | None = None,
    rationale: str = "",
    falsifier: str = "",
    prior: str = "",
) -> int:
    """Record a hypothesis. A falsifier is required, deliberately."""
    if not falsifier.strip():
        raise ValueError(
            "a hypothesis needs a falsifier — what observation would make you "
            "abandon it? If you cannot name one, this is a belief rather than a "
            "hypothesis: record it with `neobrain belief add`, where it will be "
            "reviewed against the literature."
        )
    cur = con.execute(
        """INSERT INTO hypotheses(project_id, statement, rationale, status,
                                  falsifier, prior, created_at)
           VALUES (?,?,?, 'open', ?,?,?)""",
        (project_id, statement.strip(), rationale, falsifier.strip(), prior, db.now()),
    )
    _touch(con, project_id)
    con.commit()
    hid = int(cur.lastrowid)
    journal.record(con, f"Hypothesis #{hid}: {statement.strip()} "
                        f"(falsified by: {falsifier.strip()})",
                   kind="decision", source="science", importance=3)
    return hid


def resolve_hypothesis(
    con: sqlite3.Connection,
    hypothesis_id: int,
    status: str,
    *,
    resolution: str = "",
    posterior: str = "",
) -> None:
    if status not in HYPOTHESIS_STATUS:
        raise ValueError(f"status must be one of {HYPOTHESIS_STATUS}")
    row = con.execute("SELECT statement, project_id FROM hypotheses WHERE id=?",
                      (hypothesis_id,)).fetchone()
    if row is None:
        raise ValueError(f"no hypothesis #{hypothesis_id}")
    con.execute(
        """UPDATE hypotheses SET status=?, resolution=?, posterior=?, resolved_at=?
           WHERE id=?""",
        (status, resolution, posterior, db.now(), hypothesis_id),
    )
    _touch(con, row["project_id"])
    con.commit()
    journal.record(con, f"Hypothesis #{hypothesis_id} → {status}: {resolution}",
                   kind="result", source="science", importance=4)


def get_hypotheses(con: sqlite3.Connection, *, project_id: int | None = None,
                   status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM hypotheses WHERE 1=1"
    args: list[Any] = []
    if project_id:
        sql += " AND project_id=?"
        args.append(project_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    rows = db.rows_to_dicts(con.execute(sql + " ORDER BY id DESC", args).fetchall())
    for h in rows:
        h["experiments"] = db.rows_to_dicts(con.execute(
            "SELECT id, title, status, outcome FROM experiments WHERE hypothesis_id=?",
            (h["id"],),
        ).fetchall())
        h["evidence"] = evidence_for(con, "hypothesis", h["id"])
    return rows


# ------------------------------------------------------------- experiments

def plan_experiment(
    con: sqlite3.Connection,
    title: str,
    *,
    project_id: int | None = None,
    hypothesis_id: int | None = None,
    design: str = "",
    prediction: str = "",
) -> int:
    """Register an experiment **with its prediction**, before it runs."""
    if not prediction.strip():
        raise ValueError(
            "record the prediction before the result. What do you expect to see, "
            "specifically enough that the actual result could contradict it? "
            "Without this on the record, a surprising result will be "
            "reinterpreted as expected, and you will not notice."
        )
    cur = con.execute(
        """INSERT INTO experiments(project_id, hypothesis_id, title, design,
                                   prediction, predicted_at, status)
           VALUES (?,?,?,?,?,?, 'planned')""",
        (project_id, hypothesis_id, title.strip(), design, prediction.strip(), db.now()),
    )
    _touch(con, project_id)
    con.commit()
    eid = int(cur.lastrowid)
    journal.record(con, f"Experiment #{eid} planned: {title.strip()} — predicted: "
                        f"{prediction.strip()}",
                   kind="decision", source="science", importance=3)
    return eid


def record_result(
    con: sqlite3.Connection,
    experiment_id: int,
    result: str,
    *,
    outcome: str = "ambiguous",
    surprise: int = 0,
    tools: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Record what actually happened, alongside what was predicted."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")
    row = con.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
    if row is None:
        raise ValueError(f"no experiment #{experiment_id}")

    con.execute(
        """UPDATE experiments SET result=?, outcome=?, surprise=?, status='done',
           ended_at=?, tools=COALESCE(?, tools) WHERE id=?""",
        (result, outcome, max(0, min(5, int(surprise))), db.now(),
         json.dumps(tools) if tools else None, experiment_id),
    )
    _touch(con, row["project_id"])
    con.commit()

    journal.record(
        con,
        f"Experiment #{experiment_id} ({row['title']}) → {outcome}. "
        f"Predicted: {row['prediction']}. Observed: {result}",
        kind="result", source="science", importance=4 if outcome == "contradicted" else 3,
    )
    return {
        "id": experiment_id,
        "predicted": row["prediction"],
        "observed": result,
        "outcome": outcome,
        "prompt": (
            "This contradicted the prediction. Before explaining it away: is the "
            "hypothesis wrong, or was the experiment unable to test it? Record "
            "which, and update the hypothesis."
            if outcome == "contradicted" else
            "Matched the prediction. Check the design could have produced a "
            "different answer — an experiment that could only confirm has not "
            "tested anything."
        ),
    }


def get_experiments(con: sqlite3.Connection, *, project_id: int | None = None,
                    status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM experiments WHERE 1=1"
    args: list[Any] = []
    if project_id:
        sql += " AND project_id=?"
        args.append(project_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    return db.rows_to_dicts(con.execute(sql + " ORDER BY id DESC", args).fetchall())


# --------------------------------------------------------------- decisions

def record_decision(
    con: sqlite3.Connection,
    decision: str,
    *,
    project_id: int | None = None,
    rationale: str = "",
    alternatives: str = "",
    would_revisit_if: str = "",
    review_on: str = "",
) -> int:
    cur = con.execute(
        """INSERT INTO decisions(project_id, decision, rationale, alternatives,
                                 would_revisit_if, decided_at, review_on, status)
           VALUES (?,?,?,?,?,?,?, 'standing')""",
        (project_id, decision.strip(), rationale, alternatives, would_revisit_if,
         db.now(), review_on or None),
    )
    _touch(con, project_id)
    con.commit()
    did = int(cur.lastrowid)
    journal.record(con, f"Decision #{did}: {decision.strip()} — because {rationale}",
                   kind="decision", source="science", importance=4)
    return did


def get_decisions(con: sqlite3.Connection, project_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM decisions"
    args: list[Any] = []
    if project_id:
        sql += " WHERE project_id=?"
        args.append(project_id)
    return db.rows_to_dicts(con.execute(sql + " ORDER BY id DESC", args).fetchall())


# ----------------------------------------------------------------- linking

def link_evidence(
    con: sqlite3.Connection,
    kind: str,
    item_id: int,
    *,
    paper_id: str | None = None,
    chunk_id: int | None = None,
    belief_id: int | None = None,
    stance: str = "supports",
    note: str = "",
) -> int:
    cur = con.execute(
        """INSERT INTO evidence_links(kind, item_id, paper_id, chunk_id, belief_id,
                                      stance, note, linked_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (kind, item_id, paper_id, chunk_id, belief_id, stance, note, db.now()),
    )
    con.commit()
    return int(cur.lastrowid)


def evidence_for(con: sqlite3.Connection, kind: str, item_id: int) -> list[dict]:
    return db.rows_to_dicts(con.execute(
        """SELECT e.*, p.title, p.url, p.evidence_tier
           FROM evidence_links e LEFT JOIN papers p ON p.id = e.paper_id
           WHERE e.kind=? AND e.item_id=? ORDER BY e.id""",
        (kind, item_id),
    ).fetchall())


# ------------------------------------------------------------------ status

def project_status(con: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    """Everything about one project, in the shape a supervisor would ask for."""
    proj = con.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if proj is None:
        return {}
    hyps = get_hypotheses(con, project_id=project_id)
    exps = get_experiments(con, project_id=project_id)
    return {
        "project": dict(proj),
        "hypotheses": hyps,
        "experiments": exps,
        "decisions": get_decisions(con, project_id),
        "counts": {
            "open_hypotheses": sum(1 for h in hyps if h["status"] == "open"),
            "running": sum(1 for e in exps if e["status"] == "running"),
            "planned": sum(1 for e in exps if e["status"] == "planned"),
            "awaiting_result": sum(1 for e in exps
                                   if e["status"] in ("running", "planned") and not e["result"]),
            "surprises": sum(1 for e in exps if e["outcome"] == "contradicted"),
        },
    }


def whats_next(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """What the work actually needs from you, across all projects.

    Ordered by how much it is costing to leave undone: an experiment finished
    without a recorded result is the worst, because the memory of what happened
    decays fastest and the prediction is sitting there unresolved.
    """
    items: list[dict[str, Any]] = []

    for e in db.rows_to_dicts(con.execute(
        """SELECT e.*, p.name AS project FROM experiments e
           LEFT JOIN projects p ON p.id = e.project_id
           WHERE e.status='running' ORDER BY e.started_at""").fetchall()):
        items.append({
            "priority": 1, "kind": "record result",
            "what": f"#{e['id']} {e['title']} is running",
            "why": f"predicted: {e['prediction']}",
            "project": e.get("project"),
        })

    for h in db.rows_to_dicts(con.execute(
        """SELECT h.*, p.name AS project FROM hypotheses h
           LEFT JOIN projects p ON p.id = h.project_id
           WHERE h.status='open'
             AND NOT EXISTS (SELECT 1 FROM experiments x WHERE x.hypothesis_id = h.id)
           ORDER BY h.id""").fetchall()):
        items.append({
            "priority": 2, "kind": "design an experiment",
            "what": f"hypothesis #{h['id']}: {h['statement']}",
            "why": "open, with nothing testing it",
            "project": h.get("project"),
        })

    for e in db.rows_to_dicts(con.execute(
        "SELECT * FROM experiments WHERE status='planned' ORDER BY id").fetchall()):
        items.append({
            "priority": 3, "kind": "run or drop",
            "what": f"#{e['id']} {e['title']} planned but not started",
            "why": "planned experiments that never start are a planning problem",
            "project": None,
        })

    for d in db.rows_to_dicts(con.execute(
        "SELECT * FROM decisions WHERE status='standing' AND review_on IS NOT NULL "
        "AND review_on <= ? ORDER BY review_on", (db.today(),)).fetchall()):
        items.append({
            "priority": 4, "kind": "revisit a decision",
            "what": d["decision"], "why": f"review due {d['review_on']}",
            "project": None,
        })

    items.sort(key=lambda i: i["priority"])
    return items


def stats(con: sqlite3.Connection) -> dict[str, Any]:
    def one(sql, *a):
        r = con.execute(sql, a).fetchone()
        return r[0] if r else 0

    return {
        "projects": one("SELECT COUNT(*) FROM projects WHERE status='active'"),
        "hypotheses_open": one("SELECT COUNT(*) FROM hypotheses WHERE status='open'"),
        "hypotheses_resolved": one(
            "SELECT COUNT(*) FROM hypotheses WHERE status IN ('supported','refuted')"),
        "experiments_running": one("SELECT COUNT(*) FROM experiments WHERE status='running'"),
        "awaiting_result": one(
            "SELECT COUNT(*) FROM experiments WHERE status='running' AND result IS NULL"),
        "surprises": one("SELECT COUNT(*) FROM experiments WHERE outcome='contradicted'"),
        "decisions": one("SELECT COUNT(*) FROM decisions WHERE status='standing'"),
    }
