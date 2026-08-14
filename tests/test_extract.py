"""Structured extraction, the methods audit, and quality-weighted evidence.

The property that matters most here is the one an LLM extractor cannot offer:
every value carries the sentence it came from, and a missing value is reported
as "not reported" rather than as an empty cell.
"""

import pytest

from neobrain import extract, science

WELL_REPORTED = (
    "Female C57BL/6 mice were randomly assigned to groups of n = 10 per group "
    "once tumours reached 60 mm3. Tumour volume was measured twice weekly by a "
    "blinded investigator. A power calculation determined the sample size for "
    "80% power. Groups were vehicle control, adjuvant-alone, irrelevant peptide, "
    "and neoantigen plus poly-ICLC administered subcutaneously. Overall survival "
    "was compared by log-rank test. All procedures were approved by the "
    "institutional animal care and use committee. Peptides were 25-mers "
    "predicted with NetMHCpan-4.1 at a percentile rank < 2%. HLA-A*02:01 was "
    "used. Vaccination significantly increased survival (p < 0.001)."
)
BARE = ("Mice bearing B16F10 tumours were vaccinated. Tumour growth was "
        "measured. Vaccination significantly reduced tumour volume.")


def _seed(con, db, config, pid, title, methods):
    db.upsert_paper(con, {
        "id": pid, "source": "journal", "provider": "t", "title": title,
        "abstract": "", "authors": "Doe J", "journal": "J Test",
        "pub_date": "2026-01-01", "doi": "", "pmid": "", "pmcid": "",
        "url": "", "is_oa": 1, "score": 10, "matched": "", "buckets": "t"})
    con.execute(
        "INSERT INTO sections(paper_id, ord, heading, kind, text) VALUES (?,0,'Methods','methods',?)",
        (pid, methods))
    con.commit()


# ------------------------------------------------------------- extraction

def test_extracts_the_core_fields_with_provenance(brain):
    con, config, db = brain
    _seed(con, db, config, "P:1", "A study", WELL_REPORTED)

    ex = extract.extract_paper(con, "P:1")
    assert ex["sample_size"][0].value == "10"
    assert ex["randomization"][0].value == "randomized"
    assert ex["blinding"][0].value == "blinded"
    assert ex["statistical_test"][0].value == "log-rank"
    assert ex["predictor"][0].value == "NetMHCpan-4.1"
    assert ex["sex"][0].value == "female"
    assert "C57BL/6" in ex["model_system"][0].value
    assert "HLA-A*02:01" in ex["hla_alleles"][0].value

    # The property that distinguishes this from an LLM table: every value
    # carries the sentence it came from, and that sentence is really in the text.
    for field_items in ex.values():
        for item in field_items:
            assert item.evidence, f"{item.field} has no provenance"
            assert item.evidence[:40].strip("…") in WELL_REPORTED or item.section


def test_extraction_cannot_invent_a_value(brain):
    con, config, db = brain
    _seed(con, db, config, "P:2", "Bare study", BARE)
    ex = extract.extract_paper(con, "P:2")
    assert "sample_size" not in ex
    assert "randomization" not in ex
    assert "power_calculation" not in ex
    # It still finds what is genuinely there.
    assert ex["effect_direction"][0].value == "decrease"
    assert "B16F10" in ex["model_system"][0].value


def test_multi_value_fields_collect_all_of_them(brain):
    con, config, db = brain
    _seed(con, db, config, "P:3", "Study", WELL_REPORTED)
    ex = extract.extract_paper(con, "P:3")
    controls = {i.value for i in ex["controls"]}
    assert {"adjuvant-alone arm", "irrelevant peptide", "vehicle control"} <= controls


