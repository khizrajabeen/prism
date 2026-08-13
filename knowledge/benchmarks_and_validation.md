# Benchmarks, ground truth, and how to be honest about accuracy

Every tool in this field reports good performance. Most of that performance
does not survive contact with a prospective test.

---

## What counts as ground truth, ranked

1. **A confirmed T cell response in a human**, against a peptide identified as
   presented on that patient's tumor. Rare, expensive, and the only thing that
   settles an immunogenicity claim.
2. **Immunopeptidomics detection** — the peptide was physically observed on the
   HLA. Settles *presentation*, not immunogenicity. Absence is weak evidence
   given MS sensitivity limits.
3. **In vitro T cell reactivity** against a peptide-pulsed target. Shows a TCR
   exists; does not show the epitope is naturally processed and presented.
4. **Binding assay** (MHC stabilization, competitive binding). Shows the peptide
   fits the groove. The weakest form of evidence routinely reported as if it
   were the strongest.
5. **Prediction score.** A hypothesis.

The frequent error is presenting level-4 or level-5 evidence in the language of
level-1.

---

## The benchmark landscape

- **TESLA (Tumor nEoantigen SeLection Alliance)** — the field's reference
  reality check: many teams, the same patient data, prospective validation of
  predictions against measured immunogenicity. Read it before believing any
  single tool's self-reported numbers. Its central finding — that agreement
  between pipelines is poor and that a handful of features (binding, expression,
  agretopicity, hydrophobicity) carry most of the discriminative signal — has
  held up better than most individual tool papers.
- **IEDB benchmark** — rolling, automated evaluation of binding predictors on
  newly deposited data. Because it uses *new* data, it is relatively resistant
  to the leakage problem.
- **HLA Ligand Atlas / immunopeptidomics repositories** — presentation ground
  truth across tissues; also the source of the negative sets everyone needs.
- **CAMDA / DREAM-style challenges** — occasional, useful when prospective.

Always check the date. A benchmark result from a predictor version you are not
running is not a statement about your pipeline.

---

## How performance numbers get inflated

| Mechanism | What it looks like | The fix |
|---|---|---|
| **Sequence-similarity leakage** | Near-identical peptides in train and test | Split by source protein or cluster by identity |
| **Allele leakage** | Same allele dominates both sets | Report per-allele; hold out alleles |
| **Easy negatives** | Random peptides as the negative class | Length-, protein-, and expression-matched decoys |
| **Metric choice** | AUROC ~0.95 on a 1:1000 imbalance | Precision-recall; true positives in top-N |
| **Threshold tuning on test** | "Optimal" cut-off reported on the evaluation set | Nested cross-validation |
| **Retrospective selection** | Only patients with confirmed responses analysed | Prospective, pre-specified cohorts |
| **Benchmark saturation** | Everyone tuned on the same public set for years | New, prospective, held-back data |

---

## Evaluating a claim in practice

When a paper claims improved neoantigen prediction, check in this order:

1. **What is the label?** Binding, elution/presentation, or immunogenicity?
   Improvements on the first two are common; on the third they are rare and
   should be treated with more scepticism.
2. **How was the test set split?** If it says "random 80/20", assume leakage.
3. **What is the positive:negative ratio?** and does the metric respect it?
4. **What is the top-N precision?** That is how the tool is used — you take the
   top 20 peptides to synthesis, not the whole ranked list.
5. **Was it compared to the right baseline?** NetMHCpan with expression
   filtering is a strong baseline that many new methods do not actually beat.
6. **Can you run it?** Code, weights, and a working environment, or it is not a
   tool yet.

---

## Designing your own validation

If you are building or tuning a ranker:

- Hold out **patients**, not peptides.
- Include a **naive baseline** (rank by predicted affinity alone; rank by
  expression alone) and report how much your method adds over it. Many methods
  add less than their abstract implies.
- Report the **number of validated epitopes recovered in the top 20**, because
  that is the operational question.
- Report **failures**: which true epitopes your method ranked low, and whether
  there is a pattern (class II? low expression? particular alleles?). This is
  the most useful paragraph in any methods paper and the most often missing.
