"""Model registry, peptide calculations, and the dashboard API.

The peptide tests matter most: these are the calculations where a silent
off-by-one produces a peptide that does not exist, and you would not notice
until a synthesis order came back.
"""

import json

import pytest

from neobrain import models, peptides
from neobrain.web.server import Api

# A real fragment of KRAS — position 12 is the G of the G12D hotspot.
KRAS = "MTEYKLVVVGAGGVGKSALTIQLIQNHFVDEYDPTIEDSYRKQVVIDGETCLLDILDTAGQEEYSAMRDQYMRT"


# ------------------------------------------------------------ model registry

def test_registry_loads_and_every_model_has_a_caveat():
    all_models = models.all_models()
    assert len(all_models) >= 15
    missing = [m.id for m in all_models if not m.caveat]
    assert not missing, f"models without a caveat: {missing}"


def test_availability_detection_is_real():
    """`available` must reflect this machine, not the YAML."""
    fake = models.Model(id="x", name="X", task="t", check="python:definitely_not_installed_xyz")
    assert fake.available is False
    real = models.Model(id="y", name="Y", task="t", check="python:json")
    assert real.available is True
    assert models.check_available("cli:definitely-not-a-binary-xyz") is False
    assert models.check_available("web") is False


def test_recommend_prefers_runnable_and_open():
    rec = models.recommend("structure")
    assert rec["recommended"]["name"] == "Boltz-2"      # MIT, open weights
    assert "AlphaFold" in json.dumps(rec["alternatives"])
    assert rec["guidance"]


def test_recommend_rejects_an_unknown_task():
    rec = models.recommend("astrology")
    assert "error" in rec and rec["known_tasks"]


def test_search_finds_by_task_and_name():
    assert any(m.id == "mhcflurry" for m in models.find("MHCflurry"))
    assert all(m.task == "structure" for m in models.for_task("structure"))


# ----------------------------------------------------------------- peptides

def test_mutant_windows_place_the_mutation_at_every_position():
    ws = peptides.mutant_windows(KRAS, 12, "D", lengths=(9,), wildtype_aa="G")
    assert len(ws) == 9
    assert sorted(w.mutation_position for w in ws) == list(range(1, 10))
    assert all(len(w.peptide) == 9 for w in ws)
    assert all(w.peptide[w.mutation_position - 1] == "D" for w in ws)
    # Every mutant carries its wild-type control, differing at exactly one site.
    for w in ws:
        assert sum(a != b for a, b in zip(w.peptide, w.wildtype)) == 1


def test_positions_are_one_based_and_mismatch_is_caught():
    """The single most common off-by-one in neoantigen code."""
    with pytest.raises(peptides.SequenceError, match="isoform"):
        peptides.mutant_windows(KRAS, 12, "D", wildtype_aa="V")
    with pytest.raises(peptides.SequenceError, match="1-based"):
        peptides.mutant_windows(KRAS, 9999, "D")
    with pytest.raises(peptides.SequenceError):
        peptides.mutant_windows(KRAS, 0, "D")


def test_anchor_classification():
    ws = peptides.mutant_windows(KRAS, 12, "D", lengths=(9,))
    anchors = {w.mutation_position for w in ws if w.is_anchor}
    assert anchors == {2, 9}      # P2 and the C-terminus


def test_dna_input_is_rejected_with_a_useful_message():
    with pytest.raises(peptides.SequenceError, match="translate"):
        peptides.clean_sequence("ATGGCGTAGCTAGCTAGCTB")


def test_frameshift_yields_many_novel_peptides():
    wt = "MKTAYIAKQRQ" + "ISFVKSHFSRQ"
    mut = "MKTAYIAKQRQ" + "PLQTGDWCVYA"     # diverges after position 11
    novel = peptides.frameshift_peptides(wt, mut, lengths=(9,))
    assert len(novel) > 5
    assert all(len(p) == 9 for p in novel)
    # Every returned window must overlap the novel tail.
    assert all(p not in wt for p in novel)


