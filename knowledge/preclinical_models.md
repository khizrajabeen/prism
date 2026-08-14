# Preclinical models: in vitro, in vivo, and the mouse

The assays and models downstream of prediction, and the specific ways each one
misleads.

> Maintained note. Extended from the original seed file.

---

## In vitro / ex vivo T cell assays

### Readouts of antigen-specific response

| Assay | Measures | Sensitivity | Use it when | Fails when |
|---|---|---|---|---|
| **IFN-γ ELISpot** | Cytokine-secreting cells per well | ~1 in 10⁵–10⁶ | Field standard for vaccine immunogenicity; screening many peptides | Response is non-IFN-γ (CD4 subsets); needs good negative controls; semi-quantitative |
| **Intracellular cytokine staining (ICS)** | IFN-γ, TNF-α, IL-2 per cell by flow | ~1 in 10⁴–10⁵ | You need polyfunctionality and phenotype together | Lower sensitivity than ELISpot; requires more cells |
| **pMHC tetramer / multimer** | Direct frequency of epitope-specific cells | ~1 in 10⁵ | Enumerating and sorting antigen-specific cells regardless of function | Needs a synthesized pMHC per epitope-allele pair; low-affinity TCRs stain poorly |
| **AIM (activation-induced markers)** | CD137/4-1BB, OX40, CD69 upregulation | high | CD4 responses; function-independent detection | Background needs careful gating; 18–24h stimulation |
| **Cytotoxicity** (LDH, xCELLigence/RTCA, ⁵¹Cr, IncuCyte) | Target cell killing | — | The functional endpoint that matters | Needs a relevant target line expressing the epitope + matched HLA |
| **ELISA / Luminex on supernatant** | Bulk cytokine secretion | — | Cheap multiplexed screen | No single-cell resolution; cannot attribute to a cell type |
| **TCR sequencing (bulk or paired α/β)** | Clonal expansion after vaccination | — | Tracking specific clonotypes over time; increasingly the most informative readout | Expansion ≠ specificity without a matched functional or multimer assay |
| **scRNA-seq + TCR-seq** | Phenotype-clonotype linkage | — | Asking what the responding cells *are*, not just that they exist | Cost; requires real analysis effort |

### Supporting techniques

- **MHC stabilization assay** (T2 cells for HLA-A*02:01; RMA-S for murine) to
  confirm peptide binding empirically rather than trusting the predictor.
- **In vitro priming / expansion** from PBMC to detect low-frequency naive
  precursors — necessary for most neoantigen-specific responses in an
  unvaccinated donor, and a common source of artifact.
- **Immunopeptidomics** (HLA-IP + LC-MS/MS) for direct presentation evidence.
- **Peptide-MHC stability assays** (e.g. scintillation proximity or NeoScreen-type
  approaches) — half-life predicts immunogenicity better than affinity.

### Recurring pitfalls

- **Peptide purity and solubility.** Order >90% purity, verify by HPLC/MS.
  Track DMSO concentration — carryover above ~0.5% kills cells and creates
  dose-dependent artifacts that look like specificity.
- **Pools vs individual peptides.** Pools save cost but obscure which epitope
  drove the response and create MHC competition artifacts. A pool positive is a
  screening hit, not an epitope identification — deconvolute before claiming one.
- **Controls are the experiment.** Every neoantigen assay needs: the
  **wild-type counterpart peptide**, an **irrelevant peptide**, **DMSO
  vehicle**, and a **positive control** (CEF/CEFT pool, PHA, or anti-CD3).
  A response to mutant *and* WT is a self-epitope response.
- **Cryopreservation damage.** Thawed PBMC lose function unevenly; rest
  overnight before assay and report viability.
- **Reporting.** Follow the MIATA framework for T cell assay reporting. It
  exists because this literature was irreproducible without it.

---

## Mouse models

### Syngeneic transplantable lines — immunocompetent, fast, imperfect

| Line | Background | Tumor type | Character | The specific trap |
|---|---|---|---|---|
| **MC38** | C57BL/6 | Colon adenocarcinoma | High TMB; well-characterized neoantigens (Adpgk, Reps1, Dpagt1); the canonical vaccine proving ground | So immunogenic that many interventions "work"; a positive here is a weak claim on its own |
| **B16F10** | C57BL/6 | Melanoma | Poorly immunogenic, low MHC-I; a hard test. Shared antigens gp100, TRP2 | Effects are often adjuvant-driven innate activation, not antigen-specific |
| **CT26** | BALB/c | Colon carcinoma | Immunogenic, well characterized | The endogenous retroviral **gp70/AH1** response dominates and masks neoantigen signal |
| **4T1** | BALB/c | Triple-negative breast | Spontaneously metastatic, strongly myeloid-suppressed | Very hard; also a good model *because* it is hard |
| **LLC (Lewis lung)** | C57BL/6 | Lung | Poorly immunogenic | Near-universal negative; a null result here says little |
| **Panc02 / KPC-derived** | C57BL/6 | Pancreatic | Desmoplastic, checkpoint-resistant | Low TMB; poor vehicle for neoantigen-specific claims |
| **EO771 / AT-3** | C57BL/6 | Breast | Alternatives to 4T1 on a B6 background | Sub-line variability is notorious |
| **B16-OVA / MC38-OVA** | C57BL/6 | Model antigen | Clean, quantifiable (OT-I/OT-II, SIINFEKL tetramer) | OVA is a foreign protein with an untolerized repertoire — it is a **positive control**, not a neoantigen model |

