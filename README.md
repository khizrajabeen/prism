# NeoBrain

A local research brain for **neoantigen cancer vaccines and drug discovery**.

It runs on your machine. It sweeps the literature nightly, holds your
hypotheses and experiments, checks the sentences in your draft against your own
library, and refuses to tell you anything it cannot source.

```
        ┌───────────┐     ┌────────────┐     ┌──────────────┐
        │ QUESTION  │────▶│  EVIDENCE  │────▶│  HYPOTHESIS  │
        │ Ask/Check │     │  Research  │     │  falsifier   │
        └───────────┘     └────────────┘     │  required    │
              ▲                  │           └──────┬───────┘
              │           routing│                  │
      ┌───────┴──────┐    ┌──────▼───┐    ┌─────────▼────────┐
      │    BELIEF    │◀───│  RESULT  │◀───│    EXPERIMENT    │
      │  versioned,  │    │  vs the  │    │ prediction saved │
      │ never erased │    │prediction│    │  before it runs  │
      └──────────────┘    └──────────┘    └──────────────────┘
```

Every table in the database attaches to a node of that loop. A capability that
attaches to none of them is a utility, not part of the brain.

---

## Where this sits

Three kinds of tool exist near this problem, and each is missing a different half.

|  | Knows the literature | Knows *your* work |
|---|---|---|
| **Literature tools** — Elicit, Scite, Consensus, ResearchRabbit, Undermind | ✅ | ❌ |
| **Electronic lab notebooks** — Benchling, LabArchives, eLabFTW, SciNote | ❌ | ✅ |
| **AI co-scientists** — Google Co-Scientist, FutureHouse, Sakana | ✅ | ❌ *(its hypotheses, not yours)* |
| **NeoBrain** | ✅ | ✅ |

ELNs capture what you did with excellent traceability and know nothing about
what is published. Literature tools rank papers beautifully and have never
heard of your experiment. AI co-scientists generate hypotheses *for* you —
which is a different offer from holding *yours* to a falsifier and a
pre-registered prediction.

The empty quadrant is the product.

### What we do that none of them do

**1. Extraction with sentence-level provenance, and "not reported" as data.**
Elicit extracts into custom columns with an LLM. Ours is pattern-based: lower
recall on odd phrasing, zero fabrication, and **every cell opens the sentence
it came from**. The difference that matters is the empty cell — in an LLM table
a blank is ambiguous between "the paper did not report it" and "the model
missed it". Here `not reported` is an explicit value, which turns a column into
a finding:

> 2 of 3 MC38 vaccine papers do not report a power calculation.

**2. The methods audit.** Every paper scored against what ARRIVE and CONSORT
have asked for since 2010 — group sizes, randomization, blinding, power,
controls, named statistical test, ethics — each with the sentence that
satisfied it. A checklist, deliberately not a score: a score invites ranking
papers by reporting, which correlates with rigour without being it.

**3. Quality-weighted evidence.** Scite's own documentation concedes that a
supporting citation "might come from a paper where the experimental evidence is
weak" — it counts citations knowing nothing about the citing study's design.
We weight support by `design tier × reporting completeness`, and show both
components rather than one opaque number. We will never match 1.6B citations;
we are answering a different question.

**4. Evidence routed to your own hypotheses.**

```
hypothesis #1: Class II epitopes improve durability of MC38 vaccine responses
  ← Class II epitopes improve durable responses to MC38 neoantigen vaccination
    auto-suggested: shares MC38, MHC class II
```

Structurally unavailable to every tool above, because it needs the work ledger
in the same database as the corpus.

Full analysis, including the interface principles that follow from it:
[`docs/DESIGN.md`](docs/DESIGN.md).

---

## Quick start

```bash
git clone <this repo> neobrain && cd neobrain
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

neobrain init                 # directories, database, starter index
neobrain doctor               # what is installed, what is optional
neobrain sweep --days 30      # backfill a month of literature
neobrain web                  # → http://127.0.0.1:8787
```

Then fill in `memory/CORE.md`. Five minutes there changes every answer
afterwards — it is the difference between an assistant that knows you are a
second-year PhD student without wet-lab access and one that guesses.

Two dependencies (`requests`, `PyYAML`). Everything else is optional and the
system degrades gracefully without it.

---

## The web portal

```bash
neobrain web
```

Six tabs, one per stage of the loop. Single-file HTML served by the standard
library — no framework, no CDN, no build step, because a research tool you
cannot start in three years is not durable.

