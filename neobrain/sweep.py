"""The sweep: the job that keeps the brain current.

Run nightly. It queries every source, scores what comes back against your
interest profile, stores everything (including the low scorers — they are
searchable later even if they never surface), pulls open-access full text for
the best new papers, embeds new material, and writes a dated digest that the
agent reads at the start of your next session.

Failure policy: every source is wrapped. A dead API, a rate limit, or a
malformed record degrades the run, it does not abort it. Errors are recorded in
the `runs` table so `neobrain status` can tell you the sweep has been quietly
half-broken for a week.
"""

from __future__ import annotations

import datetime as dt
import time
import traceback
from typing import Any, Callable

from . import config, db, digest, evidence, extract, graph, journal, science, scoring
from .sources import clinicaltrials, europepmc, fulltext, http, preprints


def _log(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, flush=True)


def sweep(
    days: int | None = None,
    *,
    cfg: config.Config | None = None,
    do_trials: bool = True,
    do_fulltext: bool | None = None,
    do_embed: bool = True,
    quiet: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one full sweep. Returns a report dict."""
    cfg = cfg or config.load()
    config.ensure_dirs()
    started = time.monotonic()
    emit = progress or (lambda m: _log(m, quiet))

    lookback = days or cfg.lookback_days
    today = dt.date.today()
    since = (today - dt.timedelta(days=lookback)).isoformat()
    until = today.isoformat()
    run_date = today.isoformat()

    timeout = float(cfg.get("sweep.timeout", 30))
    delay = float(cfg.get("sweep.polite_delay", 0.34))
    max_per_query = int(cfg.get("sweep.max_per_query", 300))
    sources = cfg.get("sweep.sources", ["europepmc", "clinicaltrials", "preprints"])
    errors: list[str] = []

    http.reset_circuits()
    con = db.connect()
    emit(f"Sweeping {since} → {until} (lookback {lookback}d)")

    # ---------------------------------------------------------- literature
    new_papers: list[dict[str, Any]] = []
    seen_this_run: dict[str, dict[str, Any]] = {}

    if "europepmc" in sources:
        for bucket, queries in cfg.queries.items():
            for q in queries or []:
                try:
                    hits = europepmc.search(
                        q, since, until, max_results=max_per_query,
                        timeout=timeout, delay=delay,
                    )
                except Exception as e:  # noqa: BLE001 - one query must not kill the run
                    errors.append(f"epmc {q[:40]}: {e}")
                    emit(f"  ! query failed: {q[:50]} — {e}")
                    continue

                fresh = 0
                for rec in europepmc.iter_normalized(hits):
                    rec["score"], matched = scoring.score_record(
                        rec["title"], rec["abstract"], cfg.boosts, cfg.penalties,
                        is_oa=rec["is_oa"], pub_type=rec.get("pub_type"),
                    )
                    rec["matched"] = ", ".join(matched)
                    rec["buckets"] = bucket
                    prior = seen_this_run.get(rec["id"])
                    if prior:
                        prior["buckets"] = ",".join(
                            sorted(set(prior["buckets"].split(",")) | {bucket})
                        )
                        continue
                    seen_this_run[rec["id"]] = rec
                    if db.upsert_paper(con, rec):
                        new_papers.append(rec)
                        fresh += 1
                emit(f"  [{bucket}] {q[:50]:<50} {len(hits):>4} hits · {fresh:>3} new")
        con.commit()

    # ------------------------------------------------------------ preprints
    if "preprints" in sources:
        terms = _interest_terms(cfg)
        for server in ("biorxiv", "medrxiv"):
            try:
                recs = preprints.recent(server, since, until, timeout=timeout, delay=delay)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{server}: {e}")
                emit(f"  ! {server} failed: {e}")
                continue
            fresh = 0
            for rec in recs:
                if not preprints.matches_interest(rec, terms):
                    continue
                row = preprints.normalize(rec, server)
                if not row["id"] or row["id"] == "DOI:":
                    continue
                row["score"], matched = scoring.score_record(
                    row["title"], row["abstract"], cfg.boosts, cfg.penalties,
                    is_oa=True, pub_type="preprint",
                )
                row["matched"] = ", ".join(matched)
                row["buckets"] = "preprint_feed"
                if row["id"] in seen_this_run:
                    continue
                seen_this_run[row["id"]] = row
                if db.upsert_paper(con, row):
                    new_papers.append(row)
                    fresh += 1
            emit(f"  [{server}] {len(recs):>4} posted · {fresh:>3} relevant new")
        con.commit()

    # --------------------------------------------------------------- trials
    new_trials: list[dict[str, Any]] = []
    changed_trials: list[dict[str, Any]] = []
    if do_trials and "clinicaltrials" in sources:
        for term in cfg.trial_terms:
            try:
                studies = clinicaltrials.search(term, since, timeout=timeout, delay=delay)
            except Exception as e:  # noqa: BLE001
                errors.append(f"ctgov {term}: {e}")
                emit(f"  ! trials query failed: {term} — {e}")
                continue
            for t in studies:
                verdict = db.upsert_trial(con, t)
                if verdict == "new":
                    new_trials.append(t)
                elif verdict == "changed":
                    changed_trials.append(t)
            emit(f"  [trials] {term[:50]:<50} {len(studies):>4} hits")
        con.commit()

    # ------------------------------------------------------------ full text
    ft_added = ft_chars = 0
    want_fulltext = cfg.get("sweep.fetch_fulltext", True) if do_fulltext is None else do_fulltext
    if want_fulltext:
        try:
            ft_added, ft_chars = fulltext.backfill(
                con,
                limit=int(cfg.get("sweep.fulltext_top_n", 15)),
                min_score=int(cfg.get("sweep.fulltext_min_score", 8)),
                timeout=timeout, delay=delay,
            )
            emit(f"  [fulltext] {ft_added} papers · {ft_chars:,} chars")
        except Exception as e:  # noqa: BLE001
            errors.append(f"fulltext: {e}")
            emit(f"  ! full-text pass failed: {e}")

    # ------------------------------------------------------------ indexing
    # Chunking runs whether or not embeddings are configured — keyword search
    # and the entity graph both depend on it, and neither needs a model.
    embedded = 0
    try:
        from . import embeddings as emb
        embedded = emb.index_new(con, cfg, progress=emit)
        if embedded:
            emit(f"  [embed] {embedded} chunks embedded")
    except Exception as e:  # noqa: BLE001
        errors.append(f"embed: {e}")
        emit(f"  ! indexing pass failed: {e}")

    # ------------------------------------------------------- evidence grading
    graded = 0
    try:
        graded = evidence.grade_corpus(con)
        if graded:
            emit(f"  [grade] {graded} papers assigned an evidence tier")
    except Exception as e:  # noqa: BLE001
        errors.append(f"grade: {e}")

    # ---------------------------------------------------------- entity graph
    graph_report: dict[str, int] = {}
    try:
        graph_report = graph.index_chunks(con)
        if graph_report.get("mentions"):
            emit(f"  [graph] {graph_report['mentions']} mentions · "
                 f"{graph_report['entities']} entities")
    except Exception as e:  # noqa: BLE001
        errors.append(f"graph: {e}")
        emit(f"  ! graph pass failed: {e}")

    # ------------------------------------------------- structured extraction
    extracted = {}
    try:
        extracted = extract.extract_corpus(con, limit=40)
        if extracted.get("values"):
            emit(f"  [extract] {extracted['values']} values from "
                 f"{extracted['papers']} papers")
    except Exception as e:  # noqa: BLE001
        errors.append(f"extract: {e}")

    # --------------------------------------- routing to your open hypotheses
    leads = []
    try:
        leads = science.route_evidence(con, [p["id"] for p in new_papers] or None)
        if leads:
            emit(f"  [leads] {len(leads)} new paper(s) bear on your open hypotheses")
    except Exception as e:  # noqa: BLE001
        errors.append(f"routing: {e}")

    # ------------------------------------------------- contradiction scanning
    conflicts = 0
    try:
        conflicts = evidence.scan(con)
        if conflicts:
            emit(f"  [conflicts] {conflicts} passages may contradict stored beliefs")
    except Exception as e:  # noqa: BLE001
        errors.append(f"conflicts: {e}")

    unreachable = http.circuit_state()
    if unreachable:
        hosts = ", ".join(unreachable)
        errors.append(f"unreachable hosts: {hosts}")
        emit(f"\n  ! could not reach {hosts} — check your network, then re-run. "
             f"Nothing was lost; the sweep is idempotent.")

    duration = round(time.monotonic() - started, 1)
    db.record_run(
        con, "sweep",
        new_papers=len(new_papers), new_trials=len(new_trials),
        changed_trials=len(changed_trials), fulltext_added=ft_added,
        embedded=embedded, errors=errors, duration_s=duration,
    )

    path = digest.write(
        con, cfg,
        new_papers=new_papers, new_trials=new_trials, changed_trials=changed_trials,
        run_date=run_date, lookback=lookback, errors=errors,
        fulltext_added=ft_added, conflicts=conflicts,
    )

    # The sweep itself is an event worth remembering. A gap in this record is
    # how you find out the scheduler quietly stopped firing in March.
    journal.record(
        con,
        f"Sweep over the last {lookback}d: {len(new_papers)} new papers, "
        f"{len(new_trials)} new trials, {len(changed_trials)} trial changes, "
        f"{ft_added} full texts, {conflicts} candidate contradictions."
        + (f" Errors: {'; '.join(errors[:3])}" if errors else ""),
        kind="sweep", source="sweep", importance=2 if errors else 1,
    )
    con.close()

    emit(f"\n{len(new_papers)} new papers · {len(new_trials)} new trials · "
         f"{len(changed_trials)} trial changes · {duration}s")
    emit(f"Digest → {path}")

    return {
        "new_papers": len(new_papers),
        "new_trials": len(new_trials),
        "changed_trials": len(changed_trials),
        "fulltext_added": ft_added,
        "embedded": embedded,
        "graded": graded,
        "graph": graph_report,
        "conflicts": conflicts,
        "extracted": extracted,
        "leads": len(leads),
        "errors": errors,
        "digest": str(path),
        "duration_s": duration,
    }


def _interest_terms(cfg: config.Config) -> list[str]:
    """Terms used to filter the unfiltered preprint firehose."""
    explicit = cfg.interests.get("preprint_filter_terms")
    if explicit:
        return [str(t) for t in explicit]
    return [str(t) for t, w in cfg.boosts.items() if int(w) >= 3]


def main(argv: list[str] | None = None) -> int:
    """Entry point for `python -m neobrain.sweep`."""
    import argparse

    ap = argparse.ArgumentParser(description="Run the NeoBrain literature sweep")
    ap.add_argument("--days", type=int, default=None, help="override lookback_days")
    ap.add_argument("--no-trials", action="store_true")
    ap.add_argument("--no-fulltext", action="store_true")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    try:
        sweep(
            days=args.days,
            do_trials=not args.no_trials,
            do_fulltext=False if args.no_fulltext else None,
            do_embed=not args.no_embed,
            quiet=args.quiet,
        )
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
