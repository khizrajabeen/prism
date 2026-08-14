"""Peptide and construct utilities.

Zero dependencies, and everything here computes a real answer rather than
calling out to a predictor. These are the operations that sit *around* the big
models — the ones people re-implement badly in a notebook every time, where an
off-by-one silently produces a peptide that does not exist.

The junction analysis in particular has no convenient standalone tool: it is
buried inside pVACvector. If you are designing a multi-epitope construct by
hand, or checking one a collaborator sent you, this tells you what novel
peptides your linkers just created.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

AMINO_ACIDS = set("ACDEFGHIKLMNPQRSTVWY")

# Kyte-Doolittle hydropathy. Hydrophobicity at TCR-facing positions is one of
# the few features that reliably adds signal over binding affinity.
KD_HYDROPATHY = {
    "A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5, "Q": -3.5, "E": -3.5,
    "G": -0.4, "H": -3.2, "I": 4.5, "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8,
    "P": -1.6, "S": -0.8, "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2,
}

# Average residue masses (Da), for a sanity check on synthesis orders.
_MASS = {
    "A": 71.08, "R": 156.19, "N": 114.10, "D": 115.09, "C": 103.14, "Q": 128.13,
    "E": 129.12, "G": 57.05, "H": 137.14, "I": 113.16, "L": 113.16, "K": 128.17,
    "M": 131.19, "F": 147.18, "P": 97.12, "S": 87.08, "T": 101.10, "W": 186.21,
    "Y": 163.18, "V": 99.13,
}

CLASS_I_LENGTHS = (8, 9, 10, 11)
CLASS_II_LENGTHS = (13, 15, 18, 21, 25)

# Common linkers in string-of-beads constructs, with what they are for.
LINKERS = {
    "AAY": "proteasomal cleavage-favourable, common in class I constructs",
    "GPGPG": "class II spacer, discourages junctional class II epitopes",
    "KK": "cathepsin cleavage site, class II",
    "RVKR": "furin cleavage site",
    "GGGGS": "flexible glycine-serine spacer (structural, not cleavage-directed)",
    "": "no linker — direct fusion, highest junctional risk",
}


class SequenceError(ValueError):
    """Raised on input that is not a protein sequence."""


def clean_sequence(seq: str) -> str:
    """Uppercase, strip whitespace, and reject anything that is not protein."""
    s = re.sub(r"[\s\-\*]", "", (seq or "").upper())
    if not s:
        raise SequenceError("empty sequence")
    bad = sorted(set(s) - AMINO_ACIDS)
    if bad:
        raise SequenceError(
            f"not a protein sequence — unexpected residues: {', '.join(bad)}. "
            f"(If this is DNA, translate it first; if it uses X for unknown, "
            f"resolve or trim those positions.)"
        )
    return s


def molecular_weight(seq: str) -> float:
    """Approximate average MW in Da, including the terminal water."""
    s = clean_sequence(seq)
    return round(sum(_MASS[a] for a in s) + 18.02, 2)


def hydrophobicity(seq: str) -> float:
    """Mean Kyte-Doolittle hydropathy (GRAVY)."""
    s = clean_sequence(seq)
    return round(sum(KD_HYDROPATHY[a] for a in s) / len(s), 3)


def solubility_warning(seq: str) -> str | None:
    """Flag peptides likely to give you trouble in the well, before you order.

    Not a prediction — a checklist. Aggregation and DMSO problems are the most
    common reason a peptide screen produces uninterpretable wells, and they are
    visible from the sequence.
    """
    s = clean_sequence(seq)
    problems = []
    gravy = hydrophobicity(s)
    if gravy > 1.0:
        problems.append(f"strongly hydrophobic (GRAVY {gravy}) — will likely need DMSO")
    if len(re.findall(r"[CWM]", s)) >= 2:
        problems.append("multiple Cys/Trp/Met — oxidation and disulfide scrambling risk")
    if s.count("C") >= 2:
        problems.append("2+ cysteines — consider ordering with a reduced/alkylated spec")
    if re.search(r"([VILFWY])\1{2,}", s):
        problems.append("hydrophobic run of 3+ — aggregation risk")
    if len(s) >= 20 and gravy > 0.5:
        problems.append("long and hydrophobic — long peptides aggregate more")
    return "; ".join(problems) or None


# --------------------------------------------------------------- neoepitopes

@dataclass
class PeptideWindow:
    peptide: str
    length: int
    mutation_position: int   # 1-based position of the mutated residue in the peptide
    wildtype: str            # the corresponding wild-type peptide
    is_anchor: bool          # mutation sits at a canonical anchor position

    def to_dict(self) -> dict:
        return {
            "peptide": self.peptide, "length": self.length,
            "mutation_position": self.mutation_position, "wildtype": self.wildtype,
            "is_anchor": self.is_anchor,
            "changes_binding_or_recognition":
                "anchor — changes MHC binding, may create presentation that did not exist"
                if self.is_anchor else
                "TCR-facing — binding largely unchanged, alters what the TCR sees",
        }


def mutant_windows(
    protein: str,
    position: int,
    mutant_aa: str,
    *,
    lengths: Iterable[int] = CLASS_I_LENGTHS,
    wildtype_aa: str | None = None,
) -> list[PeptideWindow]:
    """Every peptide of the given lengths containing a substituted residue.

    ``position`` is **1-based**, matching how variants are written everywhere in
    biology (p.V600E) and unlike Python indexing. This is the single most common
    off-by-one in neoantigen code, so it is asserted rather than assumed.

    Each window is returned with its wild-type counterpart, because a mutant
    peptide without its WT control cannot support a neoantigen claim.
    """
    seq = clean_sequence(protein)
    mutant_aa = (mutant_aa or "").upper().strip()
    if mutant_aa not in AMINO_ACIDS:
        raise SequenceError(f"{mutant_aa!r} is not an amino acid")
    if not 1 <= position <= len(seq):
        raise SequenceError(
            f"position {position} is outside the sequence (length {len(seq)}); "
            f"positions are 1-based"
        )

    idx = position - 1
    if wildtype_aa and seq[idx] != wildtype_aa.upper():
        raise SequenceError(
            f"position {position} is {seq[idx]}, not {wildtype_aa.upper()} — "
            f"the sequence and the variant annotation disagree, which usually "
            f"means a transcript/isoform mismatch. Resolve that before predicting."
        )

    mutated = seq[:idx] + mutant_aa + seq[idx + 1:]
    windows: list[PeptideWindow] = []

    for length in lengths:
        for start in range(max(0, idx - length + 1), min(idx + 1, len(seq) - length + 1)):
            end = start + length
            if end > len(seq):
                continue
            pep = mutated[start:end]
            wt = seq[start:end]
            pos_in_peptide = idx - start + 1
            windows.append(PeptideWindow(
                peptide=pep,
                length=length,
                mutation_position=pos_in_peptide,
                wildtype=wt,
                is_anchor=_is_anchor(pos_in_peptide, length),
            ))
    return windows


def _is_anchor(pos: int, length: int) -> bool:
    """Canonical class I anchors: P2 and the C-terminus.

    A generalisation — real anchor positions are allele-specific, and for a
    definitive answer you check the allele's motif. Good enough to sort
    candidates into "changes binding" and "changes recognition".
    """
    return pos == 2 or pos == length


def frameshift_peptides(
    wildtype_protein: str,
    mutant_protein: str,
    *,
    lengths: Iterable[int] = CLASS_I_LENGTHS,
) -> list[str]:
    """Novel peptides from the divergent tail of a frameshift.

    A frameshift produces a stretch of entirely novel sequence rather than a
    single substitution, which is why frameshifts yield many candidates per
    event and are disproportionately immunogenic. Everything downstream of the
    first difference is non-self, so every window overlapping it is a candidate.
    """
    wt = clean_sequence(wildtype_protein)
    mut = clean_sequence(mutant_protein)

    divergence = next((i for i, (a, b) in enumerate(zip(wt, mut)) if a != b), min(len(wt), len(mut)))
    novel_from = divergence

    out: list[str] = []
    for length in lengths:
        for start in range(max(0, novel_from - length + 1), len(mut) - length + 1):
            end = start + length
            if end <= novel_from:
                continue  # entirely wild-type window
            out.append(mut[start:end])
    return sorted(set(out))


# --------------------------------------------------------- construct design

@dataclass
class Junction:
    left_epitope: str
    right_epitope: str
    linker: str
    novel_peptides: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "left": self.left_epitope, "right": self.right_epitope,
            "linker": self.linker, "n_novel": len(self.novel_peptides),
            "novel_peptides": self.novel_peptides,
        }


def build_construct(epitopes: list[str], linker: str = "AAY") -> str:
    """Assemble a string-of-beads construct sequence."""
    clean = [clean_sequence(e) for e in epitopes if e and e.strip()]
    if not clean:
        raise SequenceError("no epitopes given")
    lk = clean_sequence(linker) if linker else ""
    return lk.join(clean)


def analyse_junctions(
    epitopes: list[str],
    linker: str = "AAY",
    *,
    lengths: Iterable[int] = (8, 9, 10, 11),
) -> dict:
    """Enumerate the novel peptides created at every junction of a construct.

    **The point.** When you string epitopes together, the sequence spanning each
    junction can itself form a peptide that binds MHC — a junctional neoepitope
    present in the vaccine and in no tumor cell. It diverts response to a target
    nothing displays.

    This returns every junction-spanning window that is not contained within one
    of the original epitopes. It does not predict which of them bind — feed the
    list to NetMHCpan or MHCflurry for that. But you cannot check what you have
    not enumerated, and this step is skipped far more often than it should be.
    """
    clean = [clean_sequence(e) for e in epitopes if e and e.strip()]
    if len(clean) < 2:
        raise SequenceError("a construct needs at least two epitopes")
    lk = clean_sequence(linker) if linker else ""

    construct = lk.join(clean)
    originals = set(clean)

    # Where each epitope sits in the assembled construct.
    starts: list[int] = []
    cursor = 0
    for epitope in clean:
        starts.append(cursor)
        cursor += len(epitope) + len(lk)

    # A window is junctional if it is not a substring of any original epitope.
    # That single rule covers both cases: windows containing linker residues,
    # and windows spanning two epitopes directly when there is no linker.
    junctions: list[Junction] = []
    for i in range(len(clean) - 1):
        left, right = clean[i], clean[i + 1]
        boundary_start = starts[i] + len(left)        # first linker residue
        boundary_end = starts[i + 1]                  # first residue of `right`

        novel: set[str] = set()
        for length in lengths:
            first = max(0, boundary_start - length + 1)
            last = min(boundary_end, len(construct) - length)
            for start in range(first, last + 1):
                window = construct[start : start + length]
                if len(window) < length:
                    continue
                if any(window in original for original in originals):
                    continue
                novel.add(window)

        junctions.append(Junction(left, right, lk, sorted(novel)))

    total = sum(len(j.novel_peptides) for j in junctions)
    return {
        "construct": construct,
        "length": len(construct),
        "n_epitopes": len(clean),
        "linker": lk or "(none)",
        "linker_note": LINKERS.get(lk, "custom linker"),
        "junctions": [j.to_dict() for j in junctions],
        "total_novel_peptides": total,
        "verdict": (
            f"{total} junction-spanning peptides that exist in the construct and in no "
            f"tumor cell. Screen them against the patient's HLA — any that bind are "
            f"response diverted from real targets. Reorder the epitopes or change the "
            f"linker to reduce them (pVACvector automates this as a shortest-path problem)."
        ),
    }


# ------------------------------------------------------------------ HLA

_HLA_RE = re.compile(
    r"^HLA-(?P<gene>[A-Z]{1,4}\d?)\*(?P<field1>\d{2,3}):(?P<field2>\d{2,3})"
    r"(?::(?P<field3>\d{2,3}))?(?::(?P<field4>\d{2,3}))?(?P<suffix>[NLSCAQ])?$"
)
_HLA_SHORT = re.compile(r"^HLA-(?P<gene>[A-Z]{1,4}\d?)(?P<num>\d{1,2})$")

CLASS_I_GENES = {"A", "B", "C", "E", "F", "G"}
CLASS_II_GENES = {"DRB1", "DRB3", "DRB4", "DRB5", "DQA1", "DQB1", "DPA1", "DPB1", "DRA"}


def normalize_hla(allele: str) -> dict:
    """Validate and normalize an HLA allele name.

    Silently mis-formatted alleles are a real source of wasted prediction runs:
    tools variously accept ``HLA-A*02:01``, ``HLA-A02:01`` and ``A*0201``, and
    some will happily run on a name they interpreted differently from you.
    """
    raw = (allele or "").strip().upper().replace(" ", "")
    if not raw:
        raise SequenceError("empty allele")

    if not raw.startswith("HLA-"):
        raw = "HLA-" + raw
    # A*0201 -> A*02:01
    m_legacy = re.match(r"^HLA-([A-Z]{1,4}\d?)\*(\d{4})$", raw)
    if m_legacy:
        raw = f"HLA-{m_legacy.group(1)}*{m_legacy.group(2)[:2]}:{m_legacy.group(2)[2:]}"
    # HLA-A02:01 -> HLA-A*02:01
    m_nostar = re.match(r"^HLA-([A-Z]{1,4}\d?)(\d{2,3}):(\d{2,3})$", raw)
    if m_nostar:
        raw = f"HLA-{m_nostar.group(1)}*{m_nostar.group(2)}:{m_nostar.group(3)}"

    m = _HLA_RE.match(raw)
    if m:
        gene = m.group("gene")
        normalized = f"HLA-{gene}*{m.group('field1')}:{m.group('field2')}"
        return {
            "input": allele,
            "normalized": normalized,
            "gene": gene,
            "mhc_class": "I" if gene in CLASS_I_GENES else ("II" if gene in CLASS_II_GENES else "?"),
            "resolution": "four-digit (protein-level) — the minimum for prediction",
            "valid": True,
            "suffix": m.group("suffix") or "",
            "note": ("Null allele (N suffix) — not expressed, cannot present anything."
                     if m.group("suffix") == "N" else ""),
        }

    m_short = _HLA_SHORT.match(raw)
    if m_short:
        gene = m_short.group("gene")
        return {
            "input": allele,
            "normalized": None,
            "gene": gene,
            "mhc_class": "I" if gene in CLASS_I_GENES else ("II" if gene in CLASS_II_GENES else "?"),
            "resolution": "serological / two-digit",
            "valid": False,
            "note": (
                f"'{allele}' is serological-level (e.g. HLA-A2), which is not "
                f"sufficient for prediction: A*02:01 and A*02:06 have measurably "
                f"different binding motifs. Re-type to four-digit resolution."
            ),
        }

    return {
        "input": allele, "normalized": None, "valid": False,
        "note": f"'{allele}' is not a recognisable HLA allele name. "
                f"Expected e.g. HLA-A*02:01 or HLA-DRB1*15:01.",
    }


def summarize_peptide(seq: str) -> dict:
    """Everything computable about one peptide, without a model."""
    s = clean_sequence(seq)
    length = len(s)
    if length in CLASS_I_LENGTHS:
        fit = "class I length range (8-11)"
    elif 13 <= length <= 25:
        fit = "class II length range (13-25)"
    elif length < 8:
        fit = "too short for MHC presentation"
    else:
        fit = "between the class I and class II ranges — unusual, check the design"

    return {
        "peptide": s,
        "length": length,
        "mhc_fit": fit,
        "molecular_weight_da": molecular_weight(s),
        "gravy": hydrophobicity(s),
        "anchor_residues": {"P2": s[1] if length > 1 else None, "C_term": s[-1]},
        "synthesis_warning": solubility_warning(s),
        "composition": {a: s.count(a) for a in sorted(set(s))},
    }
