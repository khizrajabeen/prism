"""The `neobrain` command.

Everything the agent can do, you can do from a terminal — that is deliberate.
An agent capability you cannot invoke and inspect yourself is a capability you
cannot debug when it starts behaving strangely at 2am.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import textwrap
from pathlib import Path
from sqlite3 import connect as sqlite3_connect

from . import (
    answer, clinic, config, db, digest, discover, evidence, graph, journal, memory,
    retrieve, science, scoring, tutor,
)
from .sources import local as local_src

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def _c(text: str, code: str) -> str:
    return text if not sys.stdout.isatty() else f"{code}{text}{RESET}"


def _print_hits(hits: list[retrieve.Hit], snippet: int = 400, query: str = "") -> None:
    if not hits:
        print("No matches in the local corpus.")
        print(_c("That is a fact about the corpus, not about the literature. "
                 "Widen config/interests.yaml or run a longer sweep.", DIM))
        return
    for i, h in enumerate(hits, 1):
        print(_c(f"[{i}] {h.title or h.doc_ref}", BOLD))
        if h.doc_kind != "knowledge":
            print(_c(f"    {h.citation()}", DIM))
            if h.url:
                print(_c(f"    {h.url}", DIM))
        section = (h.heading or "n/a").split(" — ")[-1]
        tier = f" · tier {h.evidence_tier}/5" if h.evidence_tier is not None else ""
        print(_c(f"    § {section} · {'+'.join(h.how)} · rrf={h.score}{tier}", DIM))
        body = h.snippet(query, snippet)
        print(textwrap.fill(body, width=96, initial_indent="    ", subsequent_indent="    "))
        print()


# ------------------------------------------------------------------ commands

def cmd_init(args) -> int:
    config.ensure_dirs()
    con = db.connect()
    print(f"NeoBrain home : {config.HOME}")
    print(f"Database      : {config.DB_PATH}")
    for d in config.ALL_DIRS:
        print(f"  {d.relative_to(config.HOME) if d.is_relative_to(config.HOME) else d}/")

    seeded = tutor.seed_from_curriculum(con)
    if seeded:
        print(f"Seeded {seeded} starter cards from config/curriculum.yaml")

    from . import embeddings
    n = embeddings.index_knowledge_files(con, config.load())
    print(f"Indexed {n} passages from knowledge/ (keyword search is live now)")

    print("\nNext:")
    print("  neobrain sweep --days 30     # backfill a month of literature")
    print("  neobrain brief               # what your agent reads at session start")
    con.close()
    return 0


def cmd_doctor(args) -> int:
    cfg = config.load()
    ok = True
    print(_c("NeoBrain doctor", BOLD))
    print(f"  home            {config.HOME}")
    print(f"  python          {sys.version.split()[0]}")

    for mod, why, required in [
        ("requests", "literature sweep", True),
        ("yaml", "config files", True),
        ("numpy", "faster vector search (optional)", False),
        ("sentence_transformers", "local embeddings (optional)", False),
        ("pypdf", "PDF ingestion (optional)", False),
        ("mcp", "MCP server for agent tools (optional)", False),
    ]:
        try:
            __import__(mod)
            print(f"  {mod:<22} ok")
        except ImportError:
            mark = "MISSING" if required else "not installed"
            print(f"  {mod:<22} {mark} — {why}")
            if required:
                ok = False

    try:
        con = db.connect()
        s = db.stats(con)
        print(f"  database        {s['db_mb']} MB · {s['papers']:,} papers · "
              f"{s['chunks']:,} chunks · {s['embeddings']:,} vectors")
        errs = con.execute(
            "SELECT run_at, errors FROM runs WHERE kind='sweep' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if errs and errs["errors"] and errs["errors"] not in ("[]", None):
            print(f"  last sweep      {_c('had errors', BOLD)}: {errs['errors'][:200]}")
        con.close()
    except Exception as e:  # noqa: BLE001
        print(f"  database        FAILED: {e}")
        ok = False

    backend = cfg.get("embeddings.backend", "none")
    print(f"  embeddings      backend={backend}"
          + ("  (keyword-only retrieval — fine to start)" if backend == "none" else ""))

    core_tokens = memory.core_token_estimate()
    cap = int(cfg.get("memory.core_soft_token_cap", 2000))
    flag = "" if core_tokens <= cap else _c("  ← over cap, promote detail into knowledge/", BOLD)
    print(f"  CORE.md         ~{core_tokens} tokens (cap {cap}){flag}")

    print(f"  network allow   {', '.join(cfg.get('network.allow', []))}")
    print("\n" + ("All good." if ok else "Fix the MISSING items: pip install -r requirements.txt"))
    return 0 if ok else 1


def cmd_status(args) -> int:
    con = db.connect()
    s = db.stats(con)
    print(_c("NeoBrain status", BOLD))
    groups = [
        ("corpus", ("papers", "papers_oa", "papers_read", "graded", "fulltext",
                    "sections", "trials", "trial_changes")),
        ("retrieval", ("chunks", "embeddings", "entities", "entity_edges")),
        ("memory", ("journal", "rules", "beliefs", "beliefs_due", "conflicts_open",
                    "proposals_pending", "sessions")),
        ("teaching", ("cards", "cards_due")),
    ]
    for label, keys in groups:
        print(_c(f"\n  {label}", BOLD))
        for key in keys:
            value = s.get(key, 0)
            print(f"    {key:<18} {value:,}" if isinstance(value, int)
                  else f"    {key:<18} {value}")
    if s.get("journal_since"):
        print(f"\n  remembering since  {s['journal_since'][:16]}")
    print(f"  {'last_sweep':<18} {s['last_sweep'] or 'never'}")
    print(f"  {'db':<18} {s['db_path']} ({s['db_mb']} MB)")

    rows = con.execute(
        "SELECT run_at, kind, new_papers, new_trials, duration_s FROM runs ORDER BY id DESC LIMIT 5"
    ).fetchall()
    if rows:
        print("\n  recent runs")
        for r in rows:
            print(f"    {r['run_at']}  {r['kind']:<8} +{r['new_papers']} papers "
                  f"+{r['new_trials']} trials  {r['duration_s']}s")
    con.close()
    return 0


def cmd_sweep(args) -> int:
    from . import sweep as sweep_mod

    report = sweep_mod.sweep(
        days=args.days,
        do_trials=not args.no_trials,
        do_fulltext=False if args.no_fulltext else None,
        do_embed=not args.no_embed,
        quiet=args.quiet,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    return 0


def cmd_digest(args) -> int:
    files = digest.latest(args.n)
    if not files:
        print("No digests yet. Run: neobrain sweep --days 30")
        return 1
    for path in reversed(files):
        print(path.read_text(encoding="utf-8"))
        print("\n" + "─" * 80 + "\n")
    return 0


def cmd_brief(args) -> int:
    con = db.connect()
    print(memory.brief(con, config.load()))
    con.close()
    return 0


def cmd_search(args) -> int:
    cfg = config.load()
    con = db.connect()
    if args.papers:
        rows = retrieve.keyword_papers(con, args.query, args.k)
        if not rows:
            print("No matching papers.")
        for r in rows:
            print(_c(f"[{r['score']}] {r['title']}", BOLD))
            print(_c(f"    {r['journal']} · {r['pub_date']} · {r['id']} · {r['url']}", DIM))
            print(_c(f"    state={r['read_state']} oa={'yes' if r['is_oa'] else 'no'}", DIM))
            print()
    else:
        hits = retrieve.search(args.query, k=args.k, cfg=cfg, con=con,
                               doc_kind=args.kind, use_vectors=not args.no_vectors)
        _print_hits(hits, query=args.query)
    con.close()
    return 0


def cmd_ask(args) -> int:
    """Print a citable evidence pack — the input to an answer, not the answer."""
    cfg = config.load()
    con = db.connect()
    pack = retrieve.context_pack(args.question, k=args.k, cfg=cfg, con=con,
                                 max_chars=args.max_chars)
    print(pack)
    con.close()
    return 0


def cmd_paper(args) -> int:
    con = db.connect()
    row = con.execute("SELECT * FROM papers WHERE id=? OR doi=? OR pmid=?",
                      (args.id, args.id, args.id)).fetchone()
    if row is None:
        print(f"No paper matching {args.id!r}. Try: neobrain search --papers '<terms>'")
        con.close()
        return 1
    print(_c(row["title"], BOLD))
    print(f"{row['authors']}")
    print(f"{row['journal']} · {row['pub_date']} · {row['id']}")
    print(f"{row['url']}")
    print(f"score={row['score']} matched={row['matched']} state={row['read_state']} "
          f"oa={'yes' if row['is_oa'] else 'no'}")
    if row["notes"]:
        print(f"\nnotes: {row['notes']}")
    print("\n" + textwrap.fill(row["abstract"] or "(no abstract)", width=96))

    if args.sections or args.methods:
        secs = con.execute(
            "SELECT heading, kind, text FROM sections WHERE paper_id=? ORDER BY ord",
            (row["id"],),
        ).fetchall()
        if not secs:
            print(_c("\nNo full text stored. Fetch it with: neobrain fulltext " + row["id"], DIM))
        for s in secs:
            if args.methods and s["kind"] != "methods":
                continue
            print(_c(f"\n## {s['heading']} [{s['kind']}]", BOLD))
            print(textwrap.fill(s["text"], width=96))
    con.close()
    return 0


def cmd_fulltext(args) -> int:
    from .sources import fulltext as ft

    con = db.connect()
    if args.id:
        n = ft.fetch_for_paper(con, args.id)
        con.commit()
        print(f"{args.id}: {n:,} chars" if n else
              f"{args.id}: no OA full text available (paywalled, or not in PMC). "
              f"Save the PDF into inbox/ and run `neobrain ingest-pdf --inbox`.")
    else:
        added, chars = ft.backfill(con, limit=args.limit, min_score=args.min_score)
        print(f"{added} papers, {chars:,} chars")
    con.close()
    return 0


def cmd_mark(args) -> int:
    con = db.connect()
    fields, vals = [], []
    if args.state:
        fields.append("read_state=?"); vals.append(args.state)
    if args.rating is not None:
        fields.append("rating=?"); vals.append(args.rating)
    if args.note:
        fields.append("notes=COALESCE(notes,'') || ?"); vals.append(f"\n[{db.today()}] {args.note}")
    if not fields:
        print("Nothing to change. Use --state / --rating / --note.")
        return 1
    vals.append(args.id)
    cur = con.execute(f"UPDATE papers SET {', '.join(fields)} WHERE id=?", vals)
    con.commit()
    print(f"updated {cur.rowcount} paper(s)")
    con.close()
    return 0


def cmd_embed(args) -> int:
    from . import embeddings

    cfg = config.load()
    con = db.connect()
    if args.rebuild:
        con.execute("DELETE FROM embeddings")
        con.execute("DELETE FROM chunks")
        con.commit()
        print("cleared chunks and embeddings")
    n_know = embeddings.index_knowledge_files(con, cfg)
    print(f"knowledge passages: {n_know}")
    n = embeddings.index_new(con, cfg, limit=args.limit, progress=lambda m: print(m))
    backend = cfg.get("embeddings.backend", "none")
    if backend == "none":
        print("embeddings.backend is 'none' — keyword search only. "
              "Set it in config/settings.yaml to enable vectors.")
    else:
        print(f"embedded {n} chunks with {backend}")
    con.close()
    return 0


def cmd_ingest_pdf(args) -> int:
    con = db.connect()
    targets: list[Path] = []
    if args.inbox:
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
        targets = [p for p in sorted(config.INBOX_DIR.glob("**/*"))
                   if p.is_file() and p.suffix.lower() in (".pdf", ".txt", ".md")]
        if not targets:
            print(f"inbox is empty: {config.INBOX_DIR}")
    else:
        targets = [Path(p) for p in args.paths]

    for path in targets:
        rep = local_src.ingest_pdf(con, path, title=args.title)
        if rep["ok"]:
            print(f"✓ {rep['id']} · {rep['sections']} sections · {rep['chars']:,} chars · {rep['title'][:70]}")
            if args.archive and args.inbox:
                dest = config.WORKSPACE_DIR / "ingested"
                dest.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), dest / path.name)
        else:
            print(f"✗ {path.name}: {rep['error']}")
    con.close()
    return 0


def cmd_belief(args) -> int:
    con = db.connect()
    if args.action == "add":
        sources = []
        for s in args.source or []:
            if s.startswith("http"):
                sources.append({"url": s})
            elif ":" in s and not s.startswith("10."):
                sources.append({"paper_id": s})
            else:
                sources.append({"citation": s})
        bid = memory.add_belief(
            con, args.claim, confidence=args.confidence, topic=args.topic or "",
            rationale=args.rationale or "", sources=sources, review_days=args.review_days,
        )
        print(f"belief #{bid} recorded")
    elif args.action == "list":
        beliefs = memory.get_beliefs(con, topic=args.topic, due_only=args.due,
                                     status=args.status)
        if not beliefs:
            print("No beliefs recorded yet.")
        for b in beliefs:
            print(memory.format_belief(b))
            print()
    con.close()
    return 0


def cmd_propose(args) -> int:
    con = db.connect()
    payload: dict = json.loads(args.payload) if args.payload else {}
    if args.text:
        payload = {"mode": args.mode, "text": args.text}
        if args.heading:
            payload["heading"] = args.heading
    pid = memory.propose(con, args.kind, args.target, payload,
                         rationale=args.rationale, evidence=args.evidence or "")
    print(f"proposal #{pid} queued — review with: neobrain review")
    con.close()
    return 0


def cmd_review(args) -> int:
    con = db.connect()
    pend = memory.pending_proposals(con)
    if not pend:
        print("No pending memory edits.")
        con.close()
        return 0

    for p in pend:
        print("─" * 80)
        print(_c(f"#{p['id']}  {p['kind']} → {p['target']}", BOLD))
        print(f"created  {p['created_at']}")
        print(f"why      {p['rationale']}")
        if p["evidence"]:
            print(f"evidence {p['evidence']}")
        print()
        print(memory.proposal_diff(con, p["id"]))
        print()

        if args.yes:
            decision = "a"
        elif args.list:
            continue
        else:
            try:
                decision = input("[a]pply / [r]eject / [s]kip / [q]uit ? ").strip().lower()[:1]
            except (EOFError, KeyboardInterrupt):
                print("\naborted")
                break
        if decision == "a":
            result = memory.apply_proposal(con, p["id"])
            print(_c(f"applied → {result.get('path') or result}", BOLD))
        elif decision == "r":
            note = "" if args.yes else input("reason (optional): ").strip()
            memory.reject_proposal(con, p["id"], note)
            print("rejected")
        elif decision == "q":
            break
    con.close()
    return 0


def cmd_session(args) -> int:
    con = db.connect()
    if args.action == "start":
        sid = memory.start_session(con, args.topic or "")
        print(f"session #{sid} started")
    else:
        memory.end_session(con, args.id, summary=args.summary or "",
                           decisions=args.decisions or "", open_threads=args.open or "")
        print(f"session #{args.id} closed")
    con.close()
    return 0


def cmd_teach(args) -> int:
    con = db.connect()
    print(tutor.lesson_plan(args.topic, con=con, cfg=config.load(), k=args.k))
    con.close()
    return 0


def cmd_quiz(args) -> int:
    con = db.connect()
    cards = tutor.due_cards(con, limit=args.n, topic=args.topic)
    if not cards:
        print("Nothing due. Add cards as you read: neobrain card add -q '...' -a '...'")
        con.close()
        return 0

    correct = 0
    for i, card in enumerate(cards, 1):
        print("─" * 80)
        print(_c(f"[{i}/{len(cards)}] {card['front']}", BOLD))
        try:
            input(_c("(press enter for the answer)", DIM))
        except (EOFError, KeyboardInterrupt):
            break
        print(textwrap.fill(card["back"], width=96))
        if card["source"]:
            print(_c(f"source: {card['source']}", DIM))
        try:
            raw = input("grade 0-5 (0 blank, 3 hard, 5 easy) ? ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw.isdigit():
            continue
        res = tutor.grade_card(con, card["id"], int(raw))
        correct += int(raw) >= 3
        print(_c(f"next review in {res['interval']}d ({res['due_on']})", DIM))
    print(f"\n{correct}/{len(cards)} recalled. Deck: {tutor.deck_stats(con)['due']} still due.")
    con.close()
    return 0


def cmd_card(args) -> int:
    con = db.connect()
    if args.action == "add":
        cid = tutor.add_card(con, args.q, args.a, topic=args.topic or "",
                             source=args.source or "")
        print(f"card #{cid} added")
    elif args.action == "stats":
        stats = tutor.deck_stats(con)
        print(f"{stats['total']} cards, {stats['due']} due")
        for r in stats["by_topic"]:
            print(f"  {r['topic'] or '(untopiced)':<28} {r['n']:>4} cards "
                  f"{r['due']:>4} due  ease {r['ease'] or 0:.2f}  lapses {r['lapses'] or 0}")
    con.close()
    return 0


def cmd_remember(args) -> int:
    """Write something to permanent, append-only memory."""
    con = db.connect()
    jid = journal.record(
        con, args.text, kind=args.kind, topic=args.topic or "",
        source=args.source or "user", importance=args.importance,
    )
    print(f"journal #{jid} — recorded permanently, and it cannot be edited or deleted")
    con.close()
    return 0


def cmd_recall(args) -> int:
    con = db.connect()
    entries = journal.recall(
        con, args.query or "", kind=args.kind, topic=args.topic,
        since=args.since, limit=args.n,
    )
    if not entries:
        print("Nothing in episodic memory matches.")
    for e in entries:
        print(journal.format_entry(e))
        print()
    if not args.query and not args.kind:
        s = journal.stats(con)
        print(_c(f"{s['total']:,} entries since {s['since'] or 'today'} · "
                 + ", ".join(f"{k}:{v}" for k, v in list(s['by_kind'].items())[:6]), DIM))
    con.close()
    return 0


def cmd_rule(args) -> int:
    con = db.connect()
    if args.action == "add":
        rid = memory.add_rule(con, args.trigger, args.action_text,
                              scope=args.scope or "", source=args.source or "user")
        print(f"rule #{rid} — it will appear in every session brief from now on")
    elif args.action == "list":
        rules = memory.get_rules(con, scope=args.scope)
        if not rules:
            print("No rules learned yet. Add one with:\n"
                  "  neobrain rule add 'I design a mouse vaccine study' "
                  "'check for an adjuvant-alone arm'")
        for r in rules:
            fired = f" · fired {r['fired']}×" if r["fired"] else " · never fired"
            print(_c(f"#{r['id']} when {r['trigger']}", BOLD))
            print(f"    → {r['action']}")
            print(_c(f"    {r['scope'] or 'general'}{fired} · from {r['source'] or 'user'}", DIM))
    elif args.action == "retire":
        memory.retire_rule(con, args.id, args.reason or "")
        print(f"rule #{args.id} retired (kept in history)")
    con.close()
    return 0


def cmd_graph(args) -> int:
    con = db.connect()
    if args.rebuild:
        con.execute("DELETE FROM mentions")
        con.execute("DELETE FROM entity_edges")
        con.execute("UPDATE entities SET n_mentions=0")
        con.commit()
    if args.entity:
        row = con.execute(
            "SELECT id, name, kind, n_mentions FROM entities WHERE canonical=?"
            " ORDER BY n_mentions DESC LIMIT 1", (args.entity.lower(),),
        ).fetchone()
        if row is None:
            print(f"'{args.entity}' is not in the graph yet. "
                  f"Run `neobrain graph --rebuild` after a sweep.")
            con.close()
            return 1
        print(_c(f"{row['name']} ({row['kind']}) · {row['n_mentions']} mentions", BOLD))
        print("\nmost strongly connected to:")
        for nb in graph.neighbours(con, row["id"], limit=args.k):
            print(f"  {nb['weight']:>6.2f}  {nb['name']:<32} ({nb['kind']})")
    elif args.path:
        a, b = args.path
        route = graph.explain_path(con, a, b)
        if route:
            print(" → ".join(route))
            print(_c("\nA co-occurrence path, not a causal claim: these concepts are "
                     "discussed together through these intermediates. A lead, not a finding.", DIM))
        else:
            print(f"No path found between '{a}' and '{b}' within 3 hops.")
    else:
        report = graph.index_chunks(con)
        s = graph.stats(con)
        print(f"indexed {report['chunks']} chunks · {s['entities']} entities · "
              f"{s['mentions']} mentions · {s['edges']} edges")
        print("\nmost mentioned:")
        for e in s["top"]:
            print(f"  {e['n_mentions']:>5}  {e['name']:<32} ({e['kind']})")
    con.close()
    return 0


def cmd_conflicts(args) -> int:
    con = db.connect()
    if args.scan:
        opened = evidence.scan(con)
        print(f"{opened} new candidate contradiction(s) opened")
    rows = evidence.open_conflicts(con, limit=args.n)
    if not rows:
        print("No open conflicts.")
        con.close()
        return 0
    for c in rows:
        print("─" * 80)
        print(_c(f"#{c['id']} against belief: {c['claim']}", BOLD))
        print(_c(f"cue: {c['cue']}", DIM))
        if c.get("title"):
            print(_c(f"in: {c['title']} (tier {c.get('evidence_tier')}) {c.get('url') or ''}", DIM))
        print(textwrap.fill(" ".join((c["passage"] or "").split())[:600], width=96,
                            initial_indent="    ", subsequent_indent="    "))
        if args.resolve:
            try:
                choice = input("\n[r]esolved / [d]ismiss / [s]kip ? ").strip().lower()[:1]
            except (EOFError, KeyboardInterrupt):
                break
            if choice == "r":
                evidence.resolve(con, c["id"], "resolved", input("note: ").strip())
            elif choice == "d":
                evidence.resolve(con, c["id"], "dismissed")
        print()
    con.close()
    return 0


def cmd_history(args) -> int:
    """Show how a belief changed, or what was believed at a past date."""
    con = db.connect()
    if args.as_of:
        beliefs = memory.as_of(con, args.as_of, topic=args.topic)
        print(_c(f"What this brain believed on {args.as_of}:", BOLD))
        if not beliefs:
            print("  (nothing recorded yet at that point)")
        for b in beliefs:
            print(f"  #{b['id']} [{b['confidence']}] {b['claim']}")
    else:
        versions = memory.belief_history(con, args.id)
        if not versions:
            print(f"No belief #{args.id}")
            con.close()
            return 1
        for v in versions:
            state = v["status"]
            marker = "→" if state == "active" else " "
            print(_c(f"{marker} v{v['version'] or 1} #{v['id']} [{v['confidence']}] ({state})", BOLD))
            print(f"    {v['claim']}")
            print(_c(f"    asserted {(v['asserted_at'] or v['created_at'] or '')[:16]}"
                     + (f" · invalidated {(v['invalidated_at'] or '')[:16]}" if v["invalidated_at"] else "")
                     + (f" · {v['invalidated_reason']}" if v["invalidated_reason"] else ""), DIM))
    con.close()
    return 0


def cmd_backup(args) -> int:
    con = db.connect()
    dest = Path(args.out) if args.out else config.HOME / f"backups/brain-{db.today()}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3_connect(dest)
    with target:
        con.backup(target)
    target.close()
    con.close()
    size = round(dest.stat().st_size / 1e6, 2)
    print(f"{dest} ({size} MB)")
    return 0


def cmd_web(args) -> int:
    from .web import serve

    serve(host=args.host, port=args.port, token=args.token,
          open_browser=not args.no_browser, allow_remote=args.allow_remote)
    return 0


def cmd_models(args) -> int:
    from . import models as models_mod

    if args.env:
        env = models_mod.environment()
        print(_c("This machine", BOLD))
        print(f"  GPU        {'yes, ' + str(env['vram_gb']) + ' GB' if env['gpu'] else 'no'}")
        print(f"  installed  {', '.join(env['installed']) or 'none of the registered models'}")
        print(f"  {env['note']}")
        return 0

    if args.recommend:
        rec = models_mod.recommend(args.recommend)
        if "error" in rec:
            print(rec["error"])
            print("known tasks: " + ", ".join(rec["known_tasks"]))
            return 1
        print(_c(f"Task: {rec['task']}", BOLD))
        if rec["guidance"]:
            print(textwrap.fill(rec["guidance"], width=92, initial_indent="  ",
                                subsequent_indent="  "))
        m = rec["recommended"]
        print(_c(f"\n→ {m['name']}", BOLD) + f"  ({m['availability']})")
        print(textwrap.fill(m["summary"], width=92, initial_indent="    ", subsequent_indent="    "))
        if m["caveat"]:
            print(_c("  caveat:", BOLD))
            print(textwrap.fill(m["caveat"], width=92, initial_indent="    ", subsequent_indent="    "))
        if rec["alternatives"]:
            print(f"\n  alternatives: {', '.join(a['name'] for a in rec['alternatives'])}")
        return 0

    found = (models_mod.for_task(args.task) if args.task
             else models_mod.find(args.query or ""))
    if args.available:
        found = [m for m in found if m.available]
    if not found:
        print("Nothing matches. Tasks: " + ", ".join(models_mod.tasks()))
        return 1
    for m in found:
        print(models_mod.format_model(m, verbose=args.verbose or bool(args.query)))
        print()
    if args.task:
        g = models_mod.guidance(args.task)
        if g:
            print(_c("guidance", BOLD))
            print(textwrap.fill(g, width=92, initial_indent="  ", subsequent_indent="  "))
    return 0


def cmd_peptide(args) -> int:
    from . import peptides as pep

    try:
        if args.action == "windows":
            protein = (Path(args.protein).read_text(encoding="utf-8")
                       if Path(args.protein).exists() else args.protein)
            lengths = tuple(int(x) for x in args.lengths.split(","))
            windows = pep.mutant_windows(protein, args.position, args.mutant,
                                         lengths=lengths, wildtype_aa=args.wildtype)
            print(f"{len(windows)} peptides containing the mutation\n")
            print(f"{'mutant':<28} {'len':>3} {'pos':>4}  {'wild-type':<28} type")
            for w in windows:
                kind = "anchor" if w.is_anchor else "TCR-facing"
                print(f"{w.peptide:<28} {w.length:>3} {'P' + str(w.mutation_position):>4}  "
                      f"{w.wildtype:<28} {kind}")

        elif args.action == "junctions":
            epitopes = [e.strip() for e in args.epitopes.split(",") if e.strip()]
            r = pep.analyse_junctions(epitopes, linker=args.linker)
            print(_c("construct", BOLD))
            print(f"  {r['construct']}  ({r['length']} aa)")
            print(f"  linker {r['linker']} — {r['linker_note']}\n")
            for i, j in enumerate(r["junctions"], 1):
                print(_c(f"junction {i}: {j['left']} | {j['linker']} | {j['right']}", BOLD))
                print(f"  {j['n_novel']} novel peptides")
                print(textwrap.fill("  ".join(j["novel_peptides"][:16]), width=92,
                                    initial_indent="    ", subsequent_indent="    "))
            print(_c(f"\n{r['total_novel_peptides']} total", BOLD))
            print(textwrap.fill(r["verdict"], width=92, initial_indent="  ", subsequent_indent="  "))

        elif args.action == "hla":
            r = pep.normalize_hla(args.allele)
            mark = "✓" if r["valid"] else "✗"
            print(f"{mark} {r.get('normalized') or r['input']}")
            if r.get("mhc_class"):
                print(f"  class {r['mhc_class']} · {r.get('resolution', '')}")
            if r.get("note"):
                print(textwrap.fill(r["note"], width=92, initial_indent="  ", subsequent_indent="  "))

        elif args.action == "info":
            r = pep.summarize_peptide(args.peptide)
            print(_c(r["peptide"], BOLD))
            print(f"  length {r['length']} · {r['mhc_fit']}")
            print(f"  MW {r['molecular_weight_da']} Da · GRAVY {r['gravy']}")
            print(f"  anchors: P2={r['anchor_residues']['P2']} C-term={r['anchor_residues']['C_term']}")
            if r["synthesis_warning"]:
                print(_c(f"  synthesis: {r['synthesis_warning']}", BOLD))
    except pep.SequenceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0



def cmd_project(args) -> int:
    con = db.connect()
    try:
        if args.action == "new":
            pid = science.create_project(con, args.name, args.question or "", args.deadline or "")
            print(f"project #{pid} '{args.name}'")
        elif args.action == "list":
            projects = science.get_projects(con, status=args.status)
            if not projects:
                print("No projects. Open one:\n  neobrain project new '<name>' --question '<the question>'")
            for pr in projects:
                st = science.project_status(con, pr["id"])["counts"]
                print(_c(f"#{pr['id']} {pr['name']}", BOLD))
                print(f"    {pr['question'] or '(no question set)'}")
                print(_c(f"    {st['open_hypotheses']} open hypotheses · {st['awaiting_result']} "
                         f"awaiting result · {st['surprises']} surprises", DIM))
        elif args.action == "status":
            st = science.project_status(con, args.id)
            if not st:
                print(f"no project #{args.id}")
                return 1
            print(_c(st["project"]["name"], BOLD))
            print(f"  {st['project']['question'] or ''}\n")
            for h in st["hypotheses"]:
                print(_c(f"  [{h['status']}] #{h['id']} {h['statement']}", BOLD))
                print(_c(f"      falsified by: {h['falsifier']}", DIM))
                for x in h["experiments"]:
                    print(f"      experiment #{x['id']} {x['title']} — {x['status']}"
                          + (f" ({x['outcome']})" if x["outcome"] else ""))
            for d in st["decisions"]:
                print(f"  decision: {d['decision']}")
    finally:
        con.close()
    return 0


def cmd_hypothesis(args) -> int:
    con = db.connect()
    try:
        if args.action == "add":
            hid = science.add_hypothesis(
                con, args.statement, project_id=args.project,
                rationale=args.rationale or "", falsifier=args.falsifier,
                prior=args.prior or "")
            print(f"hypothesis #{hid}")
        elif args.action == "resolve":
            science.resolve_hypothesis(con, args.id, args.status,
                                       resolution=args.resolution or "",
                                       posterior=args.posterior or "")
            print(f"hypothesis #{args.id} → {args.status}")
        elif args.action == "list":
            for h in science.get_hypotheses(con, project_id=args.project, status=args.status):
                print(_c(f"[{h['status']}] #{h['id']} {h['statement']}", BOLD))
                print(_c(f"    falsified by: {h['falsifier']}", DIM))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        con.close()
    return 0


def cmd_experiment(args) -> int:
    con = db.connect()
    try:
        if args.action == "plan":
            eid = science.plan_experiment(
                con, args.title, project_id=args.project, hypothesis_id=args.hypothesis,
                design=args.design or "", prediction=args.prediction)
            print(f"experiment #{eid} — prediction is on the record before the result exists")
        elif args.action == "start":
            con.execute("UPDATE experiments SET status='running', started_at=? WHERE id=?",
                        (db.now(), args.id))
            con.commit()
            print(f"experiment #{args.id} running")
        elif args.action == "result":
            r = science.record_result(con, args.id, args.result, outcome=args.outcome,
                                      surprise=args.surprise)
            print(_c(f"predicted: {r['predicted']}", DIM))
            print(_c(f"observed:  {r['observed']}", BOLD))
            print(textwrap.fill(r["prompt"], width=92, initial_indent="\n  ",
                                subsequent_indent="  "))
        elif args.action == "list":
            for e in science.get_experiments(con, project_id=args.project, status=args.status):
                print(_c(f"[{e['status']}] #{e['id']} {e['title']}", BOLD))
                print(_c(f"    predicted: {e['prediction']}", DIM))
                if e["result"]:
                    print(f"    observed:  {e['result']}  → {e['outcome']}")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        con.close()
    return 0


def cmd_next(args) -> int:
    """What the work needs from you, across all projects."""
    con = db.connect()
    items = science.whats_next(con)
    if not items:
        s = science.stats(con)
        print("Nothing outstanding." if s["projects"] else
              "No projects yet — the brain works much better when it knows what you are\n"
              "trying to find out:\n  neobrain project new '<name>' --question '<question>'")
    for i in items:
        print(_c(f"[{i['kind']}] {i['what']}", BOLD))
        print(_c(f"    {i['why']}" + (f" · {i['project']}" if i["project"] else ""), DIM))
    con.close()
    return 0


def cmd_check(args) -> int:
    """Check a claim, or every sentence of a draft, against the corpus."""
    con = db.connect()
    text = (Path(args.claim).read_text(encoding="utf-8")
            if Path(args.claim).exists() else args.claim)
    if args.draft:
        results = answer.check_draft(text, con=con)
        if not results:
            print("No checkable factual sentences found.")
        for r in results:
            print(answer.format_check(r))
            print("─" * 92)
    else:
        print(answer.format_check(answer.check(text, con=con)))
    con.close()
    return 0


def cmd_answer(args) -> int:
    con = db.connect()
    r = answer.compose(args.question, con=con, k=args.k)
    print(_c(f"# {r['question']}", BOLD))
    print(f"\nEvidence strength: {r['evidence_strength']}")
    print(f"{r['n_passages']} passages · {r['n_sources']} sources · tiers "
          + (", ".join(f"{t}:{n}" for t, n in r["tier_spread"].items()) or "ungraded"))
    cov = r.get("coverage", {})
    if cov.get("papers_mentioning") is not None:
        print(_c(f"corpus coverage: {cov['papers_mentioning']} papers mention "
                 f"'{(cov.get('query_terms') or [''])[0]}' ({cov.get('oldest')} – {cov.get('newest')})", DIM))
    for b in r["existing_beliefs"]:
        print(_c(f"  already believed: #{b['id']} {b['claim']}", DIM))
    for i, pg in enumerate(r["passages"], 1):
        tier = f" [tier {pg['tier']}/5]" if pg["tier"] is not None else ""
        print(_c(f"\n[{i}] {pg['title']}{tier}", BOLD))
        print(_c(f"    {pg['citation']}", DIM))
        print(textwrap.fill(pg["passage"][:600], width=92,
                            initial_indent="    ", subsequent_indent="    "))
    if args.bibtex and r["bibtex"]:
        print(_c("\n# citations", BOLD))
        print(r["bibtex"])
    con.close()
    return 0


def cmd_clinic(args) -> int:
    con = db.connect()
    profile = clinic.PatientProfile(
        tumour_type=args.tumour or "",
        variants=[v.strip() for v in (args.variants or "").split(",") if v.strip()],
        hla=[h.strip() for h in (args.hla or "").split(",") if h.strip()],
        tmb=args.tmb, msi=args.msi or "",
    )
    print(clinic.format_screen(clinic.screen(con, profile)))
    con.close()
    return 0



def cmd_discover(args) -> int:
    """Guided literature discovery: keywords → questions → every journal."""
    con = db.connect()
    try:
        if args.action == "saved":
            for srch in discover.get_searches(con):
                print(_c(f"#{srch['id']} {srch['name']}", BOLD))
                print(_c(f"    {srch['keywords']} · {srch['n_results']} results · "
                         f"{srch['created_at'][:16]}", DIM))
            return 0

        if args.action == "snowball":
            r = discover.snowball(args.seed, direction=args.direction, limit=args.limit)
            if r.get("error"):
                print(r["error"])
                return 1
            print(_c(f"seed: {r['seed']['title']}", BOLD))
            for label in ("backward", "forward"):
                rows = r.get(label) or []
                if not rows:
                    continue
                print(_c(f"\n{label.upper()} ({len(rows)})", BOLD))
                for x in rows[:args.limit]:
                    oa = " · OA" if x.get("is_oa") else ""
                    print(f"  {str(x.get('pub_date',''))[:4]} {x['title'][:76]}{oa}")
            print(textwrap.fill(r["note"], width=92, initial_indent="\n", subsequent_indent=""))
            return 0

        # interactive search
        facets = discover.clarify(args.keywords)
        answers: dict = {}
        if not args.yes:
            print(_c(f"Searching for: {args.keywords}\n", BOLD))
            for f in facets:
                print(_c(f.question, BOLD))
                print(_c(f"  {f.why}", DIM))
                if f.id == "extra":
                    val = input("  > ").strip()
                    if val:
                        answers[f.id] = val
                    print()
                    continue
                for i, o in enumerate(f.options, 1):
                    print(f"  {i}. {o['label']}")
                raw = input("  choose (numbers, comma separated, blank to skip) > ").strip()
                if raw:
                    picks = [f.options[int(n) - 1]["value"] for n in raw.replace(" ", "").split(",")
                             if n.isdigit() and 0 < int(n) <= len(f.options)]
                    answers[f.id] = picks if f.multi else (picks[0] if picks else None)
                print()

        plan = discover.build_query(args.keywords, answers)
        print(_c("query", BOLD))
        print(textwrap.fill(plan["query"], width=92, initial_indent="  ", subsequent_indent="  "))
        if plan["narrowing"]:
            print(_c("  narrowed by: " + " · ".join(plan["narrowing"]), DIM))
        print()

        result = discover.federated_search(plan, per_source=args.per_source,
                                           progress=lambda m: print(m))
        c = result["counts"]
        print(_c(f"\n{c['unique']} unique papers ({c['retrieved']} retrieved, duplicates merged) · "
                 f"{c['open_access']} open access · {c['with_pdf']} PDF fetchable", BOLD))
        for e in result.get("errors", []):
            print(_c(f"  ! {e}", DIM))

        rows = result["results"]
        if args.include or args.exclude:
            screened = discover.auto_screen(
                rows,
                include_terms=(args.include or "").split(",") if args.include else [],
                exclude_terms=(args.exclude or "").split(",") if args.exclude else [])
            rows = screened["results"]
            print(_c(f"screened: {screened['counts']}", DIM))

        for i, r in enumerate(rows[: args.limit], 1):
            oa = _c(" OA", BOLD) if r.get("is_oa") else ""
            print(_c(f"\n[{i}] {r['title']}", BOLD) + oa)
            print(f"    {(r.get('authors') or 'authors unknown')[:88]}")
            print(_c(f"    {r.get('journal','')} {str(r.get('pub_date',''))[:4]}"
                     f" · cited {r.get('cited_by',0)}× · found by "
                     f"{','.join(r.get('found_by', []))}", DIM))
            print(_c(f"    {r.get('url','')}", DIM))
            if r.get("pdf_url"):
                print(_c(f"    PDF: {r['pdf_url']}", DIM))
            if args.abstracts and r.get("abstract"):
                print(textwrap.fill(r["abstract"][:420], width=92,
                                    initial_indent="    ", subsequent_indent="    "))

        if args.save:
            sid = discover.save_search(con, args.save, plan, {"results": rows, "counts": c})
            print(_c(f"\nsaved as search #{sid}", BOLD))
        if args.download:
            print(_c("\ndownloading open-access PDFs…", BOLD))
            for r in rows[: args.limit]:
                if r.get("pdf_url"):
                    d = discover.download_pdf(r)
                    print(f"  {'✓' if d['ok'] else '✗'} {r['title'][:60]} "
                          f"{d.get('path') or d.get('reason','')}")
        if args.export:
            out = Path(args.export)
            fmt = out.suffix.lstrip(".") or "bibtex"
            fmt = {"bib": "bibtex", "md": "markdown"}.get(fmt, fmt)
            out.write_text(discover.export(rows, fmt), encoding="utf-8")
            print(_c(f"\nexported {len(rows)} records → {out}", BOLD))
    finally:
        con.close()
    return 0


def cmd_progress(args) -> int:
    con = db.connect()
    print(tutor.progress(con))
    con.close()
    return 0


def cmd_score(args) -> int:
    """Explain why a title/abstract would or would not surface."""
    cfg = config.load()
    score, matched = scoring.score_record(args.title, args.abstract or "",
                                          cfg.boosts, cfg.penalties)
    print(scoring.explain(score, matched, cfg.threshold))
    return 0


def cmd_mcp(args) -> int:
    from . import mcp_server

    return mcp_server.main()


def cmd_export(args) -> int:
    con = db.connect()
    out = Path(args.out)
    rows = con.execute(
        "SELECT * FROM papers WHERE score >= ? ORDER BY score DESC", (args.min_score,)
    ).fetchall()
    if args.format == "bibtex":
        lines = []
        for r in rows:
            key = (r["id"] or "").replace(":", "_")
            lines.append(
                f"@article{{{key},\n  title = {{{r['title']}}},\n"
                f"  author = {{{r['authors']}}},\n  journal = {{{r['journal']}}},\n"
                f"  year = {{{(r['pub_date'] or '')[:4]}}},\n  doi = {{{r['doi']}}},\n"
                f"  url = {{{r['url']}}}\n}}\n"
            )
        out.write_text("\n".join(lines), encoding="utf-8")
    else:
        out.write_text(json.dumps([dict(r) for r in rows], indent=2), encoding="utf-8")
    print(f"{len(rows)} papers → {out}")
    con.close()
    return 0


# -------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="neobrain",
        description="A local research brain for neoantigen vaccines and drug discovery.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create directories, database, and starter index")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="check the install and report what is missing")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("status", help="corpus and memory statistics")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("sweep", help="fetch new literature and trials")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--no-trials", action="store_true")
    p.add_argument("--no-fulltext", action="store_true")
    p.add_argument("--no-embed", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("digest", help="print recent digests")
    p.add_argument("-n", type=int, default=1)
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("brief", help="the session-start briefing your agent reads")
    p.set_defaults(func=cmd_brief)

    p = sub.add_parser("search", help="hybrid search over the corpus")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=10)
    p.add_argument("--kind", choices=["paper", "knowledge"], default=None)
    p.add_argument("--papers", action="store_true", help="return papers, not passages")
    p.add_argument("--no-vectors", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("ask", help="build a citable evidence pack for a question")
    p.add_argument("question")
    p.add_argument("-k", type=int, default=12)
    p.add_argument("--max-chars", type=int, default=12000)
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("paper", help="show one paper")
    p.add_argument("id")
    p.add_argument("--sections", action="store_true")
    p.add_argument("--methods", action="store_true", help="methods sections only")
    p.set_defaults(func=cmd_paper)

    p = sub.add_parser("fulltext", help="fetch open-access full text")
    p.add_argument("id", nargs="?", default=None)
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--min-score", type=int, default=0)
    p.set_defaults(func=cmd_fulltext)

    p = sub.add_parser("mark", help="record your judgement on a paper")
    p.add_argument("id")
    p.add_argument("--state", choices=["new", "queued", "read", "skimmed", "rejected"])
    p.add_argument("--rating", type=int, choices=[1, 2, 3, 4, 5])
    p.add_argument("--note")
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("embed", help="build the retrieval index")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_embed)

    p = sub.add_parser("ingest-pdf", help="ingest local PDFs (the legal paywall route)")
    p.add_argument("paths", nargs="*")
    p.add_argument("--inbox", action="store_true", help=f"ingest everything in {config.INBOX_DIR}")
    p.add_argument("--archive", action="store_true", help="move ingested files out of inbox")
    p.add_argument("--title")
    p.set_defaults(func=cmd_ingest_pdf)

    p = sub.add_parser("belief", help="the sourced-claims store")
    bs = p.add_subparsers(dest="action", required=True)
    b = bs.add_parser("add")
    b.add_argument("claim")
    b.add_argument("--confidence", choices=list(memory.VALID_CONFIDENCE), default="moderate")
    b.add_argument("--topic")
    b.add_argument("--rationale")
    b.add_argument("--source", action="append", required=True,
                   help="paper id, DOI, PMID or URL (repeatable)")
    b.add_argument("--review-days", type=int, default=None)
    b = bs.add_parser("list")
    b.add_argument("--topic")
    b.add_argument("--due", action="store_true")
    b.add_argument("--status", default="active")
    p.set_defaults(func=cmd_belief)

    p = sub.add_parser("propose", help="queue a memory edit for approval")
    p.add_argument("kind", choices=["core", "knowledge", "belief", "belief_update"])
    p.add_argument("target")
    p.add_argument("--rationale", required=True)
    p.add_argument("--evidence")
    p.add_argument("--text")
    p.add_argument("--heading")
    p.add_argument("--mode", choices=["append", "replace_section", "replace_file"],
                   default="append")
    p.add_argument("--payload", help="raw JSON payload (for belief kinds)")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("review", help="approve or reject queued memory edits")
    p.add_argument("--yes", action="store_true", help="apply everything without prompting")
    p.add_argument("--list", action="store_true", help="show diffs only, decide nothing")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("session", help="record episodic memory")
    ss = p.add_subparsers(dest="action", required=True)
    s = ss.add_parser("start"); s.add_argument("--topic")
    s = ss.add_parser("end")
    s.add_argument("id", type=int)
    s.add_argument("--summary", required=True)
    s.add_argument("--decisions")
    s.add_argument("--open", help="open threads to pick up next time")
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("teach", help="build a grounded teaching packet")
    p.add_argument("topic", help="a curriculum module id, or any topic")
    p.add_argument("-k", type=int, default=8)
    p.set_defaults(func=cmd_teach)

    p = sub.add_parser("quiz", help="spaced-repetition review")
    p.add_argument("-n", type=int, default=15)
    p.add_argument("--topic")
    p.set_defaults(func=cmd_quiz)

    p = sub.add_parser("card", help="manage review cards")
    cs = p.add_subparsers(dest="action", required=True)
    c = cs.add_parser("add")
    c.add_argument("-q", required=True); c.add_argument("-a", required=True)
    c.add_argument("--topic"); c.add_argument("--source")
    cs.add_parser("stats")
    p.set_defaults(func=cmd_card)

    p = sub.add_parser("discover",
                       help="guided literature discovery across every journal")
    ds = p.add_subparsers(dest="action", required=True)
    a = ds.add_parser("search", help="keywords → clarifying questions → federated search")
    a.add_argument("keywords")
    a.add_argument("--yes", action="store_true", help="skip the questions, search now")
    a.add_argument("--limit", type=int, default=25)
    a.add_argument("--per-source", type=int, default=50)
    a.add_argument("--abstracts", action="store_true")
    a.add_argument("--include", help="inclusion terms, comma separated")
    a.add_argument("--exclude", help="exclusion terms, comma separated")
    a.add_argument("--save", metavar="NAME", help="save this search")
    a.add_argument("--download", action="store_true", help="fetch open-access PDFs")
    a.add_argument("--export", metavar="FILE", help="write .bib / .ris / .csv / .md")
    a = ds.add_parser("snowball", help="expand from a seed paper through the citation graph")
    a.add_argument("seed", help="DOI, or a title to look up")
    a.add_argument("--direction", choices=["both", "backward", "forward"], default="both")
    a.add_argument("--limit", type=int, default=25)
    ds.add_parser("saved", help="list saved searches")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("next", help="what the work needs from you, across all projects")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("project", help="the work: questions you are trying to answer")
    prs = p.add_subparsers(dest="action", required=True)
    a = prs.add_parser("new"); a.add_argument("name")
    a.add_argument("--question"); a.add_argument("--deadline")
    a = prs.add_parser("list"); a.add_argument("--status", default="active")
    a = prs.add_parser("status"); a.add_argument("id", type=int)
    p.set_defaults(func=cmd_project)

    p = sub.add_parser("hypothesis", help="testable statements, with required falsifiers")
    hs = p.add_subparsers(dest="action", required=True)
    a = hs.add_parser("add"); a.add_argument("statement")
    a.add_argument("--falsifier", required=True,
                   help="what observation would make you abandon this?")
    a.add_argument("--project", type=int); a.add_argument("--rationale"); a.add_argument("--prior")
    a = hs.add_parser("resolve"); a.add_argument("id", type=int)
    a.add_argument("status", choices=list(science.HYPOTHESIS_STATUS))
    a.add_argument("--resolution"); a.add_argument("--posterior")
    a = hs.add_parser("list"); a.add_argument("--project", type=int); a.add_argument("--status")
    p.set_defaults(func=cmd_hypothesis)

    p = sub.add_parser("experiment", help="experiments, with the prediction recorded first")
    es = p.add_subparsers(dest="action", required=True)
    a = es.add_parser("plan"); a.add_argument("title")
    a.add_argument("--prediction", required=True,
                   help="what you expect, specifically enough to be contradicted")
    a.add_argument("--project", type=int); a.add_argument("--hypothesis", type=int)
    a.add_argument("--design")
    a = es.add_parser("start"); a.add_argument("id", type=int)
    a = es.add_parser("result"); a.add_argument("id", type=int); a.add_argument("result")
    a.add_argument("--outcome", choices=list(science.OUTCOMES), default="ambiguous")
    a.add_argument("--surprise", type=int, default=0)
    a = es.add_parser("list"); a.add_argument("--project", type=int); a.add_argument("--status")
    p.set_defaults(func=cmd_experiment)

    p = sub.add_parser("check", help="check a claim (or a whole draft) against the corpus")
    p.add_argument("claim", help="a sentence, or a path to a file")
    p.add_argument("--draft", action="store_true", help="check every sentence")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("answer", help="assemble a cited answer scaffold for a question")
    p.add_argument("question")
    p.add_argument("-k", type=int, default=12)
    p.add_argument("--bibtex", action="store_true")
    p.set_defaults(func=cmd_answer)

    p = sub.add_parser("clinic", help="molecular screen: ESCAT tiers + trial matching (a search aid)")
    p.add_argument("--tumour", "--tumor", dest="tumour")
    p.add_argument("--variants", help="comma separated, e.g. 'KRAS G12D,B2M'")
    p.add_argument("--hla", help="comma separated")
    p.add_argument("--tmb", type=float)
    p.add_argument("--msi")
    p.set_defaults(func=cmd_clinic)

    p = sub.add_parser("web", help="run the local dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--token", help="require this token (use with --allow-remote)")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--allow-remote", action="store_true",
                   help="permit binding a non-local interface (see docs/SECURITY.md)")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("models", help="biology model registry: what exists, what runs here")
    p.add_argument("query", nargs="?", help="search by name, task, or text")
    p.add_argument("--task", help="filter to one task")
    p.add_argument("--recommend", metavar="TASK", help="recommend a model for a task")
    p.add_argument("--available", action="store_true", help="only what is installed here")
    p.add_argument("--env", action="store_true", help="what this machine can run")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("peptide", help="peptide and construct calculations")
    ps = p.add_subparsers(dest="action", required=True)
    a = ps.add_parser("windows", help="mutant peptide windows, with WT controls")
    a.add_argument("protein", help="sequence, or a path to a file containing one")
    a.add_argument("position", type=int, help="1-based residue position")
    a.add_argument("mutant", help="mutant residue, e.g. D")
    a.add_argument("--wildtype", help="expected WT residue — catches isoform mismatch")
    a.add_argument("--lengths", default="8,9,10,11")
    a = ps.add_parser("junctions", help="junctional neoepitopes in a construct")
    a.add_argument("epitopes", help="comma-separated, in construct order")
    a.add_argument("--linker", default="AAY")
    a = ps.add_parser("hla", help="validate and normalize an HLA allele")
    a.add_argument("allele")
    a = ps.add_parser("info", help="everything computable about one peptide")
    a.add_argument("peptide")
    p.set_defaults(func=cmd_peptide)

    p = sub.add_parser("remember", help="write to permanent append-only memory")
    p.add_argument("text")
    p.add_argument("--kind", choices=list(journal.KINDS), default="observation")
    p.add_argument("--topic")
    p.add_argument("--source")
    p.add_argument("--importance", type=int, choices=[1, 2, 3, 4, 5], default=2)
    p.set_defaults(func=cmd_remember)

    p = sub.add_parser("recall", help="search episodic memory (never deleted)")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--kind", choices=list(journal.KINDS))
    p.add_argument("--topic")
    p.add_argument("--since", help="ISO date, e.g. 2026-01-01")
    p.set_defaults(func=cmd_recall)

    p = sub.add_parser("rule", help="procedural memory: learned ways of working")
    rs = p.add_subparsers(dest="action", required=True)
    r = rs.add_parser("add")
    r.add_argument("trigger", help="when this applies")
    r.add_argument("action_text", metavar="action", help="what to do")
    r.add_argument("--scope")
    r.add_argument("--source")
    r = rs.add_parser("list"); r.add_argument("--scope")
    r = rs.add_parser("retire"); r.add_argument("id", type=int); r.add_argument("--reason")
    p.set_defaults(func=cmd_rule)

    p = sub.add_parser("graph", help="entity graph: what connects to what")
    p.add_argument("entity", nargs="?", help="show an entity's strongest connections")
    p.add_argument("--path", nargs=2, metavar=("FROM", "TO"), help="find a path between two entities")
    p.add_argument("-k", type=int, default=15)
    p.add_argument("--rebuild", action="store_true")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("conflicts", help="evidence that may contradict stored beliefs")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--scan", action="store_true", help="re-scan the corpus first")
    p.add_argument("--resolve", action="store_true", help="decide each one interactively")
    p.set_defaults(func=cmd_conflicts)

    p = sub.add_parser("history", help="how a belief changed, or what was believed when")
    p.add_argument("id", type=int, nargs="?", default=0)
    p.add_argument("--as-of", help="ISO datetime: what did I believe then?")
    p.add_argument("--topic")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("backup", help="snapshot the brain (safe while it is in use)")
    p.add_argument("--out")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("progress", help="curriculum progress")
    p.set_defaults(func=cmd_progress)

    p = sub.add_parser("score", help="explain the relevance score for a title/abstract")
    p.add_argument("title")
    p.add_argument("--abstract")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("mcp", help="run the MCP server (agent tool interface)")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("export", help="export the corpus")
    p.add_argument("out")
    p.add_argument("--format", choices=["json", "bibtex"], default="json")
    p.add_argument("--min-score", type=int, default=0)
    p.set_defaults(func=cmd_export)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config.ensure_dirs()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        if "--debug" in sys.argv:
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
