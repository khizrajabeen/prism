# Delivery platforms and formulation

What a platform choice actually buys you, and what it costs.

---

## The comparison that matters

| Platform | Manufacturing | Class I / II | Repeat dosing | Main limitation |
|---|---|---|---|---|
| **mRNA-LNP** | Fast (weeks), fully synthetic, scales to personalization | Both (endogenous expression + cross-presentation) | Good; anti-PEG responses debated | Cold chain; innate reactogenicity; LNP biodistribution favours liver on IV |
| **Synthetic long peptides + adjuvant** | Fast, cheap per epitope, well-understood chemistry | Both (long peptides require APC processing) | Good | Solubility and aggregation; HLA-restricted per patient; limited epitope count per injection |
| **Short peptides (minimal epitopes)** | Simplest | Class I only | Good | Loaded directly onto non-professional APCs → **tolerance risk**. Largely superseded by long peptides for this reason |
| **DNA plasmid** | Cheap, stable | Both | Good | Historically weak immunogenicity in humans without electroporation |
| **Viral vector (ChAd, MVA, VSV)** | Slow, complex | Both, strong | **Anti-vector immunity limits repeats** → prime-boost with heterologous vectors | Manufacturing time is incompatible with rapid personalization |
| **Dendritic cell vaccine** | Slow, per-patient cell culture, expensive | Both, controllable | Limited by cost | Labour and cost; variable DC quality |
| **Self-amplifying / circular RNA** | Fast; lower dose needed | Both | Emerging | Newer, less clinical track record |
| **Nanoparticle / liposome peptide** | Moderate | Both | Good | Formulation complexity |

**The structural constraint on personalization is turnaround time.** Sequence →
predict → synthesize → release → dose is measured in weeks, and the patient's
disease does not pause. This is the main reason the field pushes toward the
adjuvant/MRD setting and toward off-the-shelf shared-neoantigen products.

---

## mRNA-LNP specifics

- **Nucleoside modification** (e.g. N1-methylpseudouridine) reduces innate
  sensing and raises expression; some cancer vaccine designs deliberately keep
  more innate stimulation for adjuvanticity. This is a real design tension:
  the same innate sensing that adjuvantizes also degrades the RNA and limits
  expression.
- **LNP composition** (ionizable lipid, helper phospholipid, cholesterol,
  PEG-lipid) governs biodistribution and reactogenicity. Ionizable lipid pKa
  ~6.2–6.5 for endosomal escape.
- **Route determines destination.** IV LNP goes largely to liver and spleen;
  intramuscular and intradermal drain to lymph nodes. Some cancer vaccine
  designs deliberately target the spleen (charge-tuned lipoplexes) to reach
  splenic DCs.
- **Concatemer design**: multiple epitopes strung with linkers, encoding both
  class I and class II candidates.

## Peptide vaccine specifics

- **Long (25–30-mer) over short (9-mer).** Long peptides cannot load directly
  onto surface MHC-I of non-professional APCs; they require uptake and
  processing by a professional APC, which avoids the tolerance induction that
  short peptides risk.
- **Adjuvant is the immunological choice**, not a formulation detail:

| Adjuvant | Receptor/mechanism | Notes |
|---|---|---|
| Poly-ICLC (Hiltonol) | TLR3 / MDA5 → type I IFN | Licenses cDC1 for cross-presentation; the standard for academic neoantigen peptide trials |
| Montanide ISA-51 | Water-in-oil depot | Effective but can trap and sequester T cells at the injection site — a documented failure mode |
| CpG (TLR9) | pDC activation, type I IFN | Strong CD8 priming |
| GM-CSF | DC recruitment/maturation | Mixed history; can recruit MDSCs at the wrong dose |
| Imiquimod/R848 (TLR7/8) | Topical or systemic | Useful for intradermal routes |
| STING agonists | cGAS-STING → type I IFN | Powerful, dose-limiting toxicity is the challenge |
| Alum | NLRP3, Th2-skewing | Poor choice for CD8 cancer vaccines |

## Construct design: epitope ordering

When you concatenate epitopes, the sequence spanning each junction is itself a
potential MHC binder — a **junctional neoepitope** present in neither the tumor
nor the patient. It diverts response to a target no tumor cell displays.

Mitigations:
- Order epitopes to minimize predicted junctional binders (**pVACvector** does
  exactly this, as a shortest-path problem over predicted junction scores).
- Insert cleavage-favourable linkers (AAY, GPGPG, furin sites) — with the
  caveat that linkers themselves can form epitopes.
- Include a signal peptide / MHC-trafficking sequence if you want to bias class
  I vs class II presentation.
- Re-run prediction **on the final construct sequence**, not on the epitope
  list. This step is skipped surprisingly often.

## Combination strategy

Nearly every program combines with checkpoint blockade, and the reasoning is
mechanistic rather than opportunistic: the vaccine supplies the T cells, and
PD-1 blockade keeps them functional in the microenvironment. Other rational
combinations under study: CD40 agonism (DC licensing), IL-2/IL-15 variants
(expansion), TLR agonists in situ, and low-dose radiotherapy or chemotherapy as
an antigen-release and immunogenic-cell-death step — with the ever-present
caveat that lymphodepleting chemotherapy can also remove the cells you just
primed. Sequencing matters and is under-studied.