def test_extraction_falls_back_to_the_abstract(brain):
    con, config, db = brain
    db.upsert_paper(con, {
        "id": "P:4", "source": "journal", "provider": "t", "title": "Abstract only",
        "abstract": "Mice were randomly assigned to n = 8 per group.",
        "authors": "", "journal": "", "pub_date": "", "doi": "", "pmid": "",
        "pmcid": "", "url": "", "is_oa": 1, "score": 1, "matched": "", "buckets": "t"})
    con.commit()
    ex = extract.extract_paper(con, "P:4")
    assert ex["sample_size"][0].value == "8"
    assert ex["sample_size"][0].section == "abstract"


# ----------------------------------------------------------------- matrix

def test_matrix_marks_absence_explicitly(brain):
    con, config, db = brain
    _seed(con, db, config, "P:good", "Well reported", WELL_REPORTED)
    _seed(con, db, config, "P:bare", "Bare", BARE)
    for pid in ("P:good", "P:bare"):
        extract.store(con, pid, extract.extract_paper(con, pid))

    m = extract.matrix(con, ["P:good", "P:bare"], fields=["sample_size", "blinding"])
    good = next(r for r in m["rows"] if r["paper_id"] == "P:good")
    bare = next(r for r in m["rows"] if r["paper_id"] == "P:bare")

    assert good["cells"]["sample_size"]["reported"] is True
    assert good["cells"]["sample_size"]["evidence"]
    # Absence is a value, not a blank — this is the whole point.
    assert bare["cells"]["sample_size"]["value"] == extract.NOT_REPORTED
    assert bare["cells"]["sample_size"]["reported"] is False
    assert m["gaps"]["sample_size"] == 1
    assert "1/2" in m["gap_summary"]["sample_size"]


def test_matrix_of_nothing_is_not_an_error(brain):
    con, config, db = brain
    assert extract.matrix(con, [])["rows"] == []


# ------------------------------------------------------------------ audit

def test_audit_distinguishes_well_and_badly_reported_papers(brain):
    con, config, db = brain
    _seed(con, db, config, "P:good", "Well reported", WELL_REPORTED)
    _seed(con, db, config, "P:bare", "Bare", BARE)

    good = extract.audit(con, "P:good")
    bare = extract.audit(con, "P:bare")
    assert good["reported"] == good["total"] == 7
    assert bare["reported"] == 0
    assert good["completeness"] > bare["completeness"]

    # Every satisfied item shows the sentence that satisfied it.
    for item in good["items"]:
        assert item["present"] and item["evidence"]
        assert item["why_it_matters"]


def test_audit_says_when_it_only_saw_the_abstract(brain):
    con, config, db = brain
    db.upsert_paper(con, {
        "id": "P:abs", "source": "journal", "provider": "t", "title": "t",
        "abstract": "Some abstract text about vaccination.", "authors": "",
        "journal": "", "pub_date": "", "doi": "", "pmid": "", "pmcid": "",
        "url": "", "is_oa": 0, "score": 1, "matched": "", "buckets": "t"})
    con.commit()
    a = extract.audit(con, "P:abs")
    assert a["basis"] == "abstract only"
    assert "understates" in a["caveat"]


def test_audit_is_a_checklist_not_a_score(brain):
    """A single score invites ranking papers by reporting, which is wrong."""
    con, config, db = brain
    _seed(con, db, config, "P:1", "t", WELL_REPORTED)
    a = extract.audit(con, "P:1")
    assert isinstance(a["items"], list) and len(a["items"]) == 7
    assert "score" not in a


def test_corpus_audit_finds_the_weakest_reporting(brain):
    con, config, db = brain
    _seed(con, db, config, "P:1", "good", WELL_REPORTED)
    _seed(con, db, config, "P:2", "bare", BARE)
    _seed(con, db, config, "P:3", "bare2", BARE)

    a = extract.audit_corpus(con)
    assert a["papers"] == 3
    assert a["by_item"]["sample_size"]["reported_by"] == 1
    assert a["weakest"]
    assert all("why_it_matters" in w for w in a["weakest"])


# ------------------------------------------------- quality-weighted evidence

