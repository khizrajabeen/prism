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
| **Hybrid retrieval** | BM25 + optional local vectors + an entity graph, fused by reciprocal rank |
| **Multi-hop reasoning** | Questions whose answer spans papers, via graph-seeded retrieval |
| **Memory that cannot be erased** | An append-only journal the database itself refuses to mutate |
| **Beliefs that change without forgetting** | Bitemporal versioning: superseded, never overwritten |
| **Procedural memory** | Rules learned from your corrections, surfaced every session |
| **Contradiction detection** | New evidence is scanned against what you believe, and tensions are queued |
| **Evidence grading** | Every source gets a tier, so "established" and "one preprint" stop looking alike |
| **An approval gate** | The agent proposes curated memory edits; you approve diffs |
| **A curriculum** | Ten modules from antigen presentation to study design, each with a checkpoint task |
| **Spaced repetition** | SM-2 cards built from your own reading |
| **Agent tools** | An MCP server exposing 30 tools to Claude Desktop / Claude Code / any client |

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

neobrain remember "..." --kind correction  # permanent, append-only, immediate
neobrain recall "montanide"                # search everything ever recorded
neobrain rule add "<when>" "<do this>"     # procedural memory
neobrain history 7                         # every version of a belief
neobrain history --as-of 2026-03-01        # what did I believe then?
neobrain graph CT26                        # what connects to what
neobrain graph --path CT26 immunodominance # multi-hop
neobrain conflicts --scan                  # evidence against stored beliefs

neobrain teach 05                        # curriculum module 5, grounded in your corpus
neobrain quiz                            # spaced repetition
neobrain review                          # approve or reject the agent's memory edits
neobrain backup                          # snapshot, safe while in use
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

The 2026 agent-memory literature has converged on three scopes — **episodic**
(what happened), **semantic** (what is true), **procedural** (how to work) —
and on one structural rule from the temporal-knowledge-graph systems: *never
delete, invalidate*. NeoBrain implements all four ideas, with the addition that
matters most for research: nothing enters the authoritative tiers unreviewed.

### Episodic — the journal, which cannot be erased

```bash
neobrain remember "Montanide sequesters T cells at the injection site — avoid it" \
    --kind preference --importance 4
neobrain recall "montanide"
```

Everything the brain learns lands here immediately, with no approval step, from
the first run. The table is append-only and **the database enforces it**:

```
$ sqlite3 brain.db "DELETE FROM journal WHERE id=2"
Error: the journal is append-only: entries are never deleted
```

Not a convention, a trigger. Neither a confused agent nor you at 2am can revise
what was recorded. Corrections are made by *adding* an entry, so the history of
being wrong survives alongside the fix.

### Semantic — beliefs that change without forgetting

Beliefs are versioned, never overwritten. A revision invalidates the old row and
inserts a new one sharing a lineage:

```
$ neobrain history 3
  v1 #2 [moderate] (superseded)
    Class II epitopes contribute little to vaccine responses
    asserted 2026-02-11 · invalidated 2026-08-14 · two 2026 cohorts report higher
    class II frequencies than earlier pipelines assumed
→ v2 #3 [moderate] (active)
    Class II epitopes contribute substantially to vaccine-induced responses
```

Two timestamps, deliberately: `valid_from`/`valid_until` for when the claim was
true of the world, `asserted_at`/`invalidated_at` for when *we* thought so.
That is what makes `neobrain history --as-of 2026-03-01` able to answer "what
did I believe when I wrote that methods section?" — the question that actually
comes up, months later, in front of a reviewer.

### Procedural — rules learned from your corrections

```bash
neobrain rule add "I design a mouse vaccine study" \
                  "check for an adjuvant-alone arm before anything else"
```

Not a fact about immunology, so it does not belong in `beliefs`. It is a
procedure, and procedures are what turn a correction into a mistake that does
not recur. Rules appear at the top of every session brief.

### Core — the always-loaded tier

`memory/CORE.md`: identity, project, standing preferences. ~2k token cap,
enforced by `neobrain doctor` nagging you.

### Why not just load everything

"Read all my memory at startup" works for about a week. Then the context window
fills with stale material and answer quality drops in a way that is hard to
attribute. `neobrain brief` is the bounded payload — core in full, rules, recent
corrections, corpus state, latest digest, pending approvals, beliefs due for
review, open contradictions, where you left off. Everything else is one
`recall` or `search` away, and nothing is lost.

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

## Reasoning, not just retrieval

### Three retrieval legs, because they fail differently

BM25 nails `HLA-A*02:01`, `NetMHCIIpan-4.3`, `Adpgk`, `NSG-SGM3` — the rare
tokens that carry the meaning — and misses paraphrase. Vectors find "peptide
presentation on class II" when you asked about "CD4 epitope display" and blur
the alleles together. Neither can answer a question whose evidence spans papers.

