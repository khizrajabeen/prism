# NeoBrain — product and interface design

Where this sits against the tools researchers already use, what it does that
none of them do, and how the interface should be built to make that legible.

Written to be argued with. Every design claim below has a reason attached; if
the reason is wrong, change the design.

---

## 1. The competitive picture

Ten tools a biology researcher might reasonably use, what each is genuinely
best at, and where it stops.

| Tool | Best at | Where it stops |
|---|---|---|
| **Elicit** | Systematic-review screening at scale (tens of thousands of papers); LLM data extraction into custom columns, ~94% accurate | Extraction is model-generated: the wrong 6% is invisible, and a blank cell is ambiguous between "not reported" and "missed". No persistent memory of *your* project |
| **Scite** | Citation *context* — 1.6B smart citations classified supporting / contrasting / mentioning | By its own documentation, does not assess the quality of the citing study: a "supporting" citation can come from an underpowered n=5 experiment. Preprints and grey literature are invisible |
| **Consensus** | Fast yes/no answers with a consensus meter over findings | Abstract-level; no methods detail, no project state |
| **SciSpace** | Breadth (280M+ papers), paper-by-paper reading, AI writer | Shallow per paper; no screening pipeline |
| **ResearchRabbit** | Citation-network discovery from a seed set; free | Discovery only — no extraction, screening, or synthesis |
| **Connected Papers** | One clear similarity graph per seed paper | Single-shot; no workflow around it |
| **Litmaps** | Monitoring a citation map over time | Same — a map, not a workspace |
| **Semantic Scholar** | Free API, huge corpus, citation intent (background/method/result) | Intent categories omit the support/contradict axis that actually matters |
| **Undermind** | Recursive deep search; high recall on hard questions | Search only; nothing persists |
| **PubMed / Europe PMC** | Authoritative, free, MeSH indexing | No synthesis layer at all |

**The pattern.** Every one of them is a *literature* tool. They are excellent
at finding, ranking, and summarising papers. Not one of them knows what you are
trying to find out, what you predicted last month, or what you already decided
against — because none of them holds your work.

That is the gap NeoBrain occupies, and it is a structural one rather than a
feature gap. You cannot bolt "knows my hypotheses" onto a search engine.

---

## 2. What we do that nobody else does

Four things. The first three are individually novel; the fourth is only
possible because the other three exist in the same database.

### 2.1 Extraction with sentence-level provenance, and "not reported" as data

Elicit extracts with an LLM. Fast, high recall, and it can produce a value that
is not in the paper. We extract with patterns: lower recall, zero fabrication,
and **every cell links to the exact sentence it came from**.

The more important difference is the empty cell. In an LLM table, a blank means
"the model did not find it" and you cannot tell whether the paper reported it.
Here, `not reported` is an explicit value with a defined meaning — a literal
reading found no statement of it. Read a column downwards and you learn
something about the field:

> 11 of 14 MC38 vaccine papers do not report a power calculation.

That sentence is a finding. No other tool can produce it, because no other tool
distinguishes absence from failure-to-extract.

### 2.2 The methods audit

Per paper, against the reporting essentials that ARRIVE and CONSORT have asked
for since 2010: group sizes, randomization, blinding, power, controls, named
statistical test, ethics approval. Each item present/absent **with the
sentence**.

Deliberately not a single quality score. A score invites ranking papers by it,
and this measures *reporting*, which correlates with rigour without being it.
The useful output is "go and check whether they blinded", not "4.2/7".

### 2.3 Quality-weighted evidence — the Scite gap

Scite counts citations and classifies their stance. Its own documentation
concedes the limitation: *the evidence behind a supporting citation is not
assessed*, so three supporting citations from underpowered studies outrank one
from a randomized trial.

We weight support by `study design tier × reporting completeness`, and show
both components rather than a single opaque number, because a strong design
that is unreported cannot be appraised and good reporting cannot rescue a weak
design.

We will never match Scite's 1.6B citation corpus. We do not need to — we are
answering a different question. Scite tells you *how the world cites a paper*.
We tell you *how much a paper's support is actually worth*.

### 2.4 Evidence routed to your own hypotheses

New literature arrives; the system says which of *your* open hypotheses it
bears on, and links it.

```
hypothesis #1: Class II epitopes improve durability of MC38 vaccine responses
  ← Class II epitopes improve durable responses to MC38 neoantigen vaccination
    auto-suggested: shares MC38, MHC class II
```

This is the one capability that is structurally unavailable to every tool in
the table above. It requires the work ledger — projects, hypotheses with
falsifiers, experiments with predictions recorded before results — to live in
the same database as the corpus. Elicit can screen ten thousand papers against
criteria you retype each session. It cannot tell you that this paper bears on
the hypothesis you have an experiment running for, because it has never heard
of your experiment.

---

## 3. The model, finalised

```
        ┌───────────┐     ┌────────────┐     ┌──────────────┐
        │ QUESTION  │────▶│  EVIDENCE  │────▶│  HYPOTHESIS  │
        │  Ask      │     │  Research  │     │  falsifier   │
        │  Check    │     │  Library   │     │  required    │
        └───────────┘     └────────────┘     └──────┬───────┘
              ▲                  │                  │
              │            routing│                 │
      ┌───────┴──────┐    ┌───────▼──┐    ┌─────────▼────────┐
      │    BELIEF    │◀───│  RESULT  │◀───│    EXPERIMENT    │
      │  versioned   │    │ vs the   │    │ prediction saved │
      │  weighted    │    │prediction│    │  before it runs  │
      └──────────────┘    └──────────┘    └──────────────────┘
```

Three invariants that everything else follows from:

1. **No claim without a source you can open.** Enforced at every layer:
   beliefs require sources, extractions carry sentences, answers carry BibTeX.
