# The computational stack — what to install, what to run, what to distrust

Tool names date quickly; the *reasoning* about which tool and why does not.
Verify current versions before quoting performance numbers, and record the
version you actually ran in your methods.

---

## Environment

```bash
# Reproducible base. Pin everything; "latest" is not a version.
mamba create -n neo -c conda-forge -c bioconda \
    python=3.11 samtools bcftools bedtools star salmon fastp multiqc snakemake
mamba activate neo
mamba env export --no-builds > env.lock.yml     # commit this
```

For the Python analysis layer, `uv` is now the fastest sane default:

```bash
uv venv && source .venv/bin/activate
uv pip install pandas polars scanpy scikit-learn matplotlib seaborn pysam biopython
uv pip freeze > requirements.lock              # commit this too
```

Containers (Docker/Apptainer) beat conda for anything you will run in a year.
Bioconda recipes drift; an image with a digest does not.

---

## The pipeline, stage by stage

### 1. QC and alignment
`fastp` or `fastqc`+`trimmomatic` → `bwa-mem2` (DNA) / `STAR` (RNA) →
`samtools` sort/index → `gatk MarkDuplicates` → BQSR.
`multiqc` to look at everything at once — and actually look at it. Duplicate
rate and coverage uniformity predict downstream variant-calling quality better
than any post-hoc filter.

### 2. Somatic variants

```bash
gatk Mutect2 -R ref.fa -I tumor.bam -I normal.bam -normal NORMAL_SM \
    --germline-resource af-only-gnomad.vcf.gz --panel-of-normals pon.vcf.gz \
    -O raw.vcf.gz
gatk FilterMutectCalls -R ref.fa -V raw.vcf.gz -O filtered.vcf.gz
```

Run a second caller (Strelka2) and take the intersection for high confidence,
the union when sensitivity matters more. State which you did.

### 3. Annotation and expression

- `VEP` (with the `--pick` / `--flag_pick` behaviour you can defend, and the
  Wildtype + Frameshift plugins pVACtools requires) or `SnpEff`.
- Expression: `salmon`/`kallisto` for transcript TPM, or `featureCounts` for
  gene counts. pVACseq wants per-transcript TPM to filter candidates.
- Phasing: `GATK ReadBackedPhasing` or `WhatsHap` — two variants in the same
  peptide produce a different peptide than either alone.

### 4. HLA typing

```bash
OptiType --input tumor_1.fq tumor_2.fq --dna --outdir hla/       # class I
arcasHLA genotype rna.bam -g A,B,C,DPB1,DQB1,DRB1 -o hla/        # class I+II from RNA
```

Then check **HLA LOH** before you trust any allele in downstream prediction.

### 5. Prediction and ranking

```bash
pvacseq run filtered.annotated.vcf TUMOR_SAMPLE \
    "HLA-A*02:01,HLA-B*07:02,HLA-DRB1*15:01" \
    NetMHCpan NetMHCIIpan MHCflurry \
    output/ \
    --iedb-install-directory /opt/iedb \
    --expn-val 1.0 --normal-vaf 0.02 --tdna-vaf 0.10 \
    --net-chop-method cterm --netmhc-stab \
    --binding-threshold 500 --percentile-threshold 2
```

Every one of those thresholds is a scientific choice you should be able to
defend. The defaults are conventions, not findings.

### 6. Construct design

`pvacvector` for epitope ordering; re-predict on the assembled sequence.

---

## Single-cell and spatial

- **scanpy** (Python) / **Seurat** (R) for scRNA-seq; **scirpy** / **Dandelion**
  for paired TCR; **scvi-tools** for integration and batch correction.
- Standard trap: over-integration erases the biological difference you were
  looking for. Always inspect pre- and post-integration embeddings.
- Spatial: **squidpy**, **Giotto**; and for tumor–immune spatial statistics,
  neighbourhood enrichment beats "we saw T cells nearby".
- Deconvolution of bulk RNA (CIBERSORTx, quanTIseq, MCP-counter) is a screening
  tool, not a measurement. Treat the numbers as ordinal.

---

## Structure and interaction prediction

- **AlphaFold** family and successors for pMHC and TCR–pMHC modelling. Useful
  for hypothesis generation about TCR-facing residues; **not** reliable enough
  to rank immunogenicity on its own.
- **Rosetta / FoldX** for ΔΔG on mutations in the groove.
- Molecular dynamics for pMHC stability — expensive and rarely decisive.

Be sceptical of any claim that structural modelling has solved immunogenicity.
Check whether the reported performance came from a held-out set that shares
alleles or source proteins with training data. This specific leak is endemic.

---

## Machine learning on this data — the field-specific traps

1. **Data leakage by peptide similarity.** Random train/test splits put near-
   identical peptides on both sides. Split by **source protein**, or by
   sequence-identity clustering, not by row.
2. **Allele imbalance.** Training data is dominated by HLA-A*02:01. Performance
   on rare alleles is much worse and often unreported. Break results out by
   allele.
3. **Class imbalance.** Real immunogenic peptides are a tiny minority. AUROC
   looks great and means little; report **precision-recall** and, better, the
   number of true positives in your top-N — because top-N is how the tool is
   actually used.
4. **Negative set construction.** "Random peptides" as negatives makes the task
   artificially easy. Decoys matched for length, source protein, and expression
   are the honest choice.
5. **Benchmark contamination.** Public benchmarks leak into training sets over
   time. Prefer prospective evaluation (TESLA-style) over another leaderboard.

---

## A minimal analysis skeleton worth copying

```python
"""Rank neoantigen candidates. One entry point, testable core, no notebook state."""
from pathlib import Path
import pandas as pd

MIN_TPM, MAX_RANK, MIN_CCF = 1.0, 2.0, 0.6

def load_candidates(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t")
    required = {"peptide", "hla", "mt_percentile", "wt_percentile", "tpm", "ccf"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    return df

def rank(df: pd.DataFrame) -> pd.DataFrame:
    """Filter, then score. Keep the two steps separate so you can audit each."""
    keep = df[(df.tpm >= MIN_TPM) & (df.mt_percentile <= MAX_RANK) & (df.ccf >= MIN_CCF)].copy()
    # Differential agretopicity: how much binding the mutation created.
    keep["dai"] = keep.wt_percentile / keep.mt_percentile.clip(lower=1e-3)
    keep["score"] = (
        keep.dai.clip(upper=100).pipe(lambda s: s.rank(pct=True))
        + keep.tpm.pipe(lambda s: s.rank(pct=True))
        + keep.ccf.pipe(lambda s: s.rank(pct=True))
    )
    return keep.sort_values("score", ascending=False)

if __name__ == "__main__":
    import sys
    out = rank(load_candidates(Path(sys.argv[1])))
    out.to_csv(sys.argv[2], sep="\t", index=False)
    print(f"{len(out)} candidates passed filters; top: {out.peptide.head(5).tolist()}")
```

Rank-based combination avoids pretending that TPM, percentile rank, and CCF
live on a common scale. They do not, and weighted sums of raw values quietly
let whichever variable has the widest range dominate the ranking.
