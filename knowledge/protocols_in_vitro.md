# Working protocols and their decision points

Enough detail to plan an experiment and to know what a paper is not telling you.
These are **planning templates**, not validated SOPs — you calibrate to your
own cells, reagents, and ethics approval before anything touches a patient
sample.

---

## IFN-γ ELISpot for neoantigen screening

**Question it answers:** does this person have T cells that respond to this
peptide, at a frequency above ~1 in 10⁵?

**Layout for 20 candidate peptides, one donor**

| Wells | Content | Why |
|---|---|---|
| 20 × 3 | Mutant peptides, triplicate | The test |
| 20 × 3 | Matched **wild-type** peptides | Without this you cannot claim mutation-specificity |
| 3 | DMSO vehicle only | Background; sets the negative threshold |
| 3 | Irrelevant peptide (HLA-matched, unrelated) | Controls for non-specific peptide effects |
| 3 | CEF/CEFT pool | Positive control for T cell functionality |
| 3 | PHA or anti-CD3 | Positive control for cell viability |

That is 132 wells — more than one 96-well plate. This is why people use pools,
and why pooling is a real cost in interpretability. Plan the plate before you
thaw anything.

**Key parameters and their consequences**

- Cells: 2–4 × 10⁵ PBMC per well. Fewer, and rare responses fall below detection.
- Peptide: 1–10 µg/mL final. Too high gives non-specific activation.
- **DMSO < 0.5% final** across every well, including controls. Match it exactly.
- Incubation: 18–24 h for direct ex vivo; 10–14 days for pre-expansion protocols.
- Rest thawed PBMC 2–16 h before plating; record viability (>80% or discard).

**Positivity criteria — decide before you look.** A common rule: mean spots
> 2× background AND > background + 3SD AND ≥ 5–10 spot-forming units per 10⁶
cells. State yours in the methods. Deciding after seeing the plate is how the
field acquired its reproducibility problem.

**Pre-expansion caveat.** Neoantigen-specific precursors are usually too rare to
detect directly ex vivo in an unvaccinated donor. In vitro stimulation
(peptide + IL-2/IL-7/IL-15 over 10–14 days) makes them detectable — and also
amplifies artifacts. Report which you did; they are not the same assay.

---

## Intracellular cytokine staining (ICS)

**Question:** what fraction of cells respond, and what are they?

- Stimulate 6 h with peptide (1–2 µg/mL) + co-stimulation (anti-CD28/CD49d).
- Add protein transport inhibitor (brefeldin A ± monensin) after the first hour.
  Adding it at time zero suppresses the surface markers you may also want.
- Include a degranulation marker (CD107a) **in the culture**, not at staining —
  it cycles.
- Panel core: viability dye, CD3, CD4, CD8, IFN-γ, TNF-α, IL-2; add CD154 for
  CD4, and memory markers (CCR7, CD45RA) if you care about differentiation.
- **Polyfunctionality** (cells making ≥2 cytokines) correlates with protection
  better than any single cytokine — analyse it with SPICE or equivalent rather
  than reporting three separate single-positive gates.

---

## AIM (activation-induced marker) assay

For CD4 responses and cytokine-independent detection.

- 18–24 h peptide stimulation, no transport inhibitor.
- CD4 readout: **OX40 (CD134) + CD137 (4-1BB)**, or OX40 + CD25.
- CD8 readout: **CD69 + CD137**.
- Background is the hard part: run enough unstimulated wells to define it, and
  report AIM⁺ frequency after background subtraction with the subtraction rule
  stated.

---

## pMHC multimer staining

- Use with a **dump channel** (CD14/CD19/viability) — dead cells and monocytes
  bind multimers non-specifically and generate convincing false positives.
- Stain multimer **before** surface antibodies, at room temperature; some
  protocols add a protein kinase inhibitor (dasatinib) to prevent TCR
  internalization and improve staining of low-affinity TCRs — relevant for
  neoantigens specifically, whose TCRs are often low-affinity.
- Two different fluorochromes for the same pMHC (dual-colour) removes most
  false positives.
- Negative control: an irrelevant pMHC of the same allele, and an unstimulated
  donor.

