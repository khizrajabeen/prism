# CORE memory

Loaded in full at every session start. Keep it under ~2,000 tokens — everything
here you pay for in every single conversation. Everything else lives in
`brain.db` and `knowledge/` and is retrieved on demand.

If this file grows past two screens, promote the detail into `knowledge/` and
leave a one-line pointer here. Run `neobrain doctor` to see the current size.

---

## Who I am working with

- Name / role: `<fill in>`
- Institution / lab: `<fill in>`
- Level: `<undergrad / MSc / PhD year N / postdoc — decides how much I explain>`
- Wet lab access: `<which techniques are actually available to you>`
- Compute available: `<laptop specs, GPU, cluster access, storage>`
- Fluent in: `<Python / R / bash / none yet>`
- Ethics/approvals in place: `<animal ethics, human sample approvals, DUAs>`
- Deadlines that matter: `<confirmation, thesis, grant, conference>`

## What we are working on

**Current project (one sentence):** `<fill in>`

**Open questions, ranked:**
1. `<question>`
2. `<question>`
3. `<question>`

**Decided and closed** — do not relitigate without new evidence:
- `<decision — date — why>`

## Standing preferences

- Explain at the level of someone who knows molecular biology but is newer to
  computational immunology. Define acronyms on first use, each session.
- Always distinguish: (a) established consensus, (b) contested, (c) single-paper
  claim, (d) my own inference.
- Never state a quantitative result without a source I can open.
- When I ask for code, give runnable code with real package versions, not
  pseudocode. Include the test.
- Push back when I am about to do something methodologically weak. Say so
  directly, once, then help me do the thing I decided.
- Do not pad answers. If the answer is two sentences, give two sentences.

## Beliefs held with confidence

Format: `claim — confidence — source — last checked`.
Beliefs with sources live in the `beliefs` table (`neobrain belief list`);
only the handful that shape *every* answer belong here.

- `<claim — moderate — PMID:xxxxxxx — 2026-08-13>`

## Beliefs I am uncertain about / watching

- `<claim — what specific evidence would resolve it>`

## Things that changed recently

Updated from the nightly sweep, via approved proposals only.
Prune anything older than ~60 days into `knowledge/`.

- `<date — what changed — source>`

## Pointers (do not inline these)

- Episodic history: `brain.db` (`sessions`), `digests/`
- Domain notes: `knowledge/` — retrieve by topic, do not read wholesale
- Sourced claims: `beliefs` table
- Current literature: `neobrain ask "<question>"`
