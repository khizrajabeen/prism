# Glossary

Terms that recur, defined the way they are actually used. Acronyms get defined
on first use in every session — this file is the reference for that.

---

**Agretopicity / DAI (differential agretopicity index)** — the difference in
predicted MHC binding between a mutant peptide and its wild-type counterpart.
High DAI implies the mutation created presentation that did not previously
exist, so the repertoire was not tolerized against it.

**AIM assay** — activation-induced marker assay. Detects antigen-specific T
cells by surface marker upregulation (OX40, CD137, CD69) rather than cytokine
secretion. Better than ELISpot for CD4 responses.

**APM** — antigen processing machinery: proteasome/immunoproteasome, TAP,
tapasin, ERAP, β2m. Loss of any component is an escape mechanism.

**β2m (β2-microglobulin)** — the invariant light chain of every MHC class I
molecule. Its loss removes all surface class I at once.

**CCF (cancer cell fraction)** — the fraction of tumor cells carrying a
mutation, corrected for purity and copy number. The right measure of clonality;
raw VAF is not.

**cDC1** — conventional dendritic cell type 1 (XCR1⁺, CLEC9A⁺, BATF3-dependent).
The specialist cross-presenting APC, and the mechanistic bottleneck of every
cancer vaccine.

**CLIP** — class II-associated invariant chain peptide; the placeholder in the
class II groove removed by HLA-DM.

**Cross-presentation** — an APC loading exogenous antigen onto MHC class I. How
a vaccine primes CD8 responses at all.

**DRiPs** — defective ribosomal products; rapidly degraded nascent proteins that
are a major source of class I peptides. Why presentation tracks translation
rather than protein abundance.

**ELISpot** — enzyme-linked immunospot. Counts cytokine-secreting cells per
well. The field-standard immunogenicity screen.

**ERAP1/2** — ER aminopeptidases that trim peptide N-termini to final length.
Can destroy epitopes as well as create them.

**GEMM** — genetically engineered mouse model. Autochthonous tumors; generally
low mutational burden, which limits their use for neoantigen work specifically.

**HLA LOH** — loss of heterozygosity at the HLA locus: the tumor loses one
haplotype while retaining the other. Invisible to pan-MHC-I staining.

**ICS** — intracellular cytokine staining. Flow-based per-cell cytokine
measurement; gives polyfunctionality.

**Immunoediting** — the process by which immune pressure selects for tumor
clones that are less immunogenic. Why the treatment-naive biopsy may not
describe the progressing tumor.

**Immunopeptidomics** — HLA immunoprecipitation followed by LC-MS/MS to
directly observe presented peptides. The closest thing to presentation ground
truth.

**Immunoproteasome** — IFN-γ-induced proteasome variant (β1i/β2i/β5i) with
altered cleavage specificity, producing a partly different peptide repertoire.

**Junctional neoepitope** — an artifact epitope formed across the boundary
between two epitopes in a concatemer construct. Present in the vaccine, absent
from the tumor.

**MHC / HLA** — major histocompatibility complex; HLA is the human version.
Class I (A, B, C) presents to CD8; class II (DR, DQ, DP) presents to CD4.

**MIATA** — Minimal Information About T cell Assays; the reporting standard.

**MRD** — minimal residual disease. Post-treatment disease below imaging
detection, increasingly identified by ctDNA. The setting the vaccine field is
moving into.

**MSI-H** — microsatellite instability-high. Mismatch-repair-deficient tumors
with very high indel burden and correspondingly high neoantigen load.

**Neoantigen** — a peptide arising from a tumor-specific alteration (mutation,
fusion, splice variant, retroelement) that is presented on MHC and can be seen
by a T cell. Distinct from a tumor-associated antigen, which is self.

**Neoepitope** — often used interchangeably with neoantigen; strictly, the
specific peptide-MHC combination recognized.

**pMHC** — peptide-MHC complex. The actual ligand a TCR sees.

**Percentile rank** — a peptide's predicted binding relative to a reference
peptide set for that allele. Comparable across alleles in a way that raw nM
affinity is not.

**Polyfunctionality** — a T cell producing multiple cytokines simultaneously.
Correlates with protection better than any single cytokine.

**pVACtools** — the most widely used open neoantigen prediction framework
(pVACseq, pVACbind, pVACfuse, pVACvector).

**SLP** — synthetic long peptide (typically 25–30-mers). Requires APC
processing, avoiding the tolerance risk of short minimal epitopes.

**TAP** — transporter associated with antigen processing. Moves peptides from
cytosol into the ER for class I loading.

**TCR-T** — T cells engineered to express a defined TCR, used against shared
neoantigens where an off-the-shelf receptor is available.

**TESLA** — Tumor nEoantigen SeLection Alliance; the multi-team prospective
benchmark that is the field's reality check on prediction accuracy.

**TIL** — tumor-infiltrating lymphocyte; also the adoptive therapy built from
expanding them.

**TMB** — tumor mutational burden, usually mutations per megabase. Correlates
with checkpoint response at the population level; a poor predictor in an
individual patient.

**TPM** — transcripts per million; the expression unit used to filter
candidates for actual transcription.

**VAF** — variant allele frequency; the fraction of reads carrying the variant.
Confounded by purity and copy number — convert to CCF before reasoning about
clonality.
