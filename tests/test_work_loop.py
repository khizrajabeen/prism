"""The research loop: projects, hypotheses, experiments, claim checking, clinic.

These test the guardrails, not just the plumbing. The falsifier requirement and
the prediction-before-result rule are the two places this system says no to the
user, and they are the reason it is a research tool rather than a note store.
"""

import pytest

from neobrain import answer, clinic, science


# ------------------------------------------------------------ the guardrails

def test_hypothesis_requires_a_falsifier(brain):
    con, config, db = brain
    with pytest.raises(ValueError, match="falsifier"):
        science.add_hypothesis(con, "Class II epitopes are important")

    hid = science.add_hypothesis(
        con, "Class II epitopes improve durability",
        falsifier="No difference in rechallenge survival between arms")
    assert hid


def test_experiment_requires_a_prediction_before_the_result(brain):
    con, config, db = brain
    with pytest.raises(ValueError, match="prediction"):
        science.plan_experiment(con, "MC38 vaccination study")

    eid = science.plan_experiment(
        con, "MC38 vaccination study",
        prediction="I+II arm reaches >=40% rechallenge survival")
    row = con.execute("SELECT predicted_at, ended_at, result FROM experiments WHERE id=?",
                      (eid,)).fetchone()
    assert row["predicted_at"] and not row["ended_at"] and not row["result"]


def test_result_is_reported_against_the_prediction(brain):
    con, config, db = brain
    eid = science.plan_experiment(con, "study", prediction="tumours shrink by day 21")
    r = science.record_result(con, eid, "no difference at any timepoint",
                              outcome="contradicted")
    assert r["predicted"] == "tumours shrink by day 21"
    assert r["observed"] == "no difference at any timepoint"
    # A contradicted prediction must prompt, not just record.
    assert "hypothesis wrong" in r["prompt"] or "unable to test" in r["prompt"]


def test_confirmation_also_gets_challenged(brain):
    con, config, db = brain
    eid = science.plan_experiment(con, "study", prediction="tumours shrink")
    r = science.record_result(con, eid, "tumours shrank", outcome="as_predicted")
    assert "could only confirm" in r["prompt"]


# ------------------------------------------------------------------ the loop

def test_whats_next_prioritises_unrecorded_results(brain):
    con, config, db = brain
    pid = science.create_project(con, "p", "a question")
    hid = science.add_hypothesis(con, "a claim", project_id=pid, falsifier="X happens")
    eid = science.plan_experiment(con, "running study", project_id=pid,
                                  hypothesis_id=hid, prediction="Y")
    con.execute("UPDATE experiments SET status='running' WHERE id=?", (eid,))
    con.commit()
    science.add_hypothesis(con, "untested claim", project_id=pid, falsifier="Z happens")

    items = science.whats_next(con)
    assert items[0]["kind"] == "record result"
    assert any(i["kind"] == "design an experiment" for i in items)


def test_project_status_counts(brain):
    con, config, db = brain
    pid = science.create_project(con, "proj", "q")
    hid = science.add_hypothesis(con, "h", project_id=pid, falsifier="f")
    eid = science.plan_experiment(con, "e", project_id=pid, hypothesis_id=hid, prediction="p")
    science.record_result(con, eid, "r", outcome="contradicted")

    st = science.project_status(con, pid)
    assert st["counts"]["open_hypotheses"] == 1
    assert st["counts"]["surprises"] == 1
    assert st["hypotheses"][0]["experiments"][0]["id"] == eid


def test_work_events_land_in_permanent_memory(brain):
    con, config, db = brain
    from neobrain import journal

    pid = science.create_project(con, "p", "q")
    hid = science.add_hypothesis(con, "h", project_id=pid, falsifier="f")
    eid = science.plan_experiment(con, "e", project_id=pid, hypothesis_id=hid, prediction="p")
    science.record_result(con, eid, "observed something", outcome="ambiguous")

    texts = " ".join(e["text"] for e in journal.recall(con, "", limit=50))
    assert "Hypothesis" in texts and "Experiment" in texts
    assert "observed something" in texts


# ---------------------------------------------------------- claim checking

def test_empty_corpus_is_reported_as_no_evidence_not_disagreement(brain):
    con, config, db = brain
    r = answer.check("The moon is made of cheese", con=con, record=False)
    assert r["verdict"] == "no-evidence"
    assert "not disagreement" in r["note"] or "not about the literature" in r["note"]