**The mechanism behind the CT26 trap is immunodominance.** When one epitope
elicits a response that suppresses responses to others presented on the same
cell, the subdominant responses become undetectable — not absent, outcompeted.
The gp70/AH1 response in CT26 is immunodominant, so a genuine neoantigen
response can exist and still be invisible in your ELISpot. The same phenomenon
governs vaccine construct design: a concatemer whose strongest epitope is
immunodominant may suppress the response to the other nineteen. Measuring AH1
reactivity in parallel is how you tell "no neoantigen response" apart from
"a neoantigen response you cannot see".

**Strain matching is non-negotiable.** MC38 in BALB/c is an allograft rejection
experiment. Also: authenticate your lines (STR profiling), test for mycoplasma,
and keep passage number low and recorded — MC38 and 4T1 sub-lines drift
substantially between labs, which is a real and under-reported source of
between-lab disagreement.

### Genetically engineered mouse models (GEMMs)

Autochthonous tumors, intact microenvironment, realistic tumor evolution. Slow,
expensive, variable penetrance.

- **KPC** (*LSL-Kras^G12D; LSL-Trp53^R172H; Pdx1-Cre*) — pancreatic ductal
  adenocarcinoma, the standard desmoplastic/immune-excluded model.
- *Braf/Pten* melanoma; *Kras/p53* (KP) lung.
- **The neoantigen-specific caveat:** GEMMs generally have **low mutational
  burden** and few neoantigens, which makes them poor vehicles for neoantigen
  vaccine studies specifically — the thing you are studying barely exists in
  them. Fixes: carcinogen-induced models (MCA sarcomas, 4NQO oral,
  DMBA/TPA skin, AOM/DSS colon) which generate genuine mutational load, or
  GEMMs engineered to express defined model neoantigens.

### Humanized models

- **HLA-transgenic mice** (HHD for HLA-A2, A2/DR1 double transgenics, and
  broader panels) — test human-restricted epitopes in an otherwise murine
  immune system. Useful for epitope immunogenicity; the TCR repertoire is
  murine and the thymic selection is not human.
- **NSG / NSG-SGM3 / NOG-EXL / MISTRG** reconstituted with human CD34⁺ HSC or
  PBMC — a human immune compartment, but incomplete myeloid and lymphoid
  development, **no HLA-matched thymic education**, and GvHD closes the
  experimental window (weeks for PBMC-humanized).
- **PDX** — preserves tumor heterogeneity and architecture, immunodeficient by
  definition, therefore useless for vaccine immunogenicity unless humanized.
- **Autologous humanized PDX** (patient's own HSC + own tumor) — the most
  faithful and the least practical.

### Ex vivo human systems (often the better answer)

- **Patient-derived organoids** ± autologous T cell co-culture — a genuinely
  useful bridge, and increasingly the right first experiment.
- **Tumor fragment / air-liquid interface cultures** retaining native immune
  infiltrate.
- **Tumor-on-chip / microfluidic** systems for trafficking and killing kinetics.

---

## Experimental design essentials (see `experimental_design.md` for the statistics)

- Tumor volume: **V = (length × width²) / 2**. Predefine humane endpoints and
  stick to them; ethics approval before anything starts.
- **Randomize when tumors reach a defined volume**, not at implantation, and
  blind the person measuring calipers.
- **n ≥ 8–10 per group** for survival endpoints is a rule of thumb, not a
  substitute for a power calculation.
- **Prophylactic** (vaccinate then challenge) vs **therapeutic** (implant then
  vaccinate): prophylactic protection is dramatically easier and routinely
  oversold. Therapeutic is what translates.
- Include a **depletion arm** (anti-CD8, anti-CD4, anti-NK1.1) to establish
  mechanism, and an **isotype control**.
- **Rechallenge survivors** to test memory; rechallenge on the opposite flank.
- Report the **ARRIVE 2.0** items. Sex as a biological variable — immune
  responses differ, and single-sex studies should say so and justify it.
- Cage effects are real: randomize across cages, and treat cage as a blocking
  factor for microbiome-sensitive endpoints.

### Routes and formulations

Vaccines: subcutaneous, intradermal, intramuscular, intranodal.
Tumors: subcutaneous flank (easy, measurable, wrong microenvironment),
orthotopic (right microenvironment, harder to measure), intravenous
(experimental metastasis, not spontaneous metastasis — do not conflate them).

**Orthotopic placement changes the answer**, not just the difficulty. A vaccine
that controls flank tumors and fails orthotopically has told you something
about the microenvironment, not about itself.

---

## Where the model choice usually goes wrong

1. Using B16F10 to show a vaccine "works" when the effect is adjuvant-driven innate activation.
2. Ignoring that CT26's dominant AH1 response swamps the neoantigen signal.
3. Prophylactic-only data presented as therapeutic evidence.
4. Humanized mice without acknowledging absent HLA-matched thymic selection.
5. OVA-model results described as neoantigen results.
6. Small n with post-hoc group exclusion, and survival curves without a log-rank test.
7. Sub-line drift left unmentioned when a result fails to replicate elsewhere.