def test_junction_analysis_finds_linker_spanning_peptides():
    r = peptides.analyse_junctions(["SIINFEKL", "ASMTNMELM"], linker="AAY")
    assert r["construct"] == "SIINFEKLAAYASMTNMELM"
    assert len(r["junctions"]) == 1
    novel = r["junctions"][0]["novel_peptides"]
    assert novel
    # Everything returned must be genuinely novel — absent from both epitopes.
    assert all(p not in "SIINFEKL" and p not in "ASMTNMELM" for p in novel)
    # And must actually touch the junction region.
    assert all(p in r["construct"] for p in novel)


def test_no_linker_still_creates_junctional_peptides():
    r = peptides.analyse_junctions(["SIINFEKL", "ASMTNMELM"], linker="")
    assert r["total_novel_peptides"] > 0
    assert r["construct"] == "SIINFEKLASMTNMELM"


def test_a_construct_needs_two_epitopes():
    with pytest.raises(peptides.SequenceError):
        peptides.analyse_junctions(["SIINFEKL"])


@pytest.mark.parametrize("raw,expected", [
    ("A*0201", "HLA-A*02:01"),
    ("hla-a02:01", "HLA-A*02:01"),
    ("HLA-A*02:01", "HLA-A*02:01"),
    ("HLA-DRB1*15:01", "HLA-DRB1*15:01"),
])
def test_hla_normalization(raw, expected):
    assert peptides.normalize_hla(raw)["normalized"] == expected


def test_serological_hla_is_rejected_with_the_reason():
    r = peptides.normalize_hla("HLA-A2")
    assert r["valid"] is False
    assert "A*02:01" in r["note"] and "A*02:06" in r["note"]


def test_null_allele_is_flagged():
    r = peptides.normalize_hla("HLA-A*24:02N")
    assert "Null allele" in r["note"]


def test_peptide_summary_flags_synthesis_risk():
    s = peptides.summarize_peptide("VVVLLLIIIWWW")
    assert s["gravy"] > 1.0
    assert "hydrophobic" in (s["synthesis_warning"] or "")
    assert peptides.summarize_peptide("SIINFEKL")["mhc_fit"].startswith("class I")


# ------------------------------------------------------------- dashboard API

def test_api_stats_and_search(brain):
    con, config, db = brain
    api = Api()
    s = api.stats({}, {})
    assert "papers" in s and "graph" in s and "environment" in s

    assert api.search({"q": ""}, {})["hits"] == []
    assert "hits" in api.search({"q": "neoantigen"}, {})


def test_api_journal_roundtrip(brain):
    api = Api()
    r = api.journal_add({}, {"text": "dashboard wrote this", "kind": "observation"})
    assert r["ok"] and r["id"]
    listed = api.journal_list({"q": "dashboard"}, {})
    assert any("dashboard wrote this" in e["text"] for e in listed["entries"])


def test_api_rejects_an_empty_journal_entry(brain):
    assert "error" in Api().journal_add({}, {"text": "   "})


def test_api_peptide_endpoints_report_errors_rather_than_raising(brain):
    api = Api()
    bad = api.peptide_windows({}, {"protein": "ZZZZ", "position": 1, "mutant_aa": "D"})
    assert "error" in bad

    good = api.peptide_windows({}, {"protein": KRAS, "position": 12,
                                    "mutant_aa": "D", "lengths": [9]})
    assert good["count"] == 9

    j = api.peptide_junctions({}, {"epitopes": ["SIINFEKL", "ASMTNMELM"], "linker": "AAY"})
    assert j["total_novel_peptides"] > 0


def test_api_graph_handles_a_missing_entity(brain):
    r = Api().graph_data({"entity": "not-a-real-entity"}, {})
    assert r["nodes"] == [] and "error" in r


def test_api_models_endpoint_exposes_caveats(brain):
    r = Api().models_list({"task": "tcr_specificity"}, {})
    assert r["models"]
    assert all(m["caveat"] for m in r["models"])
    assert "weakest" in r["guidance"].lower()


def test_dashboard_refuses_a_non_local_bind():
    from neobrain.web.server import serve

    with pytest.raises(SystemExit, match="Refusing to bind"):
        serve(host="0.0.0.0", port=0, open_browser=False)
