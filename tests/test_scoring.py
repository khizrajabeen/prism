"""Scoring is what decides whether you ever see a paper, so it gets tested."""

from neobrain import scoring

BOOSTS = {"neoantigen": 5, "MC38": 3, "MHC class II": 3}
PENALTIES = {"editorial": -5, "case report": -3}


def test_title_hits_count_double():
    in_title, _ = scoring.score_record("A neoantigen study", "", BOOSTS, PENALTIES)
    in_abstract, _ = scoring.score_record("A study", "About neoantigen biology", BOOSTS, PENALTIES)
    assert in_title > in_abstract


def test_penalties_apply():
    plain, _ = scoring.score_record("MC38 vaccination", "x" * 400, BOOSTS, PENALTIES)
    penalized, _ = scoring.score_record(
        "MC38 vaccination", "x" * 400 + " editorial", BOOSTS, PENALTIES
    )
    assert penalized < plain


def test_matched_terms_are_reported():
    _, matched = scoring.score_record(
        "Neoantigen prediction in MC38", "MHC class II epitopes", BOOSTS, PENALTIES
    )
    assert set(matched) == {"neoantigen", "MC38", "MHC class II"}


def test_stub_records_are_punished():
    """A metadata stub with no abstract should not outrank a real paper."""
    stub, _ = scoring.score_record("Neoantigen", "", BOOSTS, PENALTIES)
    full, _ = scoring.score_record("Neoantigen", "x" * 400, BOOSTS, PENALTIES)
    assert full > stub


def test_structural_signals_add():
    plain, _ = scoring.score_record("A vaccine study", "x" * 400, BOOSTS, PENALTIES)
    trial, _ = scoring.score_record(
        "A vaccine study", "This randomized phase 2 trial " + "x" * 400, BOOSTS, PENALTIES
    )
    assert trial > plain


def test_off_target_only_penalized_without_neoantigen():
    """'vaccine' hits about COVID are noise — unless they are about neoantigens."""
    covid, _ = scoring.score_record("A vaccine for SARS-CoV-2", "x" * 400, BOOSTS, PENALTIES)
    onco, _ = scoring.score_record(
        "SARS-CoV-2 and neoantigen cross-reactivity", "x" * 400, BOOSTS, PENALTIES
    )
    assert onco > covid


def test_retracted_is_buried():
    normal, _ = scoring.score_record("Neoantigen study", "x" * 400, BOOSTS, PENALTIES,
                                     pub_type="Journal Article")
    retracted, _ = scoring.score_record("Neoantigen study", "x" * 400, BOOSTS, PENALTIES,
                                        pub_type="Retracted Publication")
    assert retracted < normal - 15


def test_explain_mentions_threshold():
    text = scoring.explain(9, ["neoantigen"], 6)
    assert "surfaced" in text and "9" in text