2. **The agent proposes; you approve.** No tool exists that lets it write to
   authoritative memory. Raw capture is immediate and immutable; promotion is
   reviewed.
3. **Absence is data.** "Not reported", "no evidence in your corpus", "corpus
   is silent" are first-class answers, distinct from failure.

---

## 4. Interface principles

### 4.1 Overview first, zoom and filter, details on demand

Shneiderman's mantra, and the reason the eleven-tab version failed. Each screen
answers one question at one altitude, and drilling down is always available and
never mandatory.

- **Today** answers *what should I do now?*
- **Ask** answers *what does the evidence say?*
- **Research** answers *what exists on this topic?*
- **Projects** answers *where is my work?*
- **Library** answers *what have I read?*
- **Bench** answers *how do I do this?*
- **Memory** answers *what do I know, and what needs my approval?*

If a screen answers two of those, split it. If two screens answer the same one,
merge them.

### 4.2 Provenance is a first-class interaction, not a tooltip

The closest published work to what we are building — PaperTrail's claim-evidence
interface — uses span annotation to highlight the exact characters supporting a
claim. That is the right target.

Every derived value in this system must be **one click from its source**:

| Element | Click reveals |
|---|---|
| Extraction cell | The sentence, its section, the confidence |
| Answer passage | Full passage, paper, evidence tier |
| Belief | Every version, with what changed it |
| Audit item | The sentence that satisfied it, or an explicit "no statement found" |
| Routed lead | Which entities matched, and why |

A number a researcher cannot trace is a number they cannot use in a methods
section. Untraceable output is not a shortcut; it is work they will have to
redo.

### 4.3 Show the shape of the evidence before the evidence

Consensus's meter is right about one thing: a reader wants the aggregate before
the list. Every result set leads with its shape —

```
Evidence strength: established — multiple higher-tier sources agree
7 passages · 4 distinct sources · tiers 5:1 4:2 2:1
Corpus coverage: 31 papers mention "class II" (2019 – 2026)
```

— because "no evidence" and "did not look" must never be indistinguishable, and
because a reader who scrolls a list without knowing its shape will anchor on
whatever happens to be first.

### 4.4 Say no visibly

The interface refuses things, and the refusal is the feature:

- A hypothesis without a falsifier is rejected, with the reason.
- An experiment without a prediction is rejected.
- The clinical screen states it is not an eligibility determination, every time.
- Claim checking uses hedged verdicts (`likely-supported`, `needs-review`) and
  never says "verified".

Every refusal explains itself and offers the next action. A refusal without a
reason is an error message; a refusal with one is guidance.

### 4.5 Density is respect

This is a tool for people who read papers for a living. Do not pad. Tables
beat cards for comparison; monospace for sequences, alleles and identifiers;
full information over progressive disclosure when the user is comparing.

### 4.6 Local-first, visibly

The interface should make it obvious that nothing is leaving: no CDN, no
external fonts, a CSP that forbids outside requests, localhost binding, and a
plain statement wherever that changes (the voice privacy note).

---

## 5. Visual system

Already implemented in `neobrain/web/app.html`; recorded here so it stays
consistent.

**Colour.** Semantic, never decorative.

| Token | Use |
|---|---|
| `--accent` teal | Interactive, current state, "retrieved via" |
| `--ok` green | Reported, included, high tier, open access |
| `--warn` amber | Needs review, provisional, low tier, privacy caveat |
| `--danger` red | Contradicted, excluded, exclusion signal (B2M) |
| `--muted` / `--faint` | Provenance, metadata, rationale |

Both themes defined as tokens on `:root`, dark redefined under
`prefers-color-scheme`. No colour carries meaning alone — every state also has
a label, because a red pill and a grey pill are the same pill to a colourblind
reader.

**Type.** System sans for prose; monospace for anything a researcher might
retype — peptide sequences, HLA alleles, paper ids, DOIs, diffs, queries.

**Motion.** Almost none. A spinner while waiting, nothing else. Animation in a
research tool is latency you chose to add.

---

## 6. What to build next, in order

1. **Extraction matrix in the UI** — the table is built (`extract.matrix`);
   it needs its screen, with click-through to provenance and the gap summary
   above the fold.
2. **Methods audit on the paper view** — checklist with sentences, and the
   corpus-level "weakest reporting" roll-up.
3. **Screening at scale** — currently hundreds of records; Elicit does tens of
   thousands. The bottleneck is UI, not backend.
4. **Citation-context extraction from our own full texts** — when we hold both
   citing and cited paper, classify the citing sentence. A small, high-precision
   slice of what Scite does, weighted by our audit, over the corpus you actually
   read.
5. **A trained stance model** to replace the lexical claim checker, slotting in
   behind `answer._signals` without changing anything above it.

## 7. What we should not build

- **Our own citation index.** Scite has 1.6B; we would have a worse one.
- **A chat interface as the primary surface.** The work is structured; a chat
  box discards that structure and then tries to reconstruct it from prose.
- **A quality score.** Rankable, and therefore misused.
- **Cloud sync.** The entire premise is that the database is yours and local.
  The moment there is a server, the DUA questions start.

---

## 8. Honest limits

- Extraction recall is lower than an LLM's. Unusually-phrased reporting is
  missed. Mitigated by making absence explicit, not by pretending otherwise.
- The methods audit reads prose. A paper can report n in a figure legend and be
  scored as not reporting it.
- Clinical actionability uses a small hand-maintained table, not OncoKB or
  CIViC.
- Trial matching cannot read eligibility criteria.
- The claim checker is lexical, not entailment.

Each of these is stated in the product where the user meets it, not only here.
A tool that hides its limits gets trusted in exactly the situations where it
should not be.
