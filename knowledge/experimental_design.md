# Experimental design and statistics for this field

The methodological failures that recur in neoantigen work, and how to not
commit them.

---

## Before the experiment

**State the hypothesis as a prediction that could fail.** "The vaccine will
improve survival" is not testable until you say by how much, in which model,
measured how.

**Power the study.** The question a power calculation answers: given the
smallest effect I would care about, how many animals/samples do I need for an
80% chance of detecting it? Choosing that effect size is a scientific
judgement, not a statistical one — and making it explicit is most of the value.

```r
# Two-group survival, log-rank, R
library(powerSurvEpi)
ssizeCT.default(power = 0.8, k = 1, pE = 0.6, pC = 0.2, RR = 0.4, alpha = 0.05)

# Two-group continuous endpoint (e.g. tumor volume at day 21)
power.t.test(delta = 200, sd = 150, power = 0.8, sig.level = 0.05)
```

**Post-hoc power on an observed effect is uninformative.** It is a
transformation of the p-value, not new information.

**Pre-specify the analysis.** Primary endpoint, timepoint, statistical test,
exclusion criteria, and the multiplicity correction — written down before data
collection. Pre-registration (even a dated file in your repo) converts
exploratory work into confirmatory work at zero cost.

---

## Randomization, blinding, and the mouse-specific traps

- Randomize **at a defined tumor volume**, not at implantation.
- Blind caliper measurement and endpoint scoring. If you cannot blind treatment
  administration, blind the measurement at minimum, and say so.
- **Cage is a confounder** — microbiome, litter, and dominance hierarchy all
  affect immune endpoints. Randomize across cages; consider cage as a random
  effect for sensitive endpoints.
- Pre-define exclusions (failed engraftment, ulceration) and apply them blind.
  Post-hoc exclusion of outliers after seeing the groups is the most common
  quiet form of p-hacking in this literature.
- Report sex and justify single-sex designs. Immune responses are sexually
  dimorphic.

---

## Choosing the right test

| Situation | Test | Common error |
|---|---|---|
| Tumor growth over time, 2 groups | Mixed-effects model or repeated-measures ANOVA on log volume | t-test at each timepoint, then reporting the smallest p |
| Tumor volume at one predefined day | t-test or Mann-Whitney on log volume | Not log-transforming; volumes are multiplicative and right-skewed |
| Survival | Log-rank (Mantel-Cox); Cox PH for covariates | Reporting median survival without a test; ignoring censoring |
| >2 groups | ANOVA/Kruskal-Wallis + planned comparisons | All-pairs testing without correction |
| ELISpot counts | Non-parametric or a count model (negative binomial) | Assuming normality on small counts |
| Many peptides screened | FDR (Benjamini-Hochberg) across peptides | No correction at all |
| Paired pre/post vaccination | Paired test on the same donor | Treating paired samples as independent |

**Log-transform tumor volumes.** Growth is exponential; the variance scales
with the mean. Every parametric assumption you make on raw volumes is wrong.

---

## The multiplicity problem, which is severe here

You test hundreds of peptides × multiple assays × multiple timepoints ×
multiple patients. Uncorrected, positives are guaranteed.

Defences, in increasing order of strength:
1. FDR correction across the peptides tested.
2. Pre-specified candidate list, fixed before any assay runs.
3. **Confirmation in an independent assay** (ELISpot hit confirmed by tetramer
   or ICS) — the only one that really settles it.
4. Confirmation in an independent sample or patient.

---

## Reporting standards worth following

- **ARRIVE 2.0** — animal studies. Not bureaucracy: the essential-10 items are
  exactly the ones whose absence makes a mouse study uninterpretable.
- **MIATA** — T cell assay reporting. Exists because this literature was not
  reproducible without it.
- **REMARK / TRIPOD+AI** — biomarker and prediction-model reporting. If you
  build a classifier, TRIPOD+AI tells you what has to be in the paper.
- **MIAPE / PRIDE deposition** for immunopeptidomics.
- Deposit sequencing data (EGA/dbGaP for controlled access, SRA/ENA for open).

---

## Interpreting other people's results

Questions to ask of any neoantigen vaccine claim:

1. **Prophylactic or therapeutic?** Prophylactic protection is routine.
2. **Was there an adjuvant-alone arm?** Without it, innate activation and
   antigen-specific effect are confounded.
3. **What was the control peptide?** No wild-type control means no
   mutation-specificity claim.
4. **n, and was it powered?** Six mice per group detects only enormous effects.
5. **Was randomization at tumor volume, and was measurement blinded?**
6. **Immunogenicity or clinical benefit?** They are different endpoints and the
   gap between them is where the field lives.
7. **Single-arm or randomized?** Single-arm response rates in immunotherapy are
   heavily confounded by patient selection.
8. **Which predictor version, which thresholds?** Unstated thresholds mean the
   candidate list is unreproducible.
9. **Was the tumor re-biopsied at progression?** Almost never, and it is where
   the mechanism is.

## A note on effect sizes and language

Report effect sizes with confidence intervals, not just p-values. "Significant"
without a magnitude is uninformative, and in mouse tumor work a statistically
significant 15% reduction in volume at one timepoint is a very different claim
from durable rejection with rechallenge immunity. Write the number.
