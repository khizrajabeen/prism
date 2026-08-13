# Resistance and escape — what happens after the response

A vaccine that induces T cells and then fails has told you something specific.
This is the differential diagnosis.

---

## The escape routes, and how to detect each

| Mechanism | What it breaks | How you detect it |
|---|---|---|
| **β2M loss/mutation** | All surface MHC-I | MHC-I flow/IHC negative; sequence B2M; check both alleles |
| **HLA LOH** | Presentation on the lost haplotype only | Allele-specific copy number (LOHHLA-type analysis). **MHC-I staining stays positive** — this is why it is missed |
| **HLA allele-specific downregulation** | Presentation on one allele | Allele-specific qPCR/MS; not visible on pan-MHC-I stain |
| **APM component loss** (TAP1/2, tapasin, ERAP, immunoproteasome subunits) | Peptide supply | Expression of the pathway; restored by IFN-γ treatment in vitro if transcriptional |
| **JAK1/JAK2 loss** | IFN-γ responsiveness → no MHC upregulation, no growth arrest | Sequence JAK1/2; test IFN-γ-induced MHC upregulation in vitro |
| **Antigen loss / subclonal elimination** | The specific target | Re-sequence the progressing lesion; compare CCF pre/post |
| **Neoantigen transcriptional silencing** | Expression of the target | RNA-seq of the progressing lesion; promoter methylation |
| **T cell exhaustion** | Effector function | TOX, PD-1/TIM-3/LAG-3 co-expression; scRNA-seq trajectory |
| **Immune exclusion** | Trafficking | Spatial IHC/imaging — T cells at the margin, not in the nest |
| **Suppressive microenvironment** | Function on arrival | Treg/MDSC/TAM density; IDO1, adenosine axis |
| **Loss of cDC1** | Priming and re-priming | cDC1 signature in bulk/scRNA-seq |

Note the pattern: **the first three all look like "antigen presentation is
fine" on a pan-MHC-I stain.** Allele-specific analysis is not an optional
refinement, it is how you avoid misattributing the failure.

---

## Immunoediting, and why targeting matters

Tumors under immune pressure lose their most immunogenic clones. Practical
consequences:

- The neoantigens present in a **treatment-naive** biopsy are not necessarily
  those present at progression. Sequencing the wrong timepoint gives you the
  wrong targets.
- **Clonal** neoantigens are the durable targets; killing subclonal ones
  selects for the clones that lack them and produces mixed responses — some
  lesions shrinking while others grow, which is the clinical signature.
- Multi-epitope targeting is the standard hedge. Targeting ≥10–20 epitopes
  makes complete escape require many simultaneous losses.

## Designing against escape

1. **Target clonal, expressed, ideally driver-associated mutations.** A driver
   is harder to lose than a passenger, because losing it costs the tumor fitness.
   This is a large part of the appeal of KRAS G12D/G12V and TP53 hotspots.
2. **Multi-epitope, multi-allele coverage** so that HLA LOH on one haplotype
   does not disarm the whole construct.
3. **Include class II epitopes** — CD4 help sustains the CD8 response, and CD4
   effector mechanisms are not disabled by class I loss.
4. **Pair with something that works when presentation fails**: NK-engaging
   approaches are the natural complement to B2M loss, which sensitizes to NK
   killing by removing inhibitory MHC-I signals.
5. **Re-biopsy at progression.** Almost nobody does this and it is where the
   mechanistic answers are.

## The uncomfortable structural constraints

- Patients with high tumor burden are the ones who most need a vaccine and the
  ones in whom it works least well — immunosuppression is established, the
  repertoire is depleted, and priming takes weeks the patient may not have.
- Manufacturing turnaround competes with disease progression.
- Cost per patient for bespoke manufacture is a genuine barrier to access, not
  merely a commercial detail.

These are why the field is converging on the **adjuvant / minimal residual
disease** setting, on **ctDNA-guided** patient selection, and on **shared
neoantigens** as an off-the-shelf alternative. All three are attempts to route
around the same structural problem rather than solve it.
