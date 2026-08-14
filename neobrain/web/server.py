"""Dashboard HTTP server.

Standard library only. `ThreadingHTTPServer` with a small dispatch table, one
SQLite connection per request (SQLite objects are not thread-safe, and the cost
of opening one is microseconds on a local file).

Security posture
----------------
Binds **127.0.0.1** by default and refuses any other interface unless you pass
``--allow-remote`` explicitly. This database contains your unpublished research
notes, your reading history, and possibly protocol detail from papers you have
not published against yet. It should not be reachable from the coffee shop wifi
because a default was convenient.

Write endpoints exist (approve a proposal, record a memory, grade a card) and
are deliberately limited to the same actions the CLI offers. The approval gate
still holds: the dashboard lets *you* approve a proposal, which is exactly the
human review step the agent cannot perform.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .. import (
    config, db, digest, evidence, graph, journal, memory, models, peptides,
    retrieve, tutor,
)

APP_HTML = Path(__file__).parent / "app.html"


class Api:
    """The endpoint implementations, independent of HTTP plumbing.

    Kept as plain methods taking (params, body) so they are directly testable
    without spinning up a server.
    """

    # ------------------------------------------------------------- overview
    def stats(self, p, b):
        con = db.connect()
        try:
            s = db.stats(con)
            s["graph"] = graph.stats(con)
            s["journal_stats"] = journal.stats(con)
            s["environment"] = models.environment()
            return s
        finally:
            con.close()

    def brief(self, p, b):
        con = db.connect()
        try:
            return {"markdown": memory.brief(con, config.load())}
        finally:
            con.close()

    def digest(self, p, b):
        files = digest.latest(int(p.get("n", 1)))
        return {
            "digests": [
                {"date": f.stem, "markdown": f.read_text(encoding="utf-8")} for f in files
            ]
        }

    # ------------------------------------------------------------ retrieval
    def search(self, p, b):
        q = p.get("q", "").strip()
        if not q:
            return {"hits": []}
        con = db.connect()
        try:
            hits = retrieve.search(q, k=int(p.get("k", 10)), con=con,
                                   doc_kind=p.get("kind") or None)
            return {
                "query": q,
                "hits": [
                    {
                        "title": h.title or h.doc_ref,
                        "citation": h.citation(),
                        "paper_id": h.paper_id,
                        "url": h.url,
                        "heading": h.heading,
                        "how": h.how,
                        "score": h.score,
                        "tier": h.evidence_tier,
                        "snippet": h.snippet(q, 600),
                    }
                    for h in hits
                ],
            }
        finally:
            con.close()

    def evidence_pack(self, p, b):
        q = p.get("q", "").strip()
        if not q:
            return {"markdown": ""}
        con = db.connect()
        try:
            return {"markdown": retrieve.context_pack(q, con=con, max_chars=20000)}
        finally:
            con.close()

    def papers(self, p, b):
        con = db.connect()
        try:
            q = p.get("q", "").strip()
            if q:
                rows = retrieve.keyword_papers(con, q, int(p.get("k", 25)))
            else:
                rows = db.rows_to_dicts(con.execute(
                    "SELECT * FROM papers ORDER BY score DESC, first_seen DESC LIMIT ?",
                    (int(p.get("k", 25)),),
                ).fetchall())
            return {"papers": rows}
        finally:
            con.close()

    # --------------------------------------------------------------- memory
    def journal_list(self, p, b):
        con = db.connect()
        try:
            return {
                "entries": journal.recall(
                    con, p.get("q", ""), kind=p.get("kind") or None,
                    limit=int(p.get("n", 50)),
                ),
                "stats": journal.stats(con),
            }
        finally:
            con.close()

    def journal_add(self, p, b):
        text = (b.get("text") or "").strip()
        if not text:
            return {"error": "empty entry"}
        con = db.connect()
        try:
            jid = journal.record(
                con, text, kind=b.get("kind", "observation"),
                topic=b.get("topic", ""), source="dashboard",
                importance=int(b.get("importance", 2)),
            )
            return {"id": jid, "ok": True,
                    "note": "recorded permanently — append-only, cannot be edited or deleted"}
        finally:
            con.close()

    def beliefs(self, p, b):
        con = db.connect()
        try:
            return {"beliefs": memory.get_beliefs(
                con, topic=p.get("topic") or None,
                due_only=p.get("due") == "1",
                status=p.get("status", "active"),
            )}
        finally:
            con.close()

    def belief_history(self, p, b):
        con = db.connect()
        try:
            return {"versions": memory.belief_history(con, int(p.get("id", 0)))}
        finally:
            con.close()

    def rules(self, p, b):
        con = db.connect()
        try:
            return {"rules": memory.get_rules(con)}
        finally:
            con.close()

    # ------------------------------------------------------------ oversight
    def proposals(self, p, b):
        con = db.connect()
        try:
            pend = memory.pending_proposals(con)
            for item in pend:
                item["diff"] = memory.proposal_diff(con, item["id"])
            return {"proposals": pend}
        finally:
            con.close()

    def decide_proposal(self, p, b):
        con = db.connect()
        try:
            pid = int(b.get("id"))
            if b.get("decision") == "apply":
                return {"ok": True, "result": memory.apply_proposal(con, pid, b.get("note", ""))}
            memory.reject_proposal(con, pid, b.get("note", ""))
            return {"ok": True, "result": "rejected"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        finally:
            con.close()

    def conflicts(self, p, b):
        con = db.connect()
        try:
            if p.get("scan") == "1":
                evidence.scan(con)
            return {"conflicts": evidence.open_conflicts(con, limit=int(p.get("n", 25)))}
        finally:
            con.close()

    def resolve_conflict(self, p, b):
        con = db.connect()
        try:
            evidence.resolve(con, int(b.get("id")), b.get("status", "dismissed"),
                             b.get("note", ""))
            return {"ok": True}
        finally:
            con.close()

    # ---------------------------------------------------------------- graph
    def graph_data(self, p, b):
        """Nodes and edges for the visualisation, centred on an entity or global."""
        con = db.connect()
        try:
            entity = (p.get("entity") or "").strip()
            limit = int(p.get("limit", 60))

            if entity:
                row = con.execute(
                    "SELECT id, name, kind, n_mentions FROM entities WHERE canonical=?"
                    " ORDER BY n_mentions DESC LIMIT 1", (entity.lower(),),
                ).fetchone()
                if row is None:
                    return {"nodes": [], "edges": [], "error": f"'{entity}' not in the graph"}
                seed_ids = {int(row["id"])}
                for nb in graph.neighbours(con, int(row["id"]), limit=limit):
                    seed_ids.add(int(nb["id"]))
                ids = list(seed_ids)
            else:
                ids = [int(r["id"]) for r in con.execute(
                    "SELECT id FROM entities ORDER BY n_mentions DESC LIMIT ?", (limit,)
                ).fetchall()]

            if not ids:
                return {"nodes": [], "edges": []}

            ph = ",".join("?" * len(ids))
            nodes = db.rows_to_dicts(con.execute(
                f"SELECT id, name, kind, n_mentions FROM entities WHERE id IN ({ph})", ids
            ).fetchall())
            edges = db.rows_to_dicts(con.execute(
                f"""SELECT a, b, weight FROM entity_edges
                    WHERE a IN ({ph}) AND b IN ({ph}) AND weight > 0
                    ORDER BY weight DESC LIMIT 400""",
                ids + ids,
            ).fetchall())
            return {"nodes": nodes, "edges": edges, "centre": entity or None}
        finally:
            con.close()

    def graph_path(self, p, b):
        con = db.connect()
        try:
            path = graph.explain_path(con, p.get("from", ""), p.get("to", ""))
            return {
                "path": path,
                "note": ("A co-occurrence path, not a causal claim: these concepts are "
                         "discussed together through these intermediates."
                         if path else "No path found within 3 hops."),
            }
        finally:
            con.close()

    # --------------------------------------------------------------- models
    def models_list(self, p, b):
        task = p.get("task")
        found = models.for_task(task) if task else models.find(p.get("q", ""))
        return {
            "models": [m.to_dict() for m in found],
            "tasks": models.tasks(),
            "guidance": models.guidance(task) if task else models.guidance(),
            "environment": models.environment(),
        }

    # ---------------------------------------------------------------- tools
    def peptide_windows(self, p, b):
        try:
            lengths = tuple(int(x) for x in (b.get("lengths") or [8, 9, 10, 11]))
            windows = peptides.mutant_windows(
                b.get("protein", ""), int(b.get("position", 1)),
                b.get("mutant_aa", ""), lengths=lengths,
                wildtype_aa=b.get("wildtype_aa") or None,
            )
            return {"windows": [w.to_dict() for w in windows], "count": len(windows)}
        except (peptides.SequenceError, ValueError) as e:
            return {"error": str(e)}

    def peptide_junctions(self, p, b):
        try:
            return peptides.analyse_junctions(
                b.get("epitopes", []), linker=b.get("linker", "AAY")
            )
        except (peptides.SequenceError, ValueError) as e:
            return {"error": str(e)}

    def peptide_summary(self, p, b):
        try:
            return peptides.summarize_peptide(b.get("peptide", ""))
        except peptides.SequenceError as e:
            return {"error": str(e)}

    def hla_check(self, p, b):
        try:
            return peptides.normalize_hla(b.get("allele", ""))
        except peptides.SequenceError as e:
            return {"error": str(e)}

    # -------------------------------------------------------------- teaching
    def quiz_due(self, p, b):
        con = db.connect()
        try:
            return {"cards": tutor.due_cards(con, limit=int(p.get("n", 20)),
                                             topic=p.get("topic") or None),
                    "stats": tutor.deck_stats(con)}
        finally:
            con.close()

    def quiz_grade(self, p, b):
        con = db.connect()
        try:
            return tutor.grade_card(con, int(b.get("id")), int(b.get("grade", 3)))
        finally:
            con.close()

    def curriculum(self, p, b):
        con = db.connect()
        try:
            return {"markdown": tutor.progress(con),
                    "modules": tutor.load_curriculum().get("modules", [])}
        finally:
            con.close()

    def teach(self, p, b):
        con = db.connect()
        try:
            return {"markdown": tutor.lesson_plan(p.get("topic", ""), con=con)}
        finally:
            con.close()

    # ---------------------------------------------------------------- sweep
    def run_sweep(self, p, b):
        from .. import sweep as sweep_mod

        return sweep_mod.sweep(days=int(b.get("days", 7)), quiet=True)


ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/stats"): "stats",
    ("GET", "/api/brief"): "brief",
    ("GET", "/api/digest"): "digest",
    ("GET", "/api/search"): "search",
    ("GET", "/api/evidence"): "evidence_pack",
    ("GET", "/api/papers"): "papers",
    ("GET", "/api/journal"): "journal_list",
    ("POST", "/api/journal"): "journal_add",
    ("GET", "/api/beliefs"): "beliefs",
    ("GET", "/api/belief-history"): "belief_history",
    ("GET", "/api/rules"): "rules",
    ("GET", "/api/proposals"): "proposals",
    ("POST", "/api/proposals/decide"): "decide_proposal",
    ("GET", "/api/conflicts"): "conflicts",
    ("POST", "/api/conflicts/resolve"): "resolve_conflict",
    ("GET", "/api/graph"): "graph_data",
    ("GET", "/api/graph-path"): "graph_path",
    ("GET", "/api/models"): "models_list",
    ("POST", "/api/peptides/windows"): "peptide_windows",
    ("POST", "/api/peptides/junctions"): "peptide_junctions",
    ("POST", "/api/peptides/summary"): "peptide_summary",
    ("POST", "/api/hla"): "hla_check",
    ("GET", "/api/quiz"): "quiz_due",
    ("POST", "/api/quiz/grade"): "quiz_grade",
    ("GET", "/api/curriculum"): "curriculum",
    ("GET", "/api/teach"): "teach",
    ("POST", "/api/sweep"): "run_sweep",
}


def make_handler(api: Api, token: str | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "NeoBrain"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quieter than the default
            if "?" in str(args[0] if args else ""):
                return

        # ------------------------------------------------------- helpers
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # This app is entirely self-contained; forbid outside resources so a
            # future edit cannot silently start leaking queries to a CDN.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, status: int = 200) -> None:
            self._send(status, json.dumps(obj, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _authorized(self, params: dict) -> bool:
            if not token:
                return True
            supplied = self.headers.get("X-NeoBrain-Token") or params.get("token")
            return secrets.compare_digest(str(supplied or ""), token)

        # --------------------------------------------------------- verbs
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

            if parsed.path in ("/", "/index.html"):
                try:
                    html = APP_HTML.read_bytes()
                except FileNotFoundError:
                    return self._send(500, b"app.html is missing", "text/plain")
                return self._send(200, html, "text/html; charset=utf-8")

            if not self._authorized(params):
                return self._json({"error": "unauthorized"}, 401)

            self._dispatch("GET", parsed.path, params, {})

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            if not self._authorized(params):
                return self._json({"error": "unauthorized"}, 401)

            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "invalid JSON body"}, 400)

            self._dispatch("POST", parsed.path, params, body)

        def _dispatch(self, verb: str, path: str, params: dict, body: dict) -> None:
            name = ROUTES.get((verb, path.rstrip("/") or "/"))
            if name is None:
                return self._json({"error": f"no route for {verb} {path}"}, 404)
            try:
                result = getattr(api, name)(params, body)
                self._json(result)
            except sqlite3.IntegrityError as e:
                # The append-only journal triggers land here — a refused write is
                # a correct outcome, not a server error.
                self._json({"error": str(e), "refused": True}, 409)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    return Handler


def serve(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    token: str | None = None,
    open_browser: bool = True,
    allow_remote: bool = False,
) -> None:
    """Run the dashboard until interrupted."""
    if host not in ("127.0.0.1", "localhost", "::1") and not allow_remote:
        raise SystemExit(
            f"Refusing to bind {host}: this database holds your unpublished notes "
            f"and reading history.\nIf you genuinely want it reachable from other "
            f"machines, pass --allow-remote, and set a token with --token.\n"
            f"For remote access over an untrusted network, prefer an SSH tunnel:\n"
            f"  ssh -L 8787:127.0.0.1:8787 you@your-machine"
        )

    config.ensure_dirs()
    httpd = ThreadingHTTPServer((host, port), make_handler(Api(), token))
    url = f"http://{host}:{port}/" + (f"?token={token}" if token else "")

    print(f"NeoBrain dashboard → {url}")
    print(f"  database: {config.DB_PATH}")
    if token:
        print("  token auth enabled")
    if allow_remote and host not in ("127.0.0.1", "localhost"):
        print("  ⚠ bound to a non-local interface — anyone who can reach this port "
              "can read your corpus")
    print("  Ctrl-C to stop")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
