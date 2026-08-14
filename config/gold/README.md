# The gold set

This directory holds the annotated papers that `neobrain eval` measures against.
It is the only reason any number this tool prints about itself means anything.

    neobrain eval          # the report
    neobrain eval list     # what is annotated
    neobrain eval add ...  # annotate a paper you have read

## Files

| file | what it is |
| --- | --- |
| `bootstrap.yaml` | 12 hand-written methods passages whose ground truth is known by construction. Ships with the repo so `neobrain eval` runs on a fresh clone. |
| `annotated.yaml` | Written by `neobrain eval add`. Papers **you** have read. |

Both are loaded, and your annotation wins if an id appears in both. Passages
written to be extracted are easier than real ones, so bootstrap numbers are an
optimistic ceiling — the report says so, and it keeps saying so until the gold
set passes 20 papers.

## Annotating a paper

Read the methods section, then record what a careful reader says is in it:

    neobrain eval add MED:39012345 \
        --title "Personalized neoantigen vaccine in resected melanoma" \
        --sample-size 107 --randomization yes --blinding no \
        --power yes --controls "pembrolizumab alone" \
        --stats "Cox" --correction yes --ethics yes \
        --endpoint "recurrence-free survival" \
        --relevant-to "randomized trial personalized neoantigen vaccine"

The paper must already be in the corpus — annotating one that is not is
skipped rather than scored, because scoring it would measure the size of your
library rather than the quality of the extractor.

### Annotate absence explicitly

Write `no` where the paper genuinely does not report a field. Do not leave it
out. An omitted field is not measured at all; a `no` is measured, and it is the
only way to know whether "not reported" is a real finding or a pattern that
failed. That distinction is most of what separates this extractor from one that
guesses plausibly.

### `--relevant-to`

A query the paper should be retrieved for. This is what produces recall@k, so
phrase it the way you would actually type it, not the way the title reads.

## What the numbers mean

* **Extraction precision** should stay at 1.00. Pattern extraction cannot
  fabricate, so anything lower is a pattern matching the wrong span — a bug,
  not a tuning question. The report names the field.
* **Extraction recall** is where real work shows up. A field below 0.70 is
  listed as weak with the number of misses.
* **Cohen's κ** compares the automated methods audit to your checklist. Raw
  agreement is inflated on a checklist where most items are present, which is
  why it is reported alongside rather than instead.
* **recall@k** needs annotated papers that are actually in the corpus.
* **Calibration** is per-verdict-class precision for claim checks, and stays
  empty until you start overriding verdicts. It has not earned a number yet.

Headline figures also appear in `neobrain doctor`, so a regression in
extraction or retrieval shows up in the health check rather than only in a
report nobody runs.
