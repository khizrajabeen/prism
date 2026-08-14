"""The eval harness.

These tests exist because the harness is the thing that judges everything else,
so a bug in it is worse than a bug in what it measures: it either hides a real
regression or invents one. The first run of this harness did invent one — it
reported recall 0.00 on five fields that the audit simultaneously scored at
κ 0.88, because gold annotates those fields as `yes` while the extractor
returns descriptive tokens. Two tests below pin that fix in place, and two more
pin the related rule that a paper the corpus does not hold is skipped rather
than scored as a miss.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from neobrain import evaluate

yaml = pytest.importorskip("yaml")


METHODS = (
    "Female C57BL/6 mice were randomly assigned to groups of n = 10 per group. "
    "Tumour volume was measured by a blinded investigator. A power calculation "
    "determined the sample size. Groups were vehicle control and neoantigen "
    "peptide with poly-ICLC administered subcutaneously. Overall survival was "
    "compared by log-rank test. Procedures were approved by the institutional "
    "animal care and use committee."
)


def _gold_file(config, papers: list[dict]) -> None:
    directory = config.CONFIG_DIR / "gold"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test.yaml").write_text(
        yaml.safe_dump({"papers": papers}, sort_keys=False), encoding="utf-8")


def _seed(con, db, pid: str, title: str, methods: str) -> None:
    db.upsert_paper(con, {
        "id": pid, "source": "journal", "provider": "t", "title": title,
        "abstract": "", "authors": "Doe J", "journal": "J Test",
        "pub_date": "2026-01-01", "doi": "", "pmid": "", "pmcid": "",
        "url": "", "is_oa": 1, "score": 10, "matched": "", "buckets": "t"})
    con.execute(
        "INSERT INTO sections(paper_id, ord, heading, kind, text) "
        "VALUES (?,0,'Methods','methods',?)", (pid, methods))
    con.commit()


@pytest.fixture()
def gold_home(brain):
    """A brain whose gold set is only what the test writes — not the bootstrap."""
    con, config, db = brain
    importlib.reload(evaluate)
    # The packaged bootstrap is loaded as a fallback in normal use; a test that
    # measures its own annotations must not inherit twelve more.
    evaluate.PACKAGED_GOLD = config.CONFIG_DIR / "does-not-exist"
    yield con, config, db


# ------------------------------------------------------------------ metrics

def test_kappa_is_zero_when_agreement_is_only_what_chance_predicts():
    # Both raters say yes 50% of the time and agree on exactly half — the raw
    # agreement of 0.50 is worth nothing, and κ must say so.
    a = [True, True, False, False]
    b = [True, False, True, False]
    assert evaluate.cohens_kappa(a, b) == 0.0


def test_kappa_is_one_on_perfect_agreement_with_variation():
    a = b = [True, False, True, True, False]
    assert evaluate.cohens_kappa(a, b) == 1.0


def test_kappa_handles_a_rater_who_says_yes_to_everything():
    # No variance to correct for: raw agreement is 1.0 but tells us nothing.
    assert evaluate.cohens_kappa([True] * 5, [True] * 5) == 1.0
    assert evaluate.cohens_kappa([True] * 5, [False] * 5) == 0.0


def test_normalise_treats_no_and_not_reported_as_absence():
    for absent in (None, "no", "not reported", "none", "", "null"):
        assert evaluate._normalise(absent) == set()
    assert evaluate._normalise("yes") == {"yes"}
    assert evaluate._normalise(10) == {"10"}
    assert evaluate._normalise("vehicle, isotype") == {"vehicle", "isotype"}


# ------------------------------------------------------- the boolean-field bug

def test_boolean_fields_match_on_presence_not_on_the_literal_word_yes():
    """The regression that made the harness's first run wrong.

    Gold says `randomization: yes`; the extractor says "randomized". Comparing
    those as values scores a correct extraction as a miss.
    """
    gold, got = {"yes"}, {"randomized"}
    assert not evaluate._matches(gold, got)                 # as values: disagree
    assert evaluate._matches(gold, got, boolean=True)       # as presence: agree


def test_boolean_match_still_requires_the_extractor_to_have_found_something():
    assert not evaluate._matches({"yes"}, set(), boolean=True)
    assert not evaluate._matches(set(), {"randomized"}, boolean=True)


def test_extraction_recall_is_not_zero_on_reported_boolean_fields(gold_home):
    con, config, db = gold_home
    _gold_file(config, [{
        "id": "t1", "title": "A study", "source": "annotated", "text": METHODS,
        "fields": {"randomization": "yes", "blinding": "yes",
                   "power_calculation": "yes", "ethics_approval": "yes",
                   "sample_size": 10},
    }])

    result = evaluate.evaluate_extraction(con=con)
    assert result["n"] == 1
    for boolean_field in ("randomization", "blinding", "power_calculation",
                          "ethics_approval"):
        assert result["by_field"][boolean_field]["recall"] == 1.0, boolean_field
    assert result["overall"]["precision"] == 1.0


def test_absence_annotated_as_no_is_scored_as_correct_not_as_a_miss(gold_home):
    con, config, db = gold_home
    _gold_file(config, [{
        "id": "t2", "title": "Bare", "source": "annotated",
        "text": "Mice bearing B16F10 tumours were vaccinated.",
        "fields": {"randomization": "no", "blinding": "no",
                   "power_calculation": "no", "ethics_approval": "no"},
    }])

    result = evaluate.evaluate_extraction(con=con)
    assert result["absence_correct"] == 4
    assert result["absence_wrong"] == 0


# ------------------------------------------------ papers the corpus lacks

def test_an_annotation_for_a_paper_not_in_the_corpus_is_skipped_not_missed(gold_home):
    """Scoring it would measure the size of the library, not the extractor."""
    con, config, db = gold_home
    _gold_file(config, [
        {"id": "t3", "title": "Present", "source": "annotated", "text": METHODS,
         "fields": {"sample_size": 10}},
        {"id": "t4", "title": "Absent", "source": "annotated",
         "paper_id": "MED:99999999", "fields": {"sample_size": 42}},
    ])

    result = evaluate.evaluate_extraction(con=con)
    assert result["n"] == 1
    assert result["uningested"] == ["MED:99999999"]
    assert result["overall"]["recall"] == 1.0

    audit = evaluate.evaluate_audit(con=con)
    assert audit["n"] == 1


def test_retrieval_reports_not_measurable_rather_than_zero(gold_home):
    con, config, db = gold_home
    _gold_file(config, [{
        "id": "t5", "title": "Inline", "source": "annotated", "text": METHODS,
        "relevant_to": ["neoantigen vaccination in mice"],
        "fields": {"sample_size": 10},
    }])

    r = evaluate.evaluate_retrieval(con=con)
    assert r["queries"] == 0
    assert r["skipped_inline"] == 1
    assert "recall_at_k" not in r        # never a fabricated 0.00
    assert "not measurable" in r["note"].lower()


# ------------------------------------------------------- the ship gate

def test_recall_at_k_is_measured_when_the_paper_really_is_in_the_corpus(gold_home):
    """Phase 1's ship gate: a measured retrieval recall that actually exists."""
    con, config, db = gold_home
    from neobrain import embeddings

    _seed(con, db, "P:1", "Neoantigen vaccination in MC38 tumours", METHODS)
    embeddings.index_new(con)

    _gold_file(config, [{
        "id": "t6", "title": "Neoantigen vaccination in MC38 tumours",
        "source": "annotated", "paper_id": "P:1",
        "relevant_to": ["neoantigen vaccination MC38"],
        "fields": {"sample_size": 10},
    }])

    r = evaluate.evaluate_retrieval(con=con, k=10)
    assert r["queries"] == 1
    assert r["recall_at_k"] == 1.0
    assert r["missing_from_corpus"] == []