| Tab | Answers | Inside |
|---|---|---|
| **Today** | What should I do now? | Voice console, what the work needs, what needs your decision, new evidence on your hypotheses, what's new in the literature |
| **Ask** | What does the evidence say? | Cited answer scaffold, **claim/draft checking**, raw evidence packs |
| **Research** | What exists on this topic? | Keywords → clarifying questions → every journal → screening → export |
| **Projects** | Where is my work? | Hypotheses (falsifier required), experiments (prediction first), decisions |
| **Library** | What have I read? | Search, **extraction matrix**, **methods audit**, digests, entity graph |
| **Bench** | How do I do this? | Peptide tools, model registry, molecular screen |
| **Memory** | What do I know? | Approvals, conflicts, journal, beliefs, rules, spaced repetition |

Binds `127.0.0.1` and refuses any other interface without `--allow-remote`.
For remote access, tunnel: `ssh -L 8787:127.0.0.1:8787 you@your-machine`.

### Voice

Web Speech API, no dependency. Commands: `what's next` · `search <terms>` ·
`check <sentence>` · `ask <question>` · `remember <something>` · `open <tab>`.

Deliberately a small fixed grammar rather than free-form intent guessing — a
voice interface that mishears "exclude" as "include" and acts on it silently is
worse than none. **Chrome and Edge stream audio to a cloud service for
recognition**; the UI says so. Synthesis is on-device. Local Whisper path in
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

## Research: keywords in, a screened list out

```bash
neobrain discover search "neoantigen vaccine pancreatic"
```

It asks only what your keywords have **not** already answered — type
`MC38 mouse class II ELISpot randomized` and it skips four of six questions —
then searches **OpenAlex (~250M works, every journal), PubMed (MeSH,
publication types) and Europe PMC (preprints, OA full text)** at once.

Results are deduplicated across sources (DOI first, then title, so a record
arriving without a DOI still merges), screened against inclusion/exclusion
criteria with nothing discarded, and exportable as BibTeX / RIS / CSV /
Markdown. Open-access PDFs download via the Unpaywall data OpenAlex carries.

```bash
neobrain discover snowball 10.1038/s41586-023-06063-y --direction forward
```

Forward citations are how you learn your seed paper was refuted in 2025.

---

## Accuracy: how it earns trust

**Every answer carries its shape before its content:**

```
Evidence strength: established — multiple higher-tier sources agree
7 passages · 4 distinct sources · tiers 5:1 4:2 2:1
Corpus coverage: 31 papers mention "class II" (2019 – 2026)
```

**Check a sentence before you publish it:**

```bash
neobrain check "Prophylactic vaccination reliably predicts therapeutic efficacy"
```

Verdicts are hedged by construction — `likely-supported`,
`possibly-contradicted`, `needs-review`, `no-evidence`, `unverifiable-number` —
because this is lexical analysis over your corpus, not entailment detection. An
earlier version returned SUPPORTED/CONTRADICTED and was wrong in both
directions on real examples. Where it *is* reliable is where the risk lives:
your corpus is silent, your number has no numeric source, here are the three
passages to read.

---

## Memory that cannot be lost

**Episodic — append-only, enforced by the database:**

```
$ sqlite3 brain.db "DELETE FROM journal WHERE id=2"
Error: the journal is append-only: entries are never deleted
```

Not a convention, a trigger. Corrections are made by *adding*, so the history
of being wrong survives alongside the fix.

**Semantic — versioned, never overwritten:**

```
$ neobrain history 3
  v1 #2 [moderate] (superseded)
    Class II epitopes contribute little to vaccine responses
    asserted 2026-02-11 · invalidated 2026-08-14 · two 2026 cohorts report higher…
→ v2 #3 [moderate] (active)
    Class II epitopes contribute substantially to vaccine-induced responses
```

Bitemporal: `valid_from`/`valid_until` for when the claim was true of the
world, `asserted_at`/`invalidated_at` for when *we* thought so. That is what
makes `neobrain history --as-of 2026-03-01` able to reconstruct the state of
knowledge behind a decision — the question that arrives months later in front
of a reviewer.

**Procedural — rules learned from your corrections**, surfaced every session.

**The approval gate.** The agent proposes; you approve diffs with
`neobrain review`. There is no tool that lets it apply one — asserted by a test.

---

## Command reference