---

## Cytotoxicity

- **Real-time impedance (xCELLigence/RTCA)** or **live-cell imaging (IncuCyte)**
  give kinetics; endpoint LDH gives a number. Kinetics are more informative and
  usually worth the instrument time.
- Effector:target ratios: titrate 10:1 down to 1:1 at minimum. A single E:T
  ratio is uninterpretable.
- Target must express the epitope **and** the matching HLA. Peptide-pulsed
  targets test recognition; naturally-processing targets test presentation.
  Doing only the first and claiming the second is a common overreach.

---

## HLA immunopeptidomics (HLA-IP + LC-MS/MS)

The closest thing to ground truth for presentation.

- Input: **10⁸–10⁹ cells** or ~1 g tissue for a decent class I yield. This
  requirement is why immunopeptidomics is rare, not because the method is exotic.
- Lyse in mild non-ionic detergent, immunoprecipitate with **W6/32** (pan class
  I) or **L243 / anti-HLA-DR** (class II).
- Acid elution, C18 clean-up, peptide separation, LC-MS/MS.
- Search: standard database search plus a **personalized proteogenomic
  database** built from that tumor's own variants — otherwise the mutant
  peptide is not in the search space and cannot be found. This is the single
  most common way an immunopeptidomics experiment fails to find neoantigens.
- Validate hits with synthetic heavy-labelled peptides and retention-time
  matching; MS/MS spectral match alone is not identification at this stakes level.
- **Interpret absence carefully.** MS sensitivity means low-copy epitopes are
  routinely missed. "Not detected" ≠ "not presented".

---

## TCR sequencing and clonal tracking

- Bulk TCRβ (immunoSEQ-style) is cheap and tracks clonotype frequency over
  time — good for "did vaccination expand anything?"
- **Paired α/β single-cell** is what you need to reconstruct and test a TCR.
- Pair with scRNA-seq for phenotype-clonotype linkage: an expanded clone that
  is transcriptionally exhausted is a different result from an expanded
  stem-like clone.
- Expansion alone is not specificity. Confirm with multimer, functional assay,
  or TCR reconstruction and re-expression.
- Sampling: pre-vaccine baseline is mandatory. Without it you cannot distinguish
  expansion from pre-existing frequency.

---

## Mouse vaccination study — a therapeutic MC38 template

| Element | Choice | Reason |
|---|---|---|
| Mice | C57BL/6, female, 6–8 weeks, n≥10/group | Strain match; power |
| Tumor | 5 × 10⁵ MC38 s.c. flank, day 0 | Standard, reproducible take rate |
| Randomize | Day 7, at ~50–100 mm³ | Baseline balance on tumor volume |
| Vaccine | Day 7, 14, 21 s.c. contralateral flank | Therapeutic setting |
| Groups | (1) vehicle, (2) adjuvant alone, (3) irrelevant peptide + adjuvant, (4) neoantigen + adjuvant, (5) neoantigen + adjuvant + anti-PD-1 | Group 2 is what separates a vaccine effect from innate activation |
| Depletion | Anti-CD8 or anti-CD4 arm + isotype | Mechanism |
| Endpoints | Tumor volume 2–3×/week (blinded), survival to ethical endpoint | Predefined |
| Immune readout | Day 21 spleen/dLN ELISpot + tetramer; TIL flow | Links effect to mechanism |
| Rechallenge | Survivors at day 60, opposite flank, + naive controls | Memory |

The **adjuvant-alone group is the one most often missing** from published
designs, and it is the one that decides whether you have a vaccine or an
inflammation experiment.

---

## Peptide handling

- Order >90% purity with HPLC and MS certificates; keep them.
- Reconstitute in DMSO for hydrophobic peptides, water/PBS for hydrophilic.
  Some peptides need small amounts of acetic acid or ammonium bicarbonate —
  a peptide that will not dissolve is a peptide that will not work.
- Aliquot immediately, store at −80 °C, avoid freeze-thaw cycles (>3 is
  detectable as loss of signal).
- Long peptides (25-mers) aggregate more; check solubility before designing a
  20-peptide screen around them.
