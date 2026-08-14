"""The translational/clinical layer: actionability tiers and trial screening.

Who this is for
---------------
The researcher who is asked "could this patient go on a neoantigen trial?", and
the translational scientist sitting in a molecular tumour board. Those people
need a different output from the bench scientist: not "what does the literature
say about KRAS G12D", but "for *this* profile, what is actionable, at what
level of evidence, and what is open".

Why ESCAT rather than a scale of my own
---------------------------------------
Molecular tumour boards already have a standard for this — ESMO's ESCAT scale,
and OncoKB's levels — and the evidence tiers predict outcome: patients matched
on ESCAT I/II alterations do measurably better than those matched on III/IV.
Inventing a private scale would make this system's output untranslatable into
the conversation it is meant to support. So the tiers here *are* ESCAT, and the
mapping is stated explicitly so a clinician can check it.

The hard boundary
-----------------
**This screens; it does not determine eligibility, and it does not recommend
treatment.** Eligibility is decided by a protocol document and a human being.
Every output of this module says so, because a screening aid that gets quoted
as an eligibility determination is a patient-safety problem, not a UX one.

The trials it matches against are the ones NeoBrain has swept — a subset of
ClinicalTrials.gov filtered by your interests, missing EU CTIS and other
registries entirely. It is a starting point for a search, not a search.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db, peptides

# ESMO Scale for Clinical Actionability of molecular Targets, as used by
# molecular tumour boards. Roman numerals are ESCAT's own.
ESCAT = {
    "I-A": "Same tumour type, prospective randomised trial — ready for routine use",
    "I-B": "Same tumour type, prospective non-randomised trial",
    "I-C": "Same tumour type, basket trial across tumours",
    "II-A": "Same tumour type, retrospective evidence of benefit",
    "II-B": "Same tumour type, prospective evidence of activity, survival benefit unproven",
    "III-A": "Benefit shown in a DIFFERENT tumour type with the same alteration",
    "III-B": "Same alteration, different-tumour evidence, weaker",
    "IV-A": "Preclinical evidence only",
    "IV-B": "Case reports / anecdotal",
    "V": "Evidence of antitumour activity but no clinically meaningful benefit",
    "X": "No evidence of actionability",
}

# What NeoBrain can say about the neoantigen-relevant space specifically.
# These are deliberately conservative defaults; a real MTB assigns the tier
# per patient with the full trial landscape in front of it.
_KNOWN_TARGETS = {
    "KRAS G12C": {"tier": "I-A", "note": "Directly druggable in NSCLC/CRC with approved "
                                         "inhibitors; also a shared neoantigen target."},
    "KRAS G12D": {"tier": "II-B", "note": "Active clinical development, both inhibitors and "
                                          "TCR-T/vaccine approaches. HLA-restricted for the "
                                          "immunotherapy route (notably HLA-C*08:02)."},
    "KRAS G12V": {"tier": "II-B", "note": "Shared neoantigen target; TCR-T programmes. "
                                          "HLA-restricted."},
    "TP53 R175H": {"tier": "III-A", "note": "Shared neoantigen target under investigation; "
                                            "HLA-A*02:01-restricted TCRs reported."},
    "BRAF V600E": {"tier": "I-A", "note": "Approved targeted therapy in several tumour types."},
    "B2M": {"tier": "X", "note": "Loss abolishes MHC-I presentation: an exclusion signal for "
                                 "vaccine and TCR approaches, and a reason to consider "
                                 "NK-engaging strategies."},
    "JAK1": {"tier": "X", "note": "Loss-of-function predicts checkpoint-blockade resistance."},
    "JAK2": {"tier": "X", "note": "Loss-of-function predicts checkpoint-blockade resistance."},
}

_MSI_MARKERS = {"MSI-H", "MSI", "MMR", "MLH1", "MSH2", "MSH6", "PMS2", "DMMR"}


@dataclass
class PatientProfile:
    """A de-identified molecular profile. No names, no dates of birth, no MRN."""
    tumour_type: str = ""
    variants: list[str] = field(default_factory=list)
    hla: list[str] = field(default_factory=list)
    tmb: float | None = None
    msi: str = ""
    prior_lines: int = 0
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "PatientProfile":
        return cls(
            tumour_type=(d.get("tumour_type") or d.get("tumor_type") or "").strip(),
            variants=[v.strip() for v in (d.get("variants") or []) if v.strip()],
            hla=[h.strip() for h in (d.get("hla") or []) if h.strip()],
            tmb=d.get("tmb"),
            msi=(d.get("msi") or "").strip(),
            prior_lines=int(d.get("prior_lines") or 0),
            notes=(d.get("notes") or "").strip(),
        )


def _normalise_variant(v: str) -> str:
    return re.sub(r"\s+", " ", v.strip().upper().replace("P.", ""))


def annotate(profile: PatientProfile) -> list[dict[str, Any]]:
    """Assign an ESCAT-style tier and a note to each alteration."""
    out = []
    for raw in profile.variants:
        v = _normalise_variant(raw)
        hit = None
        for known, meta in _KNOWN_TARGETS.items():
            if v == known.upper() or v.startswith(known.split()[0].upper()) and known.split()[-1].upper() in v:
                hit = (known, meta)
                break
        if hit:
            known, meta = hit
            out.append({"variant": raw, "matched": known, "escat": meta["tier"],
                        "escat_meaning": ESCAT[meta["tier"]], "note": meta["note"]})
        else:
            out.append({
                "variant": raw, "matched": None, "escat": "unassigned",
                "escat_meaning": "not in NeoBrain's small local table",
                "note": "NeoBrain does not maintain a clinical variant knowledge base. "
                        "Check OncoKB, CIViC, or your MTB's own resource for an "
                        "actionability call on this alteration.",
            })

    if profile.msi and profile.msi.upper().replace("-", "") in {m.replace("-", "") for m in _MSI_MARKERS}:
        out.append({
            "variant": profile.msi, "matched": "MSI-H / dMMR", "escat": "I-A",
            "escat_meaning": ESCAT["I-A"],
            "note": "High indel burden generates many frameshift neoantigens — the "
                    "mechanistic basis for checkpoint-blockade responsiveness, and a "
                    "favourable setting for neoantigen approaches.",
        })
    if profile.tmb is not None:
        tier = "I-C" if profile.tmb >= 10 else "X"
        out.append({
            "variant": f"TMB {profile.tmb}/Mb", "matched": "TMB", "escat": tier,
            "escat_meaning": ESCAT[tier],
            "note": "TMB predicts checkpoint response at the population level and poorly "
                    "in an individual patient. It is a weak reason to expect a "
                    "neoantigen therapy to work in one person.",
        })
    return out


def check_hla(profile: PatientProfile) -> list[dict[str, Any]]:
    """Validate the HLA typing, since several shared-neoantigen products are
    restricted to specific alleles and a serological type cannot answer that."""
    return [peptides.normalize_hla(a) for a in profile.hla]


# ------------------------------------------------------------ trial screening

_STATUS_OPEN = ("RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION",
                "AVAILABLE", "Recruiting", "Not yet recruiting")


def match_trials(
    con: sqlite3.Connection,
    profile: PatientProfile,
    *,
    limit: int = 25,
    open_only: bool = True,
) -> list[dict[str, Any]]:
    """Screen tracked trials against a profile. **A search aid, not eligibility.**

    Scoring is transparent and shallow on purpose: tumour-type match, variant
    mention, and neoantigen-relevance. It cannot read an eligibility section,
    so it will produce both false positives and false negatives, and the caller
    is told so.
    """
    rows = db.rows_to_dicts(con.execute("SELECT * FROM trials").fetchall())
    if not rows:
        return []

    tumour = profile.tumour_type.lower()
    variants = [_normalise_variant(v) for v in profile.variants]
    genes = {v.split()[0] for v in variants if v}

    scored = []
    for t in rows:
        if open_only and t.get("status") and t["status"] not in _STATUS_OPEN:
            continue
        blob = " ".join(str(t.get(f) or "") for f in
                        ("title", "conditions", "interventions", "summary")).lower()
        score, why = 0, []

        if tumour and any(word in blob for word in tumour.split() if len(word) > 3):
            score += 3
            why.append(f"condition mentions {profile.tumour_type}")
        for g in genes:
            if g and g.lower() in blob:
                score += 3
                why.append(f"mentions {g}")
        for v in variants:
            if v.lower() in blob:
                score += 4
                why.append(f"mentions {v}")
        if "neoantigen" in blob or "personalized" in blob or "personalised" in blob:
            score += 2
            why.append("neoantigen / personalised platform")
        if profile.msi and "microsatellite" in blob:
            score += 2
            why.append("MSI-relevant")

        if score <= 0:
            continue
        scored.append({
            "nct_id": t["nct_id"], "title": t["title"], "status": t["status"],
            "phase": t["phase"], "conditions": t["conditions"],
            "interventions": t["interventions"], "sponsor": t["sponsor"],
            "url": t["url"], "score": score, "why": why,
        })

    scored.sort(key=lambda x: -x["score"])
    return scored[:limit]


def screen(con: sqlite3.Connection, profile: PatientProfile) -> dict[str, Any]:
    """The full screening output, with its limits stated in the payload itself."""
    annotations = annotate(profile)
    hla = check_hla(profile)
    trials = match_trials(con, profile)
    n_trials = con.execute("SELECT COUNT(*) c FROM trials").fetchone()["c"]

    flags = []
    bad_hla = [h for h in hla if not h.get("valid")]
    if bad_hla:
        flags.append(
            f"{len(bad_hla)} HLA allele(s) are not at four-digit resolution. Several "
            f"shared-neoantigen products are restricted to a specific allele "
            f"(e.g. HLA-C*08:02 for some KRAS G12D approaches), and a serological "
            f"type cannot answer whether the patient qualifies.")
    if any("B2M" in (a["variant"] or "").upper() for a in annotations):
        flags.append(
            "B2M alteration present — MHC class I presentation may be abolished, which "
            "would be expected to disable vaccine and TCR-based approaches regardless "
            "of neoantigen load.")
    if any(g in " ".join(profile.variants).upper() for g in ("JAK1", "JAK2")):
        flags.append(
            "JAK1/2 alteration present — associated with checkpoint-blockade resistance; "
            "relevant because most neoantigen programmes combine with checkpoint blockade.")
    if not profile.hla:
        flags.append("No HLA typing supplied. Neoantigen approaches are HLA-restricted; "
                     "without typing this screen cannot address allele-specific products.")

    return {
        "profile": {
            "tumour_type": profile.tumour_type, "variants": profile.variants,
            "hla": profile.hla, "tmb": profile.tmb, "msi": profile.msi,
            "prior_lines": profile.prior_lines,
        },
        "actionability": annotations,
        "hla_check": hla,
        "flags": flags,
        "trials": trials,
        "corpus": {
            "trials_tracked": n_trials,
            "note": f"Screened against the {n_trials} trials NeoBrain has swept — a subset "
                    f"of ClinicalTrials.gov filtered by config/interests.yaml. EU CTIS, "
                    f"ISRCTN, ANZCTR and jRCT are not covered at all.",
        },
        "limits": [
            "THIS IS A SEARCH AID, NOT AN ELIGIBILITY DETERMINATION. Eligibility is "
            "decided against the protocol document by a qualified human.",
            "Matching is keyword-based over title, condition and intervention text. It "
            "cannot read inclusion/exclusion criteria, so it produces both false "
            "positives and false negatives.",
            "ESCAT tiers here come from a small hand-maintained table, not a clinical "
            "knowledge base. For an actionability call, use OncoKB or CIViC.",
            "Nothing here is a treatment recommendation.",
        ],
    }


def format_screen(result: dict[str, Any], width: int = 92) -> str:
    import textwrap

    L = ["MOLECULAR SCREEN — a search aid, not an eligibility determination", ""]
    p = result["profile"]
    L.append(f"  {p['tumour_type'] or 'tumour type not given'} · "
             f"{len(p['variants'])} variant(s) · {len(p['hla'])} HLA allele(s)"
             + (f" · TMB {p['tmb']}" if p["tmb"] is not None else "")
             + (f" · {p['msi']}" if p["msi"] else ""))
    L.append("")

    L.append("  ACTIONABILITY (ESCAT)")
    for a in result["actionability"]:
        L.append(f"    {a['escat']:<11} {a['variant']}")
        L.append(textwrap.fill(a["note"], width=width - 8,
                               initial_indent="        ", subsequent_indent="        "))
    L.append("")

    if result["flags"]:
        L.append("  FLAGS")
        for f in result["flags"]:
            L.append(textwrap.fill("• " + f, width=width - 4,
                                   initial_indent="    ", subsequent_indent="      "))
        L.append("")

    L.append(f"  CANDIDATE TRIALS ({len(result['trials'])} of "
             f"{result['corpus']['trials_tracked']} tracked)")
    if not result["trials"]:
        L.append("    None matched. " + result["corpus"]["note"])
    for t in result["trials"][:10]:
        L.append(f"    [{t['score']:>2}] {t['nct_id']}  {t['status']}  {t['phase'] or 'phase n/a'}")
        L.append(textwrap.fill(t["title"], width=width - 10,
                               initial_indent="         ", subsequent_indent="         "))
        L.append(f"         why: {', '.join(t['why'])}")
        L.append(f"         {t['url']}")
    L.append("")
    L.append("  LIMITS")
    for lim in result["limits"]:
        L.append(textwrap.fill("• " + lim, width=width - 4,
                               initial_indent="    ", subsequent_indent="      "))
    return "\n".join(L)
