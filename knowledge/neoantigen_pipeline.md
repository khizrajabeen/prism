# Neoantigen discovery → vaccine: the pipeline

The path from a tumor sample to a construct you can put in an arm, with the
attrition and the failure modes stated at each step.

> Maintained note. Extended from the original seed file. The agent proposes
> dated, sourced edits; it does not write here directly.

**Attrition, in orders of magnitude, for a typical solid tumor:**

```
  10²–10³   somatic non-synonymous mutations (WES)
   10²      expressed in RNA-seq at meaningful TPM
   10²      peptides predicted to bind patient HLA
   10¹      selected for synthesis / construct inclusion
   10⁰–10¹  with confirmed T cell reactivity
   ?        that actually contribute to tumor control
```

Every step of that funnel is a design decision, and the last line is the honest
state of the field.

---

## 1. Sequencing and variant calling

**Input.** Tumor–normal paired **WES** is the workhorse. **WGS** additionally
catches structural variants, non-coding drivers, and retroelements at higher
cost. **RNA-seq of the same tumor is not optional** — it is how you establish
the mutant allele is expressed, and expression filtering removes a large
fraction of nominal candidates.

Practical minimums: ~100–150× tumor, ~50× normal for WES. Lower coverage
systematically loses subclonal variants, which biases you toward clonal
candidates — sometimes an accident that helps you, but know that it happened.

**Somatic callers.** Mutect2 (GATK), Strelka2, VarScan2, and consensus of two
callers to cut false positives. Purity and ploidy estimation
(FACETS, ASCAT, PureCN, Sequenza) is a prerequisite for computing cancer cell
fraction later, and is routinely skipped by people who then talk confidently
about clonality.

**Beyond SNVs — where candidates are being left on the table:**

| Class | Why it matters | Tools |
|---|---|---|
| Frameshift indels | Long novel ORFs; many epitopes per event; disproportionately immunogenic; the basis of MSI-high responsiveness | standard callers + careful indel realignment |
| Gene fusions | Novel junction peptides, common in sarcomas and some leukaemias | Arriba, STAR-Fusion → NeoFuse |
| Splice variants / retained introns | Novel junction and intronic peptides; splicing factor mutations (SF3B1, U2AF1) create these systematically | IRFinder, rMATS, SpliceAI, NeoSplice |
| Endogenous retroelements / TEs | Derepressed in many tumors and by hypomethylating agents; shared across patients | TElocal, SQuIRE, REdiscoverTE |
| Non-canonical ORFs (uORFs, lncRNA-encoded) | Detected by Ribo-seq and immunopeptidomics; a growing fraction of observed HLA ligands | Ribo-seq + proteogenomics |
| Post-translational / spliced peptides | Proteasome-spliced peptides remain contested in prevalence | MS-based, contested |
| Fusion of viral ORFs (HPV, EBV, MCPyV) | Not neoantigens but shared, foreign, and clinically actionable | viral read mapping |

A pipeline that handles only missense SNVs is the field's default and its
biggest systematic blind spot.

## 2. HLA typing

Class I **and** class II, four-digit resolution minimum.

- From WES/WGS: **OptiType** (class I, very accurate), **Polysolver**, **HLA-HD**
  (covers class II).
- From RNA-seq: **arcasHLA**.
- Reference: **IPD-IMGT/HLA**.
- Also compute **HLA LOH** (LOHHLA and successors). A patient can be
  heterozygous in germline and have lost an allele in the tumor — targeting
  epitopes restricted to a lost allele is a guaranteed failure that looks like
  a prediction failure.

Class II typing is harder and less accurate than class I. Carry the error bars
explicitly into downstream class II predictions rather than pretending they are
equivalent.

## 3. Peptide generation and binding prediction

Generate mutant peptides tiling the variant: **8–11-mers** for class I,
**13–25-mers** for class II, with the mutated residue at every position.

