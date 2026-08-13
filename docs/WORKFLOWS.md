# Workflows

Concrete sequences for the things you will actually do. Each one assumes an
agent connected via MCP, but every step has a CLI equivalent.

---

## The daily loop

```
morning     neobrain sweep runs on a timer while you sleep
            ↓
session     agent calls brief → summarizes what changed in <150 words
            ↓
work        you ask questions → agent retrieves → answers with citations
            ↓
end         agent proposes memory edits + review cards
            ↓
you         neobrain review → approve/reject diffs → git commit
```

Ten minutes of yours per day, most of it the review step.

---

## "What's new in my field this week?"

```bash
neobrain sweep --days 7 && neobrain digest
```

Ask the agent: *"Read the latest digest. Which of these change anything we
believe? Flag contradictions with the beliefs table explicitly."*

What good looks like: it names two or three papers, says which existing belief
each bears on, and proposes edits rather than asserting them. What bad looks
like: a summary of every paper in the digest. Push back — the digest is already
the summary.

---

## "I need to understand X properly"

```bash
neobrain teach 03          # a curriculum module
neobrain teach "HLA loss of heterozygosity"   # or any topic
```

The packet contains the module, your relevant knowledge notes, and current
evidence from the corpus. Ask the agent to teach from it. Insist on the
checkpoint task — a verbal answer is not evidence you understood it.

Afterwards: `neobrain quiz` a day later, then three days, then a week. That is
what the scheduler is for.

---

## "Help me design this experiment"

1. `neobrain search "MC38 therapeutic vaccination design" -k 10`
2. Ask for the design, then ask specifically: *"What is the control group that
   would falsify my hypothesis, and did you include it?"*
3. Have it compute the power calculation and **show the working**.
4. Check it against `knowledge/experimental_design.md` § "Interpreting other
   people's results" — the questions you would ask of someone else's paper are
   the questions to ask of your own design.

The adjuvant-alone arm is the one most often missing. If the agent did not
include it, that tells you something about how carefully it read.

---

## "Is this paper any good?"

```bash
neobrain fulltext MED:39012345        # pull the OA full text
neobrain paper MED:39012345 --methods # read the methods, not the abstract
```

Then: *"Critique the methods. Specifically: n and power, randomization point,
control peptides, blinding, and whether the conclusion follows from the primary
endpoint."*

Record your verdict so the corpus learns your taste:

```bash
neobrain mark MED:39012345 --state read --rating 2 \
    --note "no adjuvant-alone arm; prophylactic only; overclaims therapeutic"
```

---

## "Build me the pipeline"

1. `neobrain search "pVACseq expression filter threshold" -k 8`
2. Ask for the code with the test first, then the implementation.
3. Every threshold gets a comment saying why that number.
4. Run it. A pipeline that has not run is a draft.

See `knowledge/coding_practices.md` § "Working with an AI assistant on research
code" for the specific failure modes to check for.

---

## "I read something important that isn't in the corpus"

Paywalled paper you opened through your library proxy:

```bash
# save the PDF to inbox/, then
neobrain ingest-pdf --inbox --archive
neobrain search "the thing you remember from it"
```

Now it is retrievable and citable alongside everything else.

---

## "The digest is boring / noisy"

Boring → your queries are too narrow, or the threshold is too high.
Noisy → the opposite.

```bash
neobrain score "a title you wish had surfaced" --abstract "..."
neobrain score "a title that wasted your time" --abstract "..."
```

The scoring is additive and transparent; both outputs tell you exactly which
terms to add, weight, or penalize in `config/interests.yaml`. Retune weekly at
first. A digest you have learned to skip is worse than no digest.

---

## "Where did that claim come from?"

```bash
neobrain belief list --topic "class II"
```

Every belief carries its sources and a review date. When one comes due, the
brief tells you, and the right move is to ask the agent: *"Re-check belief #7
against the current corpus. Has anything changed?"* — then let it propose an
update, a confidence change, or a supersession.

This is the mechanism that stops a 2026 claim from silently becoming a 2029
assumption.

---

## Monthly maintenance

```bash
neobrain doctor                       # anything quietly broken?
neobrain belief list --due            # what needs re-checking
neobrain card stats                   # which topics you keep failing
git log --stat knowledge/ memory/     # how your understanding changed
```

`neobrain card stats` is the most useful of these: the topics with the lowest
ease are, empirically, the ones you do not actually understand yet. That is
where the next `neobrain teach` should go.
