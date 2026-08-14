"""MCP server — the brain's tool interface for any agent.

Point Claude Desktop, Claude Code, or any MCP client at this and the agent gets
the corpus, the memory, and the teaching layer as first-class tools instead of
having to shell out.

One asymmetry is deliberate and load-bearing: the agent can **propose** memory
edits but cannot **apply** them. There is no `apply_proposal` tool here. That
gate is yours alone, through `neobrain review` in a terminal. An agent that can
approve its own belief changes has no drift guard at all, and the failure mode
is silent — it tells you something wrong months later with total confidence and
no trail back to where it went wrong.

Run:
    neobrain mcp

Claude Desktop config (`claude_desktop_config.json`):
    {"mcpServers": {"neobrain": {"command": "neobrain", "args": ["mcp"],
                                 "env": {"NEOBRAIN_HOME": "/path/to/neobrain"}}}}

Claude Code:
    claude mcp add neobrain -- neobrain mcp
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import config, db, digest, evidence, graph, journal, memory, retrieve, tutor
from .sources import fulltext as ft_mod

# The SDK renamed its high-level server class in 2.0 (FastMCP → MCPServer).
# Both take (name, instructions), expose a `.tool()` decorator, and run over
# stdio, so one import shim covers both and nothing below has to care.
try:
    from mcp.server.mcpserver import MCPServer as _ServerClass  # SDK >= 2.0
except ImportError:  # pragma: no cover
    try:
        from mcp.server.fastmcp import FastMCP as _ServerClass  # SDK 1.x
    except ImportError:
        _ServerClass = None  # type: ignore


def _server():
    if _ServerClass is None:
        raise SystemExit(
            "The MCP SDK is not installed.\n"
            "  pip install 'mcp[cli]'\n"
            "Everything else in NeoBrain works without it — the CLI is the "
            "fallback interface."
        )

    mcp = _ServerClass(
        "neobrain",
        instructions=(
            "NeoBrain is the user's local research brain for neoantigen cancer "
            "vaccines and drug discovery. Call `brief` at the start of every "
            "session before answering anything. Ground domain claims in "
            "`evidence` results and cite paper ids. Never assert a quantitative "
            "result without a source. Propose memory edits at session end with "
            "`propose_memory_edit`; you cannot apply them — the user approves "
            "them in a terminal."
        ),
    )

    # ------------------------------------------------------------- memory
    @mcp.tool()
    def brief() -> str:
        """Read this FIRST, every session.

        Returns core memory, corpus state, the latest research digest, pending
        memory edits, beliefs due for re-check, and where the last session left
        off. Bounded on purpose — retrieve everything else on demand.
        """
        con = db.connect()
        try:
            return memory.brief(con, config.load())
        finally:
            con.close()

    @mcp.tool()
    def evidence(question: str, k: int = 12, max_chars: int = 12000) -> str:
        """Build a citable evidence pack for a domain question.

        Hybrid keyword + vector retrieval over papers, full-text sections, and
        the user's own knowledge notes. Every passage carries its source. Use
        this before answering anything factual about the field. If it returns
        nothing, say the corpus is empty on this — do not answer from general
        memory as if it were sourced.
        """
        con = db.connect()
        try:
            return retrieve.context_pack(question, k=k, max_chars=max_chars, con=con)
        finally:
            con.close()

    @mcp.tool()
    def search(query: str, k: int = 10, kind: str | None = None) -> str:
        """Search the corpus and return matching passages as JSON.

        kind: 'paper' (literature only), 'knowledge' (the user's curated notes
        only), or omitted for both.
        """
        con = db.connect()
        try:
            hits = retrieve.search(query, k=k, doc_kind=kind, con=con)
            return json.dumps([
                {
                    "title": h.title or h.doc_ref,
                    "paper_id": h.paper_id,
                    "source": h.citation(),
                    "url": h.url,
                    "section": h.heading,
                    "text": h.text[:1500],
                    "retrieved_via": h.how,
                }
                for h in hits
            ], indent=2)
        finally:
            con.close()

    @mcp.tool()
    def search_papers(query: str, k: int = 15) -> str:
        """Find papers (not passages) by title/abstract. Returns JSON metadata."""
        con = db.connect()
        try:
            rows = retrieve.keyword_papers(con, query, k)
            return json.dumps([
                {kk: r[kk] for kk in ("id", "title", "journal", "pub_date", "score",
                                      "url", "is_oa", "read_state", "doi")}
                for r in rows
            ], indent=2)
        finally:
            con.close()

    @mcp.tool()
    def get_paper(paper_id: str, include_methods: bool = False,
                  include_full_text: bool = False) -> str:
        """Fetch one paper: metadata, abstract, and optionally its sections.

        `include_methods=True` returns only the methods sections — the protocol
        detail (predictor versions, peptide lengths, mouse group sizes, adjuvant
        doses) that abstracts always omit.
        """
        con = db.connect()
        try:
            row = con.execute(
                "SELECT * FROM papers WHERE id=? OR doi=? OR pmid=?",
                (paper_id, paper_id, paper_id),
            ).fetchone()
            if row is None:
                return f"No paper matching {paper_id!r} in the corpus."
            out = {k: row[k] for k in row.keys() if k not in ("notes",)}
            if include_methods or include_full_text:
                sql = "SELECT heading, kind, text FROM sections WHERE paper_id=?"
                args: list[Any] = [row["id"]]
                if include_methods and not include_full_text:
                    sql += " AND kind='methods'"
                sql += " ORDER BY ord"
                out["sections"] = [dict(s) for s in con.execute(sql, args).fetchall()]
                if not out["sections"]:
                    out["sections_note"] = (
                        "No stored full text. Call fetch_fulltext(paper_id) if it is "
                        "open access; otherwise the user must save the PDF to inbox/."
                    )
            return json.dumps(out, indent=2)
        finally:
            con.close()

    @mcp.tool()
    def fetch_fulltext(paper_id: str) -> str:
        """Pull open-access full text for one paper into the corpus."""
        con = db.connect()
        try:
            n = ft_mod.fetch_for_paper(con, paper_id)
            con.commit()
            return (f"stored {n:,} characters for {paper_id}" if n else
                    f"no open-access full text available for {paper_id}")
        finally:
            con.close()

    @mcp.tool()
    def latest_digest() -> str:
        """The most recent research digest — what changed since the last sweep."""
        text = digest.read_latest()
        return text or "No digests yet. Run a sweep first."

    @mcp.tool()
    def run_sweep(days: int = 7, include_trials: bool = True) -> str:
        """Fetch new literature and trials now, then write a digest.

        Takes a minute or two. Use when the user asks what is new, or when the
        corpus looks stale in `brief`.
        """
        from . import sweep as sweep_mod

        report = sweep_mod.sweep(days=days, do_trials=include_trials, quiet=True)
        return json.dumps(report, indent=2)

    @mcp.tool()
    def trials(query: str = "", status: str | None = None, limit: int = 20) -> str:
        """Search tracked clinical trials, including their status-change history."""
        con = db.connect()
        try:
            sql = "SELECT * FROM trials WHERE 1=1"
            args: list[Any] = []
            if query:
                sql += " AND (title LIKE ? OR conditions LIKE ? OR interventions LIKE ?)"
                args += [f"%{query}%"] * 3
            if status:
                sql += " AND status LIKE ?"
                args.append(f"%{status}%")
            sql += " ORDER BY last_update DESC LIMIT ?"
            args.append(limit)
            rows = con.execute(sql, args).fetchall()
            out = []
            for r in rows:
                item = {k: r[k] for k in ("nct_id", "title", "status", "phase",
                                          "conditions", "interventions", "sponsor",
                                          "enrollment", "url")}
                hist = con.execute(
                    "SELECT observed_on, field, old_value, new_value FROM trial_history"
                    " WHERE nct_id=? ORDER BY id DESC LIMIT 5", (r["nct_id"],),
                ).fetchall()
                if hist:
                    item["changes"] = [dict(h) for h in hist]
                out.append(item)
            return json.dumps(out, indent=2)
        finally:
            con.close()

    # ------------------------------------------------------------ knowledge
    @mcp.tool()
    def read_knowledge(filename: str) -> str:
        """Read one curated knowledge note in full, e.g. 'preclinical_models.md'.

        Use only when the conversation is genuinely about that topic — these are
        long, and loading them all defeats the point of tiered memory.
        """
        path = config.KNOWLEDGE_DIR / Path(filename).name
        if not path.exists():
            available = ", ".join(p.name for p in config.KNOWLEDGE_DIR.glob("*.md"))
            return f"No such note. Available: {available}"
        return path.read_text(encoding="utf-8")

    @mcp.tool()
    def list_knowledge() -> str:
        """List the curated knowledge notes with approximate sizes."""
        return json.dumps(
            [{"file": n, "approx_tokens": t} for n, t in memory.list_knowledge()], indent=2
        )

    # ------------------------------------------------- permanent memory
    @mcp.tool()
    def remember(text: str, kind: str = "observation", topic: str = "",
                 importance: int = 2) -> str:
        """Write something to permanent, append-only memory. USE THIS OFTEN.

        This is the one memory tool with no approval step, because the storage
        is immutable: entries can never be edited or deleted, by you or anyone.
        An unreviewed entry is a record that something was said or seen — it is
        not authoritative and must not be cited as established fact.

        Record, as they happen: corrections the user makes to you, their stated
        preferences, decisions and the reasons behind them, experimental
        results, and anything you would be annoyed to have forgotten next
        month. Prefer their exact words over your paraphrase.

        kind: observation | correction | decision | preference | result |
              question | error
        importance: 1-5; 4+ surfaces in every future session brief.
        """
        con = db.connect()
        try:
            jid = journal.record(con, text, kind=kind, topic=topic,
                                 source="agent", importance=importance)
            return f"journal #{jid} recorded permanently (append-only, cannot be edited)"
        finally:
            con.close()

    @mcp.tool()
    def recall(query: str = "", kind: str | None = None, since: str | None = None,
               limit: int = 20) -> str:
        """Search episodic memory — everything ever recorded, never deleted.

        Use before claiming you do not know something about the user's project,
        and before repeating advice they may have already rejected. An empty
        query returns the most recent entries.
        """
        con = db.connect()
        try:
            return json.dumps(
                journal.recall(con, query, kind=kind, since=since, limit=limit), indent=2
            )
        finally:
            con.close()

    @mcp.tool()
    def get_rules(scope: str | None = None) -> str:
        """Procedural memory: the ways of working learned from this user.

        Check these before giving methodological advice — they encode
        corrections the user already made, and repeating a corrected mistake is
        the failure this system exists to prevent.
        """
        con = db.connect()
        try:
            return json.dumps(memory.get_rules(con, scope=scope), indent=2)
        finally:
            con.close()

    # -------------------------------------------------------------- beliefs
    @mcp.tool()
    def get_beliefs(topic: str | None = None, due_only: bool = False) -> str:
        """The sourced-claims store: what this brain believes, and how strongly."""
        con = db.connect()
        try:
            return json.dumps(memory.get_beliefs(con, topic=topic, due_only=due_only), indent=2)
        finally:
            con.close()

    @mcp.tool()
    def belief_history(belief_id: int) -> str:
        """Every version of a claim, including superseded ones.

        Beliefs are versioned, never overwritten. Use this to answer "did we
        always think that?" and to see what evidence changed our minds.
        """
        con = db.connect()
        try:
            return json.dumps(memory.belief_history(con, belief_id), indent=2)
        finally:
            con.close()

    @mcp.tool()
    def believed_as_of(when: str, topic: str | None = None) -> str:
        """What this brain believed at a past moment (ISO date or datetime).

        For questions like "on what basis did I write that in March?" — the
        state of knowledge then, not now.
        """
        con = db.connect()
        try:
            return json.dumps(memory.as_of(con, when, topic=topic), indent=2)
        finally:
            con.close()

    # ---------------------------------------------------------- entity graph
    @mcp.tool()
    def graph_neighbours(entity: str, limit: int = 15) -> str:
        """What an entity is most strongly connected to in the corpus.

        Entities are genes, HLA alleles, cell lines, mouse strains, tools,
        assays and key concepts. Use to orient on an unfamiliar term, or to
        find the confounds attached to a model system.
        """
        con = db.connect()
        try:
            row = con.execute(
                "SELECT id, name, kind, n_mentions FROM entities WHERE canonical=?"
                " ORDER BY n_mentions DESC LIMIT 1", (entity.lower(),),
            ).fetchone()
            if row is None:
                return f"'{entity}' is not in the entity graph."
            return json.dumps({
                "entity": dict(row),
                "neighbours": graph.neighbours(con, int(row["id"]), limit=limit),
            }, indent=2)
        finally:
            con.close()

    @mcp.tool()
    def graph_path(from_entity: str, to_entity: str) -> str:
        """How two concepts connect through the literature, via intermediates.

        Answers multi-hop questions that no single passage covers. The result
        is a co-occurrence path, NOT a causal claim: it means these things are
        discussed together through these intermediates. Report it as a lead to
        verify, never as a finding.
        """
        con = db.connect()
        try:
            path = graph.explain_path(con, from_entity, to_entity)
            if not path:
                return (f"No path found between '{from_entity}' and '{to_entity}' "
                        f"within 3 hops. They may be genuinely unconnected in this "
                        f"corpus, or the corpus may not cover the link yet.")
            return " → ".join(path) + (
                "\n\nCo-occurrence path, not causation: these concepts are discussed "
                "together through these intermediates. Verify before asserting."
            )
        finally:
            con.close()

    # ------------------------------------------------------------ conflicts
    @mcp.tool()
    def open_conflicts(limit: int = 15) -> str:
        """Evidence that may contradict what this brain currently believes.

        Candidates from a deliberately over-sensitive detector — read the
        passage before acting. When one is real, propose a `belief_revision`;
        the user adjudicates.
        """
        con = db.connect()
        try:
            rows = evidence.open_conflicts(con, limit=limit)
            if not rows:
                return "No open conflicts."
            return json.dumps(rows, indent=2)
        finally:
            con.close()

    @mcp.tool()
    def scan_for_conflicts() -> str:
        """Re-scan the corpus for evidence contradicting stored beliefs."""
        con = db.connect()
        try:
            return f"{evidence.scan(con)} candidate contradiction(s) opened"
        finally:
            con.close()

    @mcp.tool()
    def propose_memory_edit(
        kind: str,
        target: str,
        rationale: str,
        text: str = "",
        mode: str = "append",
        heading: str = "",
        evidence: str = "",
        payload_json: str = "",
    ) -> str:
        """Queue an edit to long-term memory for the user's approval.

        kind: 'core' (memory/CORE.md), 'knowledge' (a knowledge/*.md file),
        'belief' (a new sourced claim), 'belief_update', 'belief_revision'
        (target = the belief id being revised; payload needs a new `claim`),
        or 'rule' (procedural memory; payload needs `trigger` and `action`).
        mode: 'append' | 'replace_section' (needs `heading`) | 'replace_file'.

        Note the division of labour: use `remember` for raw observations, which
        is immediate and needs no approval. Use this when something should
        become authoritative — cited as fact, or applied as a standing rule.

        You cannot apply this yourself — the user reviews the diff with
        `neobrain review`. Always include the evidence that motivated it.
        """
        con = db.connect()
        try:
            payload = json.loads(payload_json) if payload_json else {"mode": mode, "text": text}
            if heading:
                payload["heading"] = heading
            pid = memory.propose(con, kind, target, payload,
                                 rationale=rationale, evidence=evidence)
            return (f"Proposal #{pid} queued for {kind} → {target}. "
                    f"The user must approve it with `neobrain review`; until then "
                    f"do not treat it as accepted knowledge.")
        finally:
            con.close()

    @mcp.tool()
    def list_proposals() -> str:
        """Memory edits awaiting the user's approval."""
        con = db.connect()
        try:
            return json.dumps(memory.pending_proposals(con), indent=2)
        finally:
            con.close()

    # ------------------------------------------------------------- sessions
    @mcp.tool()
    def start_session(topic: str = "") -> str:
        """Open an episodic memory record for this conversation."""
        con = db.connect()
        try:
            return f"session #{memory.start_session(con, topic)}"
        finally:
            con.close()

    @mcp.tool()
    def end_session(session_id: int, summary: str, decisions: str = "",
                    open_threads: str = "") -> str:
        """Close the session record. Write the summary for your future self:
        what we concluded, what we decided not to do, what is still open."""
        con = db.connect()
        try:
            memory.end_session(con, session_id, summary=summary, decisions=decisions,
                               open_threads=open_threads)
            return f"session #{session_id} recorded"
        finally:
            con.close()

    # -------------------------------------------------------------- teaching
    @mcp.tool()
    def teaching_packet(topic: str, k: int = 8) -> str:
        """Grounded material for teaching a topic: curriculum module, the user's
        notes, and current evidence. Teach from this, do not lecture from memory."""
        con = db.connect()
        try:
            return tutor.lesson_plan(topic, con=con, k=k)
        finally:
            con.close()

    @mcp.tool()
    def due_review_cards(limit: int = 15, topic: str | None = None) -> str:
        """Spaced-repetition cards due today. Quiz the user conversationally,
        then record each result with `grade_review_card`."""
        con = db.connect()
        try:
            return json.dumps(tutor.due_cards(con, limit=limit, topic=topic), indent=2)
        finally:
            con.close()

    @mcp.tool()
    def grade_review_card(card_id: int, grade: int) -> str:
        """Record a review result (0-5; below 3 is a lapse) and reschedule."""
        con = db.connect()
        try:
            return json.dumps(tutor.grade_card(con, card_id, grade))
        finally:
            con.close()

    @mcp.tool()
    def add_review_card(front: str, back: str, topic: str = "", source: str = "") -> str:
        """Add a card for a fact the user should have instantly available.
        Add these when they get something wrong, or when a paper introduces a
        fact they will need again. Always set `source`."""
        con = db.connect()
        try:
            return f"card #{tutor.add_card(con, front, back, topic=topic, source=source)}"
        finally:
            con.close()

    @mcp.tool()
    def corpus_status() -> str:
        """Corpus and memory statistics — use to check whether the brain is stale."""
        con = db.connect()
        try:
            return json.dumps(db.stats(con), indent=2)
        finally:
            con.close()

    return mcp


def main() -> int:
    config.ensure_dirs()
    try:
        _server().run()
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
