# Coding for research that has to survive review

Not general software engineering advice — the specific practices that stop a
computational result from evaporating when someone asks how you got it.

---

## The minimum bar for "reproducible"

1. **Pinned environment** — a lockfile or a container digest. Not `pip install
   pandas`.
2. **Identified input** — accession numbers or checksums for every input file.
3. **One entry point** — a single command that regenerates every figure from
   raw input. `make all`, `snakemake --cores 8`, or `bash run.sh`.
4. **Version control** — the analysis committed at the state that produced the
   result, tagged if it produced a figure in a paper.

Miss any one of those and you will not reproduce your own result in six months.

## Repository layout that works

```
project/
├── README.md            what this is, how to run it, what the result was
├── env.lock.yml         pinned environment
├── Makefile / run.sh    the entry point
├── config/              parameters, no hard-coded paths in code
├── data/
│   ├── raw/             read-only, never edited, checksummed
│   ├── interim/         intermediate, regenerable, gitignored
│   └── processed/       analysis-ready
├── src/                 importable functions with tests
├── scripts/             thin CLI wrappers over src/
├── notebooks/           exploration only — never the source of a result
├── tests/
└── results/figures/     regenerable output
```

**The notebook rule.** Notebooks are for exploring. The moment a result matters,
move the logic into `src/` behind a function, call it from a script, and let the
notebook import it. Out-of-order cell execution has produced more retracted
figures than any bug.

## Practices that pay off specifically here

- **Fail loudly on bad input.** Bioinformatics inputs are malformed constantly.
  Validate columns, chromosome naming (`chr1` vs `1`), genome build, and
  coordinate convention (BED is 0-based half-open; VCF and GTF are 1-based
  inclusive) at load time. An off-by-one in coordinates produces a peptide that
  looks plausible and is wrong.
- **Pin the reference.** Genome build and annotation version go in the config
  and in the methods. Changing GENCODE versions changes your peptide set.
- **Log the versions of every tool you shell out to**, into the output
  directory, at runtime. Your future self writing the methods section will need
  exactly this.
- **Set seeds** for anything stochastic, and record them.
- **Write the test for the transform that would silently corrupt everything** —
  coordinate conversion, peptide generation around a variant, HLA allele
  formatting. Those three account for most quiet catastrophes.

```python
def test_peptide_window_around_variant():
    """A 9-mer window must place the mutation at every position 1..9."""
    protein = "MKTAYIAKQRQISFVKSHFSRQ"
    peptides = mutant_peptides(protein, pos=10, mutant_aa="D", length=9)
    assert len(peptides) == 9
    assert all(len(p) == 9 for p in peptides)
    assert all("D" in p for p in peptides)
    # The mutation walks from the C-terminus to the N-terminus of the window.
    assert [p.index("D") for p in peptides] == list(range(8, -1, -1))
```

## Working with an AI assistant on research code

- **Ask for the test first**, then the implementation. It makes the assumptions
  explicit before they are buried in code.
- **Never accept a magic number.** A binding threshold of 500 nM, a TPM cut-off
  of 1.0, an FDR of 0.05 — each needs a stated reason in a comment.
- **Ask it to say what would falsify the analysis.** A model that cannot name a
  failure mode for its own code has not thought about it.
- **Check every claimed API.** Fabricated function names and plausible-looking
  parameters are the characteristic failure mode. Run it.
- **Keep generated code in version control from the first commit**, so you can
  see what it changed when a result shifts.

## Performance, only when it matters

Profile before optimizing (`cProfile`, `snakeviz`, `py-spy`). In this domain
the usual wins, in order: vectorize with numpy/polars instead of iterating rows;
avoid re-reading large references inside loops; use `pysam`/`pyranges` for
interval work instead of pandas joins; cache expensive predictor calls keyed on
(peptide, allele) — a real pipeline re-queries the same pairs many times.

Do not parallelize before you have profiled. Most slow bioinformatics scripts
are slow for one identifiable reason, and it is usually I/O in a loop.
