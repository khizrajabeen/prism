# NeoBrain

A local research brain for **neoantigen cancer vaccines and drug discovery**.

It runs on your machine. It sweeps the literature and trial registries every
night, stores everything in a database you own, learns what you care about,
remembers your project across sessions, teaches you the field, and refuses to
tell you anything it cannot source.

```
                    ┌──────────────────────────────────────┐
   Europe PMC ─┐    │  sweep  →  score  →  store  →  digest │
   bioRxiv     ├───▶│                                      │
   medRxiv     │    │            brain.db                  │
   CT.gov      ┘    │  papers · full text · trials ·       │
                    │  chunks · beliefs · sessions · cards │
   your PDFs ──────▶└──────────────┬───────────────────────┘
                                   │
                    hybrid retrieval (BM25 + vectors, RRF)
                                   │
                    ┌──────────────▼───────────────────────┐
                    │   CLI          MCP server            │
                    │   neobrain …   any agent client      │
                    └──────────────┬───────────────────────┘
                                   │
                         your model, grounded and cited
```

---

## Quick start

```bash
git clone <this repo> neobrain && cd neobrain
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

neobrain init                 # directories, database, starter index
neobrain doctor               # check the install, see what is optional
neobrain sweep --days 30      # backfill a month of literature — takes a few minutes
neobrain digest               # read what it found
neobrain brief                # exactly what your agent reads at session start
```

Then fill in `memory/CORE.md`. Five minutes on that file changes the quality of
every answer afterwards, because it is the difference between an assistant that
knows you are a second-year PhD student with no wet lab access and one that
guesses.

## What you get

| | |
|---|---|
| **Live literature** | Europe PMC (journals **and** preprints), bioRxiv/medRxiv direct feed, ClinicalTrials.gov v2 with **status-change history** |
| **Full text, not just abstracts** | Open-access papers are pulled as structured sections — the Methods are where the protocol detail lives |
| **Your own PDFs** | Save a paywalled paper into `inbox/`, ingest it into the same tables |
| **Hybrid retrieval** | BM25 keyword + optional local vectors, fused by reciprocal rank |
| **Three-tier memory** | Core (always loaded) / semantic (retrieved) / episodic (searched) |
| **An approval gate** | The agent proposes memory edits; you approve diffs. It cannot write to memory itself |
| **A sourced belief store** | Claims with confidence, provenance, and a review date |
| **A curriculum** | Ten modules from antigen presentation to study design, each with a checkpoint task |
| **Spaced repetition** | SM-2 cards built from your own reading |
| **Agent tools** | An MCP server, so Claude Desktop / Claude Code / any client gets all of it |

Two dependencies (`requests`, `PyYAML`). Everything else is optional and the
system degrades gracefully without it.

---

## Daily use

```bash
neobrain brief                          # start here, every session
neobrain ask "does class II inclusion improve vaccine responses?"
neobrain search "HLA LOH detection" -k 8
neobrain search --papers "KRAS G12D vaccine"
neobrain paper MED:39012345 --methods    # just the protocol detail
neobrain mark MED:39012345 --state read --rating 5 --note "the control design I want"
neobrain teach 05                        # curriculum module 5, grounded in your corpus
neobrain quiz                            # spaced repetition
neobrain review                          # approve or reject the agent's memory edits
neobrain status
```

`neobrain ask` does not answer the question — it builds a **citable evidence
pack**. That is the point: the model reads it and answers on top of it, and
every claim in the answer traces to a passage you can open.

## Connecting an agent

**Claude Code**

```bash
claude mcp add neobrain -- neobrain mcp
```

Then add `AGENT.md` to your `CLAUDE.md`, or point at it directly.

