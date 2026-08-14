"""The daily digest — what changed, written for the agent to read at startup.

A digest is not a list of papers. It is a briefing: what is new, what it
contradicts, what needs a decision from you. The "For the agent" block at the
end is an instruction set, deliberately written in the imperative, because the
agent reads this file before it reads anything else.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import config, db


def _snippet(text: str | None, n: int = 300) -> str:
    return " ".join((text or "").split())[:n]


def write(
    con: sqlite3.Connection,
    cfg,
    *,
    new_papers: list[dict[str, Any]],
    new_trials: list[dict[str, Any]],
    changed_trials: list[dict[str, Any]],
    run_date: str,
    lookback: int,
    errors: list[str] | None = None,
    fulltext_added: int = 0,
    conflicts: int = 0,
) -> Path:
    config.DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DIGEST_DIR / f"{run_date}.md"

    threshold = cfg.threshold
    max_per_bucket = int(cfg.get("digest.max_per_bucket", 15))
    surfaced = sorted(
        [p for p in new_papers if p.get("score", 0) >= threshold],
        key=lambda p: -p.get("score", 0),
    )

    lines: list[str] = [
        f"# Research digest — {run_date}",
        "",
        f"Window: last {lookback} day(s). "
        f"{len(new_papers)} new records, {len(surfaced)} above threshold (score ≥ {threshold}). "
        f"{len(new_trials)} new trials, {len(changed_trials)} trial status changes. "
        f"{fulltext_added} full texts ingested.",
        "",
    ]

    if not surfaced and not new_trials and not changed_trials:
        lines += [
            "Nothing above threshold. That is a normal result for a single day — "
            "it is not a sign the sweep is broken. Check `neobrain status` if it "
            "repeats for more than a week.",
            "",
        ]

    # ------------------------------------------------------------- papers
    if surfaced:
        by_bucket: dict[str, list[dict]] = {}
        for p in surfaced:
            for bucket in (p.get("buckets") or "unsorted").split(","):
                by_bucket.setdefault(bucket, []).append(p)

        for bucket, papers in by_bucket.items():
            lines.append(f"## {bucket.replace('_', ' ').title()}")
            lines.append("")
            for p in papers[:max_per_bucket]:
                venue = "preprint" if p.get("source") == "preprint" else (p.get("journal") or "")
                oa = " · OA" if p.get("is_oa") else " · paywalled"
                lines.append(f"**[{p.get('score')}] {p.get('title')}**  ")
                lines.append(f"{venue} · {p.get('pub_date', '')}{oa} · `{p['id']}` · [link]({p.get('url', '')})  ")
                if p.get("matched"):
                    lines.append(f"matched: {p['matched']}  ")
                snip = _snippet(p.get("abstract"), 320)
                if snip:
                    lines.append(f"{snip}…")
                lines.append("")

    # ------------------------------------------------------------- trials
    if new_trials:
        lines += ["## New trials", ""]
        for t in new_trials[:25]:
            lines.append(
                f"- **{t['nct_id']}** ({t.get('status', '')}, {t.get('phase') or 'phase n/a'}) — "
                f"{t.get('title', '')} · {t.get('sponsor', '')} · [link]({t.get('url', '')})"
            )
        lines.append("")

    if changed_trials:
        lines += ["## Trial status changes", ""]
        for t in changed_trials[:25]:
            hist = con.execute(
                "SELECT field, old_value, new_value FROM trial_history "
                "WHERE nct_id=? ORDER BY id DESC LIMIT 3",
                (t["nct_id"],),
            ).fetchall()
            deltas = "; ".join(f"{h['field']}: {h['old_value']} → {h['new_value']}" for h in hist)
            lines.append(f"- **{t['nct_id']}** — {deltas or 'updated'} · {t.get('title', '')}")
        lines.append("")

    # ------------------------------------------------------ contradictions
    conflict_rows = con.execute(
        """SELECT c.id, c.cue, c.passage, b.claim, p.title, p.url, p.evidence_tier
           FROM conflicts c
           JOIN beliefs b ON b.id = c.belief_id
           LEFT JOIN papers p ON p.id = c.paper_id
           WHERE c.status='open' ORDER BY COALESCE(p.evidence_tier,0) DESC, c.id DESC LIMIT 10"""
    ).fetchall()
    if conflict_rows:
        lines += [
            "## Evidence that may contradict what we believe",
            "",
            "Candidates from an imprecise detector — read the passage before "
            "acting. Resolve with `neobrain conflicts`.",
            "",
        ]
        for c in conflict_rows:
            lines.append(f"**`#{c['id']}` against:** _{_snippet(c['claim'], 160)}_  ")
            lines.append(f"cue: {c['cue']}  ")
            if c["title"]:
                tier = f" · tier {c['evidence_tier']}" if c["evidence_tier"] is not None else ""
                lines.append(f"in: {c['title']}{tier} · [link]({c['url'] or ''})  ")
            lines.append(f"> {_snippet(c['passage'], 320)}…")
            lines.append("")

    # ---------------------------------------------------------- open loops
    pending = con.execute(
        "SELECT id, kind, target, rationale FROM proposals WHERE status='pending' ORDER BY id"
    ).fetchall()
    if pending:
        lines += ["## Memory edits awaiting your approval", ""]
        for p in pending[:20]:
            lines.append(f"- `#{p['id']}` **{p['kind']}** → {p['target']} — {_snippet(p['rationale'], 160)}")
        lines += ["", "Review with `neobrain review` before trusting anything downstream of them.", ""]

    due = con.execute(
        "SELECT id, claim, confidence, review_on FROM beliefs "
        "WHERE status='active' AND review_on IS NOT NULL AND review_on<=? ORDER BY review_on",
        (db.today(),),
    ).fetchall()
    if due:
        lines += ["## Beliefs due for re-check", ""]
        for b in due[:15]:
            lines.append(f"- `#{b['id']}` ({b['confidence']}) {_snippet(b['claim'], 180)} — due {b['review_on']}")
        lines.append("")

    if errors:
        lines += ["## Sweep errors", ""]
        for e in errors[:12]:
            lines.append(f"- {e}")
        lines.append("")

    # ------------------------------------------------------- agent contract
    lines += [
        "## For the agent",
        "",
        "1. Read the entries above. For anything that **changes, sharpens, or "
        "contradicts** a claim in `memory/CORE.md`, `knowledge/*.md`, or the "
        "`beliefs` table, write a proposal (`neobrain propose`) — never edit "
        "memory directly.",
        "2. State contradictions explicitly rather than quietly preferring the "
        "newer paper. A single preprint does not overturn a replicated result.",
        "3. Anything below threshold is still in `brain.db`. Search wider with "
        "`neobrain search` before concluding there is no evidence.",
        "4. When you cite any of this to me, cite the paper id and the link, so "
        "I can open it. No claim without a source.",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def latest(n: int = 1) -> list[Path]:
    """The n most recent digest files, newest first."""
    if not config.DIGEST_DIR.exists():
        return []
    return sorted(config.DIGEST_DIR.glob("*.md"), reverse=True)[:n]


def read_latest() -> str:
    files = latest(1)
    return files[0].read_text(encoding="utf-8") if files else ""
