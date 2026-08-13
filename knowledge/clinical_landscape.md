# Clinical landscape

> **This file ages faster than any other in the knowledge base.** Treat every
> statement here as a prior to be checked, not a fact to be quoted. Before
> using anything from this file in writing, run:
>
> ```bash
> neobrain search "individualized neoantigen therapy phase" --papers
> ```
>
> and check the `trials` table for current status. The agent should say
> explicitly when it is relying on this file rather than on a retrieved source.

---

## The shape of the field

**Individualized neoantigen therapies.** Tumor sequenced, candidates predicted,
a bespoke construct manufactured per patient, usually combined with checkpoint
blockade. Melanoma has been the lead indication (high TMB, accessible lesions,
established checkpoint benefit), with pancreatic, lung, renal, bladder, and
glioblastoma programs following. Randomized and early-phase data have been
encouraging enough to move several programs to later-stage trials; the field's
central open question is whether that translates into a durable, reproducible
survival benefit at scale.

**Shared / public neoantigens.** Off-the-shelf products targeting recurrent
driver mutations — KRAS G12D/G12V/G12C, TP53 hotspots (R175H, R248W), and
similar. Trades the personalized fit for manufacturability, cost, and speed.
Restricted by HLA: a KRAS G12D epitope restricted to HLA-C*08:02 is available
only to patients carrying that allele, which is a small fraction. This HLA
restriction is the structural ceiling on shared-neoantigen reach, and is why
these programs report both a mutation and an allele.

**Adjacent modalities that compete for the same patients**: TCR-T cells against
shared neoantigens, TIL therapy, and personalized ctDNA-guided approaches.

## Where the field is moving, and why

1. **Toward the adjuvant / minimal residual disease setting.** Low tumor
   burden, functional immune system, time to prime. **ctDNA** is being used to
   identify MRD patients — the clearest current application of liquid biopsy in
   this space.
2. **Toward combination with checkpoint blockade** as the default rather than
   the exception, for the mechanistic reason in `resistance_and_escape.md`.
3. **Toward shorter manufacturing turnaround**, which is a manufacturing
   science problem more than an immunology one.
4. **Toward class II inclusion** in construct design.

## The structural constraints (stable, unlike the trial list)

- **Turnaround time** from biopsy to first dose.
- **Cost per patient** for bespoke manufacture, and what that means for access.
- **The sickest patients benefit least** — the inverse relationship between
  need and expected benefit.
- **Regulatory novelty**: a therapy where every dose is a different product
  requires a different approval framework than a fixed molecule, and that
  framework is still settling.
- **Endpoint choice**: immunogenicity is measurable in weeks, clinical benefit
  in years. Programs that report only the former are not yet evidence of the
  latter.

## How to read a trial readout in this space

Work through `experimental_design.md` § "Interpreting other people's results",
then specifically:

- **Randomized or single-arm?** Immunotherapy single-arm response rates are
  heavily selection-confounded.
- **What is the control arm receiving?** Vaccine + checkpoint vs checkpoint
  alone is the informative comparison; vaccine + checkpoint vs nothing is not.
- **Was the primary endpoint pre-specified**, and is the headline result the
  primary endpoint or a subgroup?
- **How many patients actually received the full course?** Manufacturing
  failures and progression before dosing are real attrition, and per-protocol
  analysis hides them. Look for intention-to-treat.
- **Immunogenicity rate vs response rate.** A large gap is the norm and is
  informative about where the failure sits.

## What to track in the sweep

The `trials` table records status transitions, so the leading indicators are:

- Phase 1 → phase 2 progression for a given platform.
- Status changes to "Active, not recruiting" (enrolment closed — data coming).
- **Terminated / withdrawn** trials, which are the signal nobody publishes.
- New sponsors entering, and new indications for an existing platform.

```bash
neobrain sweep --days 30
sqlite3 brain.db "SELECT nct_id, field, old_value, new_value, observed_on
                  FROM trial_history ORDER BY id DESC LIMIT 20;"
```