def test_quantitative_claims_need_quantitative_sources(brain):
    con, config, db = brain
    con.execute(
        """INSERT INTO chunks(paper_id, doc_kind, doc_ref, heading, ord, text, n_chars)
           VALUES (NULL,'knowledge','k.md','h',0,?,100)""",
        ("Neoantigen vaccination in MC38 improves survival substantially in "
         "immunocompetent mice, and we show durable protection.",),
    )
    con.commit()
    r = answer.check("Neoantigen vaccination in MC38 improves survival by 90 percent",
                     con=con, record=False)
    assert r["verdict"] == "unverifiable-number"
    assert "quantitative" in r["note"]


def test_verdict_vocabulary_is_always_hedged(brain):
    """No verdict may read as a truth claim — this is lexical, not entailment."""
    con, config, db = brain
    allowed = {"likely-supported", "possibly-contradicted", "disputed",
               "no-evidence", "needs-review", "unverifiable-number"}
    for claim in ["B2M loss abolishes MHC class I presentation",
                  "CT26 has a dominant AH1 response",
                  "Vaccines work"]:
        r = answer.check(claim, con=con, record=False)
        assert r["verdict"] in allowed
        assert "NOT entailment" in r["method"]


def test_check_records_provenance(brain):
    con, config, db = brain
    answer.check("some claim about neoantigens", con=con, record=True)
    row = con.execute("SELECT claim, verdict FROM claim_checks ORDER BY id DESC LIMIT 1").fetchone()
    assert row["claim"] == "some claim about neoantigens"


def test_draft_check_skips_non_claims(brain):
    con, config, db = brain
    results = answer.check_draft(
        "In this section we describe the method. Is that right? "
        "Neoantigen vaccination in MC38 improves survival substantially.",
        con=con)
    claims = [r["claim"] for r in results]
    assert not any("In this section" in c for c in claims)
    assert not any(c.endswith("?") for c in claims)


def test_compose_reports_coverage_and_exports_citations(brain):
    con, config, db = brain
    db.upsert_paper(con, {
        "id": "MED:1", "source": "journal", "provider": "t", "title": "Neoantigen study",
        "abstract": "We show neoantigen vaccination improves survival. " * 10,
        "authors": "Doe J", "journal": "J Test", "pub_date": "2026-01-01",
        "doi": "10.1/x", "pmid": "", "pmcid": "", "url": "https://e.org", "is_oa": 1,
        "score": 10, "matched": "", "buckets": "core",
    })
    con.commit()
    from neobrain import embeddings
    embeddings.rebuild_chunks_for_paper(con, "MED:1", config.load())
    con.commit()

    r = answer.compose("neoantigen vaccination survival", con=con)
    assert r["coverage"]["papers_mentioning"] >= 1
    assert "@article" in r["bibtex"]
    assert any("cite each claim" in i for i in r["instructions"])


# ----------------------------------------------------------------- clinical

def test_screen_flags_b2m_as_an_exclusion_signal(brain):
    con, config, db = brain
    profile = clinic.PatientProfile(tumour_type="pancreatic", variants=["KRAS G12D", "B2M"],
                                    hla=["HLA-A*02:01"])
    r = clinic.screen(con, profile)
    assert any("B2M" in f for f in r["flags"])
    tiers = {a["matched"]: a["escat"] for a in r["actionability"]}
    assert tiers["KRAS G12D"].startswith("II")


def test_screen_rejects_serological_hla(brain):
    con, config, db = brain
    r = clinic.screen(con, clinic.PatientProfile(variants=[], hla=["HLA-A2"]))
    assert any("four-digit" in f for f in r["flags"])


def test_unknown_variants_are_not_given_a_confident_tier(brain):
    con, config, db = brain
    r = clinic.screen(con, clinic.PatientProfile(variants=["FOO Q99Z"]))
    a = r["actionability"][0]
    assert a["escat"] == "unassigned"
    assert "OncoKB" in a["note"] or "CIViC" in a["note"]


def test_screen_always_states_it_is_not_an_eligibility_determination(brain):
    con, config, db = brain
    r = clinic.screen(con, clinic.PatientProfile(tumour_type="melanoma"))
    joined = " ".join(r["limits"]).lower()
    assert "not an eligibility determination" in joined
    assert "treatment recommendation" in joined
    assert "false positives and false negatives" in joined


def test_trial_matching_explains_itself(brain):
    con, config, db = brain
    db.upsert_trial(con, {
        "nct_id": "NCT1", "title": "Personalized neoantigen vaccine in melanoma",
        "status": "RECRUITING", "phase": "Phase 1", "conditions": "Melanoma",
        "interventions": "Biological: mRNA vaccine", "sponsor": "S", "enrollment": 20,
        "start_date": "", "last_update": "2026-01-01", "url": "u", "summary": "",
    })
    con.commit()
    r = clinic.screen(con, clinic.PatientProfile(tumour_type="melanoma"))
    assert r["trials"] and r["trials"][0]["nct_id"] == "NCT1"
    assert r["trials"][0]["why"]