The third leg is an **entity graph**. Ask *"why would a vaccine response be
invisible in CT26?"* — a question sharing almost no wording with its answer —
and the graph seeds on `CT26`, spreads activation, and surfaces the passage
about gp70/AH1 immunodominance:

```
$ neobrain graph --path CT26 immunodominance
CT26 → AH1 → immunodominance
```

Entity extraction is a curated domain gazetteer plus regex for things with
strict formats (HLA alleles, mutation notation, tool names), **not** an LLM.
LLM extraction hallucinates edges, and in a research assistant a fabricated
relationship between a gene and a phenotype is exactly the failure you cannot
afford. Aliases normalize (`HLA LOH` ≡ `HLA loss of heterozygosity`) so the
graph does not fragment. Edges are weighted co-occurrence, and the tool says so
every time it prints a path: *a co-occurrence path is a lead, not a finding.*

Questions are also **decomposed** — split on conjunctions, plus one query per
named entity — and the sub-results fused, which is what makes multi-hop
questions retrievable at all. MMR diversification then stops the top ten
passages being ten near-copies from one paper.

### Evidence grading

Every paper gets a tier from its own text: randomized trial (5) down through
cohort, in vivo, in vitro, preprint, to review/editorial (0). Evidence packs
lead with a read on collective strength:

```
**Evidence strength:** established: multiple higher-tier sources agree.
7 passages from 4 distinct sources.
```

so the instruction to label claims *established / contested / single-paper* has
something underneath it besides vibes.

### Contradiction detection

Every sweep scans new evidence against your stored beliefs and queues the
tensions:

```
$ neobrain conflicts
#4 against belief: MC38 neoantigen vaccination improves survival
cue: explicit negative result; could not confirm (shared: MC38, neoantigen)
in: Failure to replicate MC38 vaccine benefit (tier 3)
    In MC38 tumours, neoantigen vaccination did not improve survival, and we
    were unable to confirm the previously reported benefit.
```

The detector is shared entities + negation/reversal cues + numeric divergence —
deliberately over-sensitive and explicitly *not* a verdict. Dismissing a false
positive costs five seconds; missing a real contradiction costs you a wrong
claim in a paper. Scientific claim verification (SciFact-style stance models)
would improve precision here and is the obvious upgrade path.

### Reciprocal-rank fusion

RRF takes the union of the legs without needing their score scales to be
comparable, which they are not.

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
│   ├── journal.py             append-only memory (immutable)
│   ├── graph.py               entity graph + personalized PageRank
│   ├── evidence.py            evidence tiers + contradiction detection
│   ├── memory.py  tutor.py  db.py  cli.py  mcp_server.py
│   └── sources/  europepmc · clinicaltrials · preprints · fulltext · local
├── docs/  DEPLOYMENT.md  SECURITY.md  WORKFLOWS.md
└── brain.db                   SQLite: everything
```

## Design notes and prior art

The memory design follows where the 2026 agent-memory field landed, adapted for
research work where a wrong claim is expensive:

- **Three memory scopes** — episodic / semantic / procedural — are now the
  standard taxonomy across [Mem0, Zep, Letta and
  successors](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/).
- **Invalidate, never delete.** [Zep's Graphiti](https://www.getzep.com/ai-agents/temporal-knowledge-graph/)
  gives every edge a validity interval and writes an invalidation timestamp
  instead of dropping the row, so the graph can say what was believed and when.
  NeoBrain's beliefs are bitemporal for the same reason, and the journal goes
  further by being immutable at the storage layer.
- **Memory-as-OS.** [Letta/MemGPT](https://github.com/NirDiamant/Agent_Memory_Techniques)
  splits main context from recall and archival stores, paged on demand — which
  is what `brief` + `recall` + `search` are.
- **Graph-seeded multi-hop retrieval.** HippoRAG and the
  [GraphRAG](https://atlan.com/know/advanced-rag-techniques/) family seed an
  entity graph from the query and spread activation. NeoBrain does this with
  deterministic extraction rather than LLM triple extraction, trading recall for
  the guarantee that no edge is fabricated.
- **Stance-based claim verification.** [SciFact](https://aclanthology.org/2020.emnlp-main.609/)
  frames contradiction detection as SUPPORTS / REFUTES / NOINFO. The detector
  here is the deterministic precursor to that, and a local stance model is the
  natural upgrade.

The one deliberate divergence: most of these systems let the agent write to
memory autonomously, and [research on memory
contamination](https://arxiv.org/pdf/2605.28009) is now catching up with why
that is risky. Here, raw capture is automatic and immutable, but promotion to
*authoritative* memory always passes through you.

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