def test_the_report_labels_a_small_gold_set_as_indicative(gold_home):
    con, config, db = gold_home
    _gold_file(config, [{
        "id": "t7", "title": "A study", "source": "annotated", "text": METHODS,
        "fields": {"sample_size": 10},
    }])

    result = evaluate.run_all(con)
    assert result["extraction"]["defensible"] is False
    report = evaluate.format_report(result)
    assert "indicative only" in report
    assert "MEASURED PERFORMANCE" in report


def test_headline_is_short_enough_for_doctor(gold_home):
    con, config, db = gold_home
    _gold_file(config, [{
        "id": "t8", "title": "A study", "source": "annotated", "text": METHODS,
        "fields": {"sample_size": 10, "randomization": "yes"},
    }])

    lines = evaluate.headline(con)
    assert 1 <= len(lines) <= 4
    assert any("extraction" in line for line in lines)
    assert all(len(line) < 100 for line in lines)


# ------------------------------------------------------------ adding gold

def test_add_annotation_round_trips_and_overrides_by_id(gold_home):
    con, config, db = gold_home

    evaluate.add_annotation("MED:1", {"sample_size": 8}, title="First")
    evaluate.add_annotation("MED:2", {"sample_size": 9}, title="Second",
                            relevant_to=["a query"])
    gold = {g.id: g for g in evaluate.load_gold()}
    assert set(gold) == {"MED:1", "MED:2"}
    assert gold["MED:2"].relevant_to == ["a query"]
    assert gold["MED:1"].source == "annotated"

    # Re-annotating replaces rather than duplicates.
    evaluate.add_annotation("MED:1", {"sample_size": 12}, title="First, corrected")
    gold = {g.id: g for g in evaluate.load_gold()}
    assert len(gold) == 2
    assert gold["MED:1"].fields["sample_size"] == 12


def test_a_hand_annotation_wins_over_a_bootstrap_entry_with_the_same_id(brain):
    con, config, db = brain
    importlib.reload(evaluate)
    # The shipped bootstrap must never shadow your own reading of the paper.
    packaged = config.CONFIG_DIR / "packaged"
    packaged.mkdir(parents=True, exist_ok=True)
    (packaged / "bootstrap.yaml").write_text(
        yaml.safe_dump({"papers": [
            {"id": "shared", "title": "Bootstrap version", "source": "bootstrap",
             "fields": {"sample_size": 1}}]}), encoding="utf-8")
    evaluate.PACKAGED_GOLD = packaged

    _gold_file(config, [{"id": "shared", "title": "My reading",
                         "source": "annotated", "fields": {"sample_size": 99}}])

    gold = evaluate.load_gold()
    assert len(gold) == 1
    assert gold[0].fields["sample_size"] == 99


def test_bootstrap_gold_ships_and_loads():
    """A fresh clone must be able to run `neobrain eval` with no setup."""
    # Resolved from the repo rather than from the module global, which earlier
    # tests repoint at a temp directory.
    packaged = Path(evaluate.__file__).resolve().parent.parent / "config" / "gold"
    gold = evaluate.load_gold(packaged)
    assert len(gold) >= 10
    assert all(g.source == "bootstrap" for g in gold)
    # Absence is annotated, not merely omitted — that is what makes the
    # extractor's "not reported" a measurable claim rather than an empty cell.
    # YAML reads `no` as False, which _normalise treats as absence.
    assert any("randomization" in g.fields and not evaluate._normalise(
        g.fields["randomization"]) for g in gold)
