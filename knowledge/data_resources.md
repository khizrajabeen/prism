# Databases and data resources

Where the data lives, what each source is actually good for, and its access
terms. Access model matters as much as content — a resource you cannot get
approval for is not a resource.

---

## Sequence and variant

| Resource | Content | Access |
|---|---|---|
| **TCGA** (via GDC) | ~11k tumors, 33 types; WES/WGS/RNA-seq/methylation/clinical | Open for derived data; controlled for raw |
| **ICGC / PCAWG** | International, whole-genome focus | Mixed open/controlled |
| **cBioPortal** | Curated, queryable front end over many studies | Open, and the fastest way to answer "is this mutation recurrent?" |
| **COSMIC** | Curated somatic mutations, signatures | Free for academic, registration |
| **gnomAD** | Germline population variation | Open — use it to filter germline contamination out of "somatic" calls |
| **dbGaP / EGA** | Controlled-access human data | Application required; budget weeks to months |
| **DepMap** | Cell line genomics + CRISPR dependency | Open; essential for choosing model lines |

## Immunology-specific

| Resource | Content | Notes |
|---|---|---|
| **IEDB** | Epitopes, assays, MHC binding data; the field's backbone | Open. Also hosts the standard prediction tools and the rolling benchmark |
| **IPD-IMGT/HLA** | HLA allele reference sequences | Open; the authority for allele nomenclature |
| **Allele Frequency Net (AFND)** | HLA allele frequencies by population | Open; use it when reasoning about population coverage of a shared-neoantigen product |
| **HLA Ligand Atlas** | Immunopeptidomics across normal tissues | Open; the reference for "is this peptide also presented on healthy tissue?" — i.e. your safety filter |
| **PRIDE / MassIVE** | Proteomics/immunopeptidomics raw data | Open |
| **VDJdb, McPAS-TCR, IEDB receptor tables** | TCR sequences with known specificity | Open; small, biased toward viral epitopes, and that bias propagates into every TCR specificity model trained on them |
| **TCIA / TCIA-neoantigen resources** | Precomputed neoantigen predictions for TCGA | Useful for hypothesis generation |
| **Human Protein Atlas** | Tissue expression + subcellular localization | Open; the practical check for on-target/off-tumor risk |
| **GTEx** | Normal tissue expression | Open; same purpose, quantitative |

**The safety pairing worth internalizing:** before nominating any shared
target, check expression in normal tissue (**GTEx**, **HPA**) *and* whether the
peptide appears in the normal immunopeptidome (**HLA Ligand Atlas**). Predicted
tumor-specificity at the transcript level is not tumor-specificity at the
peptide level.

## Trials and regulatory

- **ClinicalTrials.gov** (API v2) — swept automatically; status history is kept.
- **EU CTIS** / **ISRCTN** / **ANZCTR** / **ChiCTR** — non-US registrations that
  ClinicalTrials.gov misses. A genuine blind spot in an automated US-centric sweep.
- **FDA / EMA** approval documents and advisory committee materials — the review
  documents contain far more methodological detail than the resulting papers.

## Literature

- **Europe PMC** — journals + preprints in one query language, open API, no key.
  The sweep's primary source for exactly this reason.
- **PubMed / E-utilities** — MEDLINE, MeSH-indexed.
- **bioRxiv / medRxiv APIs** — the direct preprint feed, days ahead of indexing.
- **OpenAlex** — open bibliographic graph; good for citation networks and for
  finding who is actually working on something.
- **Crossref** — DOI metadata, and the route to check whether a preprint has
  since been published (and whether it was retracted).
- **Retraction Watch database** (now integrated into Crossref) — worth checking
  before you cite anything foundational.

## Reference and ontology

- **Ensembl / RefSeq / GENCODE** — transcript annotation. Version-pin this;
  changing GENCODE versions changes your peptide set.
- **UniProt** — protein sequences, the reference proteome for self-similarity
  filtering.
- **Gene Ontology, Reactome, MSigDB** — pathway analysis.
- **Cell Ontology / Human Cell Atlas** — single-cell reference annotation.

---

## Practical notes on access

- Controlled-access applications (dbGaP, EGA) take **weeks to months**. If your
  project depends on one, start it before you need it.
- Institutional data-use agreements often already exist — ask your data manager
  before starting a fresh application.
- For anything patient-identifiable, the ethics approval governs what may
  touch a laptop at all. **Do not put controlled-access raw data into a local
  agent's workspace** without checking the DUA — "it stays on my machine" is
  not automatically compliant, and an agent that indexes it into a database has
  created a copy the DUA may not permit.
