# Antigen presentation and T cell recognition — the mechanism everything rests on

If you can trace a mutation from DNA to a T cell synapse and name every step
that could kill it, most of the rest of this field becomes readable. This file
is that trace.

> Maintained note. The agent may propose edits with dated sources; it may not
> edit this file directly.

---

## 1. The class I pathway — what the cell is making

Every nucleated cell continuously reports its internal protein synthesis to the
immune system through MHC class I.

1. **Source.** Cytosolic proteins, heavily weighted toward defective ribosomal
   products (DRiPs) — misfolded or prematurely terminated nascent chains
   degraded within minutes of synthesis. This is why presentation tracks
   *current translation*, not steady-state protein abundance, and why a
   mutation in a highly transcribed gene is more likely to be presented than
   one in a stable, long-lived protein.
2. **Proteasome.** Degrades ubiquitinated substrates. The **immunoproteasome**
   (β1i/β2i/β5i subunits, induced by IFN-γ) has altered cleavage preferences and
   generates a partly different peptide repertoire — relevant because an
   inflamed tumor presents a different peptide set than the same tumor
   unperturbed, and because your predictor was probably trained on one of them.
3. **TAP.** Transports peptides from cytosol into the ER. Has its own sequence
   preferences (disfavours proline near the N-terminus, prefers hydrophobic or
   basic C-termini). TAP deficiency is a real tumor escape mechanism.
4. **Peptide loading complex.** Tapasin, ERp57, calreticulin, and MHC-I heavy
   chain + β2-microglobulin. **Tapasin acts as a peptide editor**, exchanging
   low-affinity peptides for higher-affinity ones. ERAP1/ERAP2 trim the
   N-terminus to the final 8–11 residues — and can destroy an epitope as easily
   as create one.
5. **Surface display.** The stable pMHC-I complex traffics to the plasma
   membrane, where a CD8 T cell may or may not have a TCR that reads it.

**Consequences you will use constantly**

- Losing **β2m** removes all class I from the surface. One mutation, complete
  escape from every CD8 response simultaneously.
- The groove is closed at both ends, so class I peptides are **8–11 residues**,
  usually 9. Anchor residues (commonly P2 and PΩ) determine allele specificity.
- A mutation at an **anchor** position changes binding; a mutation at a
  **TCR-facing** position (roughly P4–P6 in a 9-mer) changes recognition without
  changing binding. These are different kinds of neoantigen and behave
  differently — the anchor-modified one may be presented where the WT was not,
  the TCR-facing one may be presented identically to a self peptide the
  repertoire is tolerized against.

## 2. The class II pathway — what the neighbourhood contains

Restricted to professional antigen-presenting cells (dendritic cells, B cells,
macrophages; and inducibly on other cells under IFN-γ).

1. Exogenous material is endocytosed and degraded by cathepsins in an
   increasingly acidic endosomal compartment.
2. MHC-II is held peptide-free by the **invariant chain (Ii/CD74)**, which is
   trimmed to **CLIP** sitting in the groove.
3. **HLA-DM** catalyses CLIP release and edits the bound peptide toward
   kinetic stability; **HLA-DO** modulates DM in B cells and thymic epithelium.
4. The loaded complex reaches the surface and is read by CD4 T cells.

**Consequences**

- The groove is **open at both ends**, so peptides are **13–25 residues** with a
  9-residue binding core plus flanking residues that contribute to stability and
  TCR contact. The register of that core is ambiguous, which is the single
  biggest reason class II predictors are weaker than class I.
- Class II polymorphism includes both chains for DP and DQ (α and β), so the
  functional allele space is larger and typing is harder.

## 3. Cross-presentation — how a vaccine works at all

A vaccine does not put antigen into the tumor cell's own class I pathway. It
depends on APCs — chiefly **cDC1** (XCR1⁺, CLEC9A⁺, BATF3-dependent) — taking up
exogenous antigen and loading it onto **class I**. This is cross-presentation,
and it is the mechanistic bottleneck of every peptide, mRNA, and protein cancer
vaccine.

Practical implications:

- Adjuvant choice is really a choice about **how you license cDC1**. Poly-ICLC
  (TLR3/MDA5 → type I IFN) is the canonical one for this reason.
- Tumors that exclude or deplete cDC1 are poor vaccine responders regardless of
  neoantigen load.
- Draining lymph node biology matters. Intradermal and intranodal routes exist
  because where the antigen goes determines which DCs see it.

## 4. Why CD4 help is not optional

Early neoantigen work was class I-centric on the reasoning that CD8 cells do the
killing. That framing is incomplete:

- CD4 help licenses DCs (CD40–CD40L), without which CD8 priming is weak and the
  resulting memory is poor.
- CD4 T cells sustain CD8 responses and prevent the exhaustion trajectory.
- Class II neoepitopes are found at meaningful frequency in vaccine-induced
  responses, and the practical peptide formats (synthetic **long** peptides,
  mRNA-encoded strings) present both classes rather than only class I.

If you are designing a construct, design for both. Long peptides (25–30-mers)
covering the mutation are the standard hedge: they require processing by an APC
(so they are not loaded directly onto class I of non-professional cells, which
is tolerogenic) and can yield both class I and class II epitopes.

## 5. T cell repertoire and tolerance — the ceiling on everything

Prediction assumes a T cell exists that can see the epitope. Often none does.

- **Central tolerance**: thymocytes with high-affinity TCRs against self peptide
  presented by thymic epithelium (AIRE-driven promiscuous expression) are
  deleted. A neoantigen differing from self by one residue may be seen by a
  repertoire already pruned of the relevant clones.
- **Peripheral tolerance**: Tregs, anergy, and deletion continue in tissue.
- **Precursor frequency** for any given naive specificity is on the order of 1
  in 10⁵–10⁶ CD8 T cells. Some epitopes simply have no available responder.

This is the honest answer to "is prediction the bottleneck?" — partly. The other
part is that the repertoire may not contain the receptor, and no amount of
predictor accuracy fixes that.

## 6. Reading a TCR–pMHC interaction

- TCR binds diagonally across the pMHC surface; CDR3α/β dominate peptide
  contact, CDR1/CDR2 contact the MHC helices.
- Affinities are weak (µM) compared to antibodies (nM–pM), and **kinetics
  matter more than affinity** — dwell time and mechanical catch-bond behaviour
  predict activation better than KD alone.
- Cross-reactivity is a feature, not a bug: any given TCR must recognize many
  peptides for the repertoire to cover sequence space. This is also the source
  of on-target/off-tumor toxicity in TCR-T therapy, and why alanine scanning
  and proteome-wide cross-reactivity screening are mandatory before clinical
  TCR use.

## 7. The tumor microenvironment, in the terms that matter here

A vaccine-induced T cell has to survive the place it is sent. What it meets:

- **Physical exclusion** — stroma, desmoplasia (pancreatic tumors especially),
  aberrant vasculature.
- **Suppressive cells** — Tregs, MDSCs, M2-like TAMs.
- **Metabolic hostility** — hypoxia, low glucose, lactate acidosis, tryptophan
  depletion via IDO1, adenosine via CD39/CD73.
- **Inhibitory signalling** — PD-L1, and the wider set of checkpoints
  (LAG-3, TIM-3, TIGIT).
- **Progressive exhaustion** — TOX-driven epigenetic program; terminally
  exhausted cells do not recover with checkpoint blockade. The stem-like
  TCF1⁺ progenitor pool is what responds.

This is the mechanistic reason vaccines are being pushed into the **adjuvant /
minimal residual disease** setting: low burden, less established
immunosuppression, and time to prime.

---

## Where people go wrong

1. Treating predicted binding affinity as if it were immunogenicity.
2. Ignoring class II entirely, then being surprised by weak, short-lived CD8 responses.
3. Forgetting that the tumor cell is not the APC for a vaccine.
4. Assuming a neoantigen is foreign in the way a viral antigen is — one residue
   from self is not the same as a pathogen protein, and the repertoire reflects that.
5. Reading MHC-I staining as intact antigen presentation, missing allele-specific HLA loss.