def test_weight_combines_design_and_reporting(brain):
    con, config, db = brain
    from neobrain import evidence

    _seed(con, db, config, "P:good", "Well reported", WELL_REPORTED)
    _seed(con, db, config, "P:bare", "Bare", BARE)
    evidence.grade_corpus(con)

    good = extract.evidence_weight(con, "P:good")
    bare = extract.evidence_weight(con, "P:bare")
    assert good["weight"] > bare["weight"]
    assert good["reporting_completeness"] == 1.0
    assert bare["reporting_completeness"] == 0.0
    # Both components are shown, so it is clear which is doing the work.
    assert "tier" in good["explanation"] and "reporting" in good["explanation"]
    # Never zero — a weak paper still counts for something.
    assert bare["weight"] > 0


def test_weighted_support_beats_counting_citations(brain):
    """Three badly-reported papers must not outweigh one well-reported one."""
    con, config, db = brain
    from neobrain import evidence

    _seed(con, db, config, "P:good", "Well reported", WELL_REPORTED)
    for i in range(3):
        _seed(con, db, config, f"P:bare{i}", "Bare", BARE)
    evidence.grade_corpus(con)

    one_good = extract.weigh_support(con, ["P:good"])
    three_bad = extract.weigh_support(con, [f"P:bare{i}" for i in range(3)])
    assert one_good["per_paper"][0]["weight"] > three_bad["per_paper"][0]["weight"]
    assert one_good["verdict"] and three_bad["verdict"]


def test_no_sources_is_reported_as_such(brain):
    con, config, db = brain
    assert extract.weigh_support(con, [])["n"] == 0


# ------------------------------------------------------- evidence routing

def test_new_papers_route_to_matching_hypotheses(brain):
    con, config, db = brain
    pid = science.create_project(con, "proj", "q")
    hid = science.add_hypothesis(
        con, "Class II epitopes improve durability of MC38 vaccine responses",
        project_id=pid, falsifier="No difference in rechallenge survival")

    db.upsert_paper(con, {
        "id": "P:match", "source": "journal", "provider": "t",
        "title": "MHC class II epitopes and MC38 neoantigen vaccination",
        "abstract": "Adding MHC class II epitopes to an MC38 vaccine improved responses.",
        "authors": "", "journal": "", "pub_date": "", "doi": "", "pmid": "",
        "pmcid": "", "url": "", "is_oa": 1, "score": 9, "matched": "", "buckets": "t"})
    db.upsert_paper(con, {
        "id": "P:noise", "source": "journal", "provider": "t",
        "title": "Lipid nanoparticle cold chain logistics",
        "abstract": "Storage and shipping of LNP formulations.",
        "authors": "", "journal": "", "pub_date": "", "doi": "", "pmid": "",
        "pmcid": "", "url": "", "is_oa": 1, "score": 3, "matched": "", "buckets": "t"})
    con.commit()

    leads = science.route_evidence(con, ["P:match", "P:noise"])
    assert len(leads) == 1
    assert leads[0]["hypothesis_id"] == hid
    assert leads[0]["paper_id"] == "P:match"
    assert "MC38" in leads[0]["shared"]

    # Idempotent — re-running does not duplicate the link.
    assert science.route_evidence(con, ["P:match", "P:noise"]) == []
    assert len(science.open_leads(con)) == 1


def test_routing_needs_open_hypotheses(brain):
    con, config, db = brain
    assert science.route_evidence(con, []) == []


# ------------------------------------------------------ gazetteer regression

def test_mhc_class_i_and_ii_are_distinct_entities():
    """They were once both aliased away, so a class II hypothesis and a class II
    paper shared no entity at all."""
    from neobrain import graph

    one = {n for _, n in graph.extract("restricted by MHC class I")}
    two = {n for _, n in graph.extract("a class II epitope")}
    assert "MHC class I" in one
    assert "MHC class II" in two
    assert one != two