**Claude Desktop** — in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "neobrain": {
      "command": "neobrain",
      "args": ["mcp"],
      "env": { "NEOBRAIN_HOME": "/absolute/path/to/neobrain" }
    }
  }
}
```

The agent gets ~20 tools: `brief`, `evidence`, `search`, `get_paper`,
`fetch_fulltext`, `trials`, `run_sweep`, `propose_memory_edit`,
`teaching_packet`, `due_review_cards`, and so on. It can propose memory edits.
**It cannot apply them.** See `AGENT.md` for the full operating contract.

## Scheduling the sweep

```bash
# Linux/macOS — 06:00 daily
crontab -e
0 6 * * * cd /path/to/neobrain && .venv/bin/neobrain sweep >> logs/sweep.log 2>&1
```

systemd timer and Windows Task Scheduler instructions are in
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md), along with `scripts/` that set both up.

---

## How the memory actually works

Three tiers, because "read all my memory at startup" stops working after about
a week — the context window fills with stale material and answer quality drops
in a way that is hard to attribute.

1. **Core** (`memory/CORE.md`) — identity, project, standing preferences, the
   handful of beliefs that shape every answer. Always loaded. ~2k token cap,
   enforced by `neobrain doctor` nagging you.
2. **Semantic** (`knowledge/*.md` + the `beliefs` table) — durable domain
   knowledge, retrieved by topic. Beliefs carry confidence, sources, and a
   **review date**, so a 2026 claim gets re-examined rather than hardening into
   dogma.
3. **Episodic** (`brain.db`, `digests/`) — everything ever seen. Searched,
   never loaded wholesale.

`neobrain brief` is the bounded startup payload: core in full, corpus state,
the latest digest, pending approvals, beliefs due for review, and where the last
session left off. Everything else is a retrieval away.

### The approval gate is the important part

The agent writes proposals. You see diffs. You decide.

```
$ neobrain review
────────────────────────────────────────────────────────────────
#3  knowledge → neoantigen_pipeline.md
why      Two 2026 papers report class II epitopes at higher frequency
         in vaccine-induced responses than the section currently implies
evidence MED:39112233, PPR:PPR845221

--- a/neoantigen_pipeline.md
+++ b/neoantigen_pipeline.md
@@ -142,6 +142,11 @@
+Class II contribution appears larger than early class I-focused
+pipelines assumed (MED:39112233; and PPR:PPR845221, preprint,
+unreplicated).

[a]pply / [r]eject / [s]kip / [q]uit ?
```

An agent that silently rewrites its own beliefs will drift, and you will not
notice until it confidently tells you something wrong in a paper draft. This
gate costs you thirty seconds a day.

---

## Retrieval: why hybrid, not vectors

Keyword and vector search fail in opposite directions here.

BM25 nails `HLA-A*02:01`, `NetMHCIIpan-4.3`, `Adpgk`, `NSG-SGM3` — the rare
tokens that carry the meaning — and misses paraphrase. Vectors find "peptide
presentation on class II" when you asked about "CD4 epitope display" and blur
the alleles together. Reciprocal-rank fusion takes the union without needing the
two score scales to be comparable, which they are not.

So **start with `embeddings.backend: none`.** Keyword-only retrieval over this
literature is genuinely good. Add vectors when you notice yourself failing to
find things you know are in there:

```yaml
# config/settings.yaml
embeddings:
  backend: sentence-transformers
  model: pritamdeka/S-PubMedBert-MS-MARCO   # biomedical; or all-MiniLM-L6-v2 to start
```

```bash
pip install -e ".[embeddings]"
neobrain embed --rebuild
```

Vectors are stored as normalized float32 blobs in SQLite and scanned brute
force. That is milliseconds for tens of thousands of chunks and saves you a
vector database. When the corpus outgrows it, swap `neobrain/retrieve.py` and
nothing else changes.

---

## Full-text acquisition, and the line not to cross

Open access is fetchable and legal: Europe PMC OA subset, PMC, bioRxiv/medRxiv.
The sweep pulls JATS XML for the best new OA papers and splits it into labelled
sections, so `neobrain paper <id> --methods` gives you the protocol detail that
abstracts always omit.

**Paywalled content is not bulk-downloadable.** Route it through your
institutional proxy in a browser, save the PDF into `inbox/`, and:

```bash
pip install pypdf
neobrain ingest-pdf --inbox --archive
```

Do not build scraping around publisher paywalls. It gets your IP range blocked,
which punishes everyone at your institution, and the ingestion path above works
fine.

---

## Tuning it

`config/interests.yaml` is the file you touch most. It drives the queries, the
scoring weights, and the digest threshold. Everything is transparent and
additive — you can always ask why something surfaced:

```bash
$ neobrain score "Class II neoepitopes drive durable responses in MC38" \
    --abstract "We show that ..."
score 21 (surfaced, threshold 6) — matched: neoepitope, MHC class II, MC38
```

Retune weekly at first. A digest you skim and ignore is worse than no digest,
because it trains you to ignore it.

---

## Deployment, and the honest tradeoff

Everything above is genuinely local — your notes, your PDFs, your database,
your file access. The **reasoning model** is where "local" and "advanced" pull
against each other:

- **Hybrid (recommended to start):** local data, local memory, local files;
  a frontier model over API for reasoning and code. Only the current question
  and the retrieved snippets leave the machine.
- **Fully local:** open-weight models have closed much of the gap and are
  credible for agentic coding. On long multi-step tool loops and dense
  immunology reasoning they still lose ground — and you will feel it most on
  exactly the hard questions you built this for.

A practical split: a local model for the cheap high-volume work (triaging 400
abstracts a night, tagging, deduplication) and a frontier model for synthesis,
code, and anything you will rely on. `docs/DEPLOYMENT.md` covers both.

## Letting it use your machine

You want the agent to operate the laptop. Do it in this order, not all at once:

1. **Filesystem scoped to one directory.** `~/neobrain/` and `workspace/`.
   Not your home directory, not Documents.
2. **Named tools before a shell.** The MCP tools cover the real work; a raw
   `bash -c` is a much larger blast radius while you are still learning its
   failure modes.
3. **Approval gate on writes and deletes.** Reads can be automatic.
4. **Network allowlist** for the APIs you actually use.
5. **Git the whole folder.** `git init` now. When the agent corrupts a knowledge
   file at 2am — and it will, once — you want `git diff`.

Widen the permissions after a few weeks of watching it behave, not before.
Full detail in [`docs/SECURITY.md`](docs/SECURITY.md).

---

## Layout

```
neobrain/
├── AGENT.md                   the agent's operating contract — read this
├── config/
│   ├── interests.yaml         what you care about; tune weekly
│   ├── settings.yaml          system settings
│   └── curriculum.yaml        10-module learning path with checkpoints
├── memory/CORE.md             loaded in full every session
├── knowledge/                 13 curated domain notes, retrieved on demand
├── digests/YYYY-MM-DD.md      what changed, written by the sweep
├── inbox/                     drop PDFs here
├── workspace/                 the only place the agent writes freely
├── neobrain/                  the package
│   ├── sweep.py  digest.py  scoring.py  retrieve.py  embeddings.py
│   ├── memory.py  tutor.py  db.py  cli.py  mcp_server.py
│   └── sources/  europepmc · clinicaltrials · preprints · fulltext · local
├── docs/  DEPLOYMENT.md  SECURITY.md  WORKFLOWS.md
└── brain.db                   SQLite: everything
```

## Tests

```bash
pip install pytest && pytest
```

The tests cover the parts that would fail silently: scoring, chunking, rank
fusion, SM-2 scheduling, proposal diffs and application, and the FTS escaping
that otherwise turns a query with a hyphen into a syntax error.

---

## The rule that makes it worth using

The agent attaches a source to every factual claim, or says it does not know.

A confabulating research assistant is worse than none, because the error enters
your thesis without a trail back to where it came from. Everything in this
repository — the belief store, the approval gate, the citation-carrying context
packs, the staleness warnings — exists to enforce that one property.