```bash
# the work
neobrain next                              # what the work needs from you
neobrain project new "<name>" --question "<the question>"
neobrain hypothesis add "<claim>" --falsifier "<what would refute it>"
neobrain experiment plan "<title>" --prediction "<what you expect>"
neobrain experiment result 3 "<what happened>" --outcome contradicted

# evidence
neobrain ask "does class II inclusion improve durable responses?"
neobrain answer "<question>" --bibtex
neobrain check "<a sentence from your draft>"     # --draft for a paragraph
neobrain discover search "<keywords>"             # --download --export refs.bib
neobrain search "HLA LOH detection" -k 8

# reading
neobrain paper MED:39012345 --methods
neobrain mark MED:39012345 --state read --rating 5 --note "the control design I want"
neobrain ingest-pdf --inbox --archive

# memory
neobrain remember "<something>" --kind correction
neobrain recall "montanide"
neobrain rule add "<when>" "<do this>"
neobrain history 7                                # --as-of 2026-03-01
neobrain review                                   # approve/reject memory edits

# bench
neobrain peptide windows KRAS_SEQ 12 D --wildtype G
neobrain peptide junctions SIINFEKL,ASMTNMELM --linker AAY
neobrain models --recommend structure
neobrain clinic --tumour pancreatic --variants "KRAS G12D,B2M" --hla "HLA-C*08:02"

# housekeeping
neobrain web · doctor · status · backup · graph · conflicts · quiz · teach 05
```

Everything the agent can do, you can do from a terminal. An agent capability
you cannot invoke yourself is one you cannot debug at 2am.

---

## Connecting an agent

```bash
claude mcp add neobrain -- neobrain mcp     # Claude Code
cat AGENT.md >> CLAUDE.md                    # the operating contract
```

Claude Desktop, `claude_desktop_config.json`:

```json
{"mcpServers": {"neobrain": {
  "command": "/absolute/path/to/neobrain/.venv/bin/neobrain",
  "args": ["mcp"],
  "env": {"NEOBRAIN_HOME": "/absolute/path/to/neobrain"}}}}
```

43 tools: `brief`, `whats_next`, `evidence`, `check_claim`, `compose_answer`,
`add_hypothesis`, `plan_experiment`, `record_result`, `remember`, `recall`,
`graph_path`, `molecular_screen`, `propose_memory_edit`… and no tool to approve
one.

---

## Layout

```
neobrain/
├── AGENT.md                   the agent's operating contract
├── docs/  DESIGN.md · DEPLOYMENT.md · SECURITY.md · WORKFLOWS.md
├── config/
│   ├── interests.yaml         what you care about; tune weekly
│   ├── models.yaml            ~20 biology models with licences and caveats
│   ├── curriculum.yaml        10 modules with checkpoint tasks
│   └── settings.yaml
├── memory/CORE.md             loaded in full every session
├── knowledge/                 13 curated domain notes
├── neobrain/
│   ├── discover.py            guided multi-source search + snowballing
│   ├── extract.py             extraction with provenance + methods audit
│   ├── science.py             projects, hypotheses, experiments, routing
│   ├── answer.py              cited answers + claim checking
│   ├── clinic.py              ESCAT tiers + trial screening
│   ├── journal.py graph.py evidence.py memory.py retrieve.py …
│   ├── web/                   dashboard: stdlib server + single-file app
│   └── sources/  openalex · pubmed · europepmc · clinicaltrials · preprints · local
└── brain.db                   SQLite: everything
```

## Tests

```bash
pytest        # 167 tests
```

They cover the parts that fail silently: FTS escaping (`HLA-A*02:01` must not
become a syntax error), rank fusion, SM-2 arithmetic, journal immutability,
belief versioning, dedup across sources, extraction provenance, and a smoke
test that every MCP tool actually *executes* — added after a tool named
`evidence` shadowed the `evidence` module and broke two others in a way that
listing the tools could not reveal.

---

## Honest limits

- Extraction recall is below an LLM's; unusually-phrased reporting is missed.
  Mitigated by making absence explicit, not by pretending otherwise.
- The methods audit reads prose — a paper can report `n` in a figure legend and
  be scored as not reporting it.
- Screening handles hundreds of records; Elicit does tens of thousands.
- Clinical actionability uses a small hand-maintained table, not OncoKB/CIViC.
- Trial matching cannot read eligibility criteria.
- The claim checker is lexical, not entailment.

Each of these is stated in the product where you meet it, not only here. A tool
that hides its limits gets trusted in exactly the situations where it should
not be.

## The rule that makes it worth using

The agent attaches a source to every factual claim, or says it does not know.

A confabulating research assistant is worse than none, because the error enters
your thesis without a trail back to where it came from. The belief store, the
approval gate, the citation-carrying context packs, the staleness warnings, and
`not reported` as a first-class value all exist to enforce that one property.