**Class I predictors.** NetMHCpan (the long-standing pan-specific reference),
MHCflurry, and deep-learning entrants (BigMHC, TransPHLA, and successors).
**Class II.** NetMHCIIpan; substantially weaker, for the open-groove /
register-ambiguity reasons in `immunology_foundations.md`.

**Presentation ≠ binding.** Predictors trained on **mass-spec eluted ligand**
data outperform pure binding-affinity models for identifying what actually
reaches the surface — because they implicitly learn processing, transport, and
expression effects that affinity models cannot see. Prefer the eluted-ligand or
combined output when a tool offers both.

**Rank vs affinity.** Use the **percentile rank** across a reference peptide set
rather than raw nM affinity when comparing across alleles — alleles differ in
their affinity distributions, and a 500 nM binder means different things for
A*02:01 and B*07:02. Conventional cut-offs (strong ≤0.5%, weak ≤2%) are
conventions, not biology; state which you used.

**Always verify the current version.** This area moves fast, and quoting results
from a predictor version you did not use is a common and avoidable error.

## 4. Immunogenicity ranking — genuinely unsettled

Features with evidence of adding signal beyond binding:

- **Expression** of the source transcript (TPM), and allele-specific expression.
- **Clonality** — cancer cell fraction, not raw VAF. Clonal targets can clear
  the tumor; subclonal ones select for escape.
- **Differential agretopicity (DAI)** — mutant vs WT predicted binding. Large
  DAI means the epitope is genuinely new to the repertoire.
- **Foreignness / dissimilarity from self proteome**; and **similarity to known
  pathogen epitopes** (molecular mimicry heuristic — plausible, contested).
- **Proteasomal cleavage and TAP transport** predictions.
- **TCR-facing position** of the mutation — a change buried in the groove alters
  binding but not what the TCR reads.
- **Peptide-MHC stability** (half-life), which tracks immunogenicity better than
  affinity in several datasets.
- **Hydrophobicity** at TCR-contact residues.

Ranking tools: PRIME, DeepImmuno, the scoring inside pVACtools,
Antigen.garnish. Benchmark reality check: the **TESLA** consortium comparison —
read it before believing any single tool's self-reported performance.

**The honest framing:** ranking beyond binding adds real but modest signal.
Anyone claiming to have solved immunogenicity prediction should be read with
the TESLA results in hand.

## 5. Integrated pipelines

- **pVACtools** (pVACseq / pVACbind / pVACfuse / pVACvector) — Griffith lab; the
  most widely used open framework. pVACvector orders epitopes in a
  string-of-beads construct to avoid junctional neoepitopes.
- **NeoPredPipe**, **MuPeXI**, **Antigen.garnish** (R), **OpenVax / vaxrank**,
  **NeoFuse** (fusions), **Neoepiscope** (phased variants).
- **Phasing matters**: two variants on the same allele within one peptide
  produce a different peptide than either alone. Neoepiscope handles this; most
  pipelines silently do not.

## 6. Validation — prediction is a hypothesis generator

- **Immunopeptidomics** (HLA-IP + LC-MS/MS) — direct observation of presented
  peptides. The closest thing to ground truth for presentation, with the caveat
  that MS sensitivity means absence of evidence is weak evidence of absence,
  and single-copy-per-cell epitopes are routinely missed.
- **T cell assays** — see `protocols_in_vitro.md` and `preclinical_models.md`.

## 7. Delivery

See `delivery_platforms.md`.

## 8. Clinical landscape

See `clinical_landscape.md`. That file is explicitly marked as ageing fast and
should be checked against the trials table before being quoted.

---

## Open questions worth tracking

- Class II vs class I contribution to clinical benefit.
- Whether prediction accuracy is the bottleneck, or repertoire/tolerance is.
- Clonal vs subclonal targeting under immune editing.
- Whether shared-neoantigen products can approach personalized efficacy.
- How much of the non-canonical (TE, uORF, spliced) HLA ligandome is targetable.
- Whether MS-negative but predicted epitopes are genuinely absent or below LOD.
