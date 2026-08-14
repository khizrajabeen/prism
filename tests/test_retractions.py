"""Retraction watch, and the distinction between "absent" and "unchecked".

Two failures are being guarded against here, and they are the same failure
wearing different clothes:

* citing a paper that has been retracted, because nothing ever looked again;
* reporting a paper as "not reported" when nothing has read it, because an
  empty cell and an unexamined cell were stored identically.

Both are cases of a tool reassuring you about something it never checked.
"""

from __future__ import annotations

import json

from neobrain import db as db_mod, extract, memory, retractions


def _paper(con, db, pid, *, title="A paper", doi="", pmid="", methods=None,
           abstract=""):
    db.upsert_paper(con, {
        "id": pid, "source": "journal", "provider": "t", "title": title,
        "abstract": abstract, "authors": "Doe J", "journal": "J Test",
        "pub_date": "2026-01-01", "doi": doi, "pmid": pmid, "pmcid": "",
        "url": "", "is_oa": 1, "score": 10, "matched": "", "buckets": "t"})
    if methods:
        con.execute(
            "INSERT INTO sections(paper_id, ord, heading, kind, text) "
            "VALUES (?,0,'Methods','methods',?)", (pid, methods))
    con.commit()


def _flag(con, pid, status="retracted", note="retraction (2026-03-01)"):
    con.execute(
        "UPDATE papers SET retraction_status=?, retraction_note=?, "
        "retraction_url=?, retraction_checked_at=? WHERE id=?",
        (status, note, "https://doi.org/10.0/notice", db_mod.now(), pid))
    con.commit()


# --------------------------------------------------------------- severity

def test_a_paper_that_is_both_corrected_and_retracted_is_retracted():
    assert retractions._worst(["corrected", "retracted"]) == "retracted"
    assert retractions._worst(["corrected", "concern"]) == "concern"
    assert retractions._worst([]) == "clean"


def test_an_unrecognised_crossref_relation_does_not_flag_the_paper(monkeypatch):
    """Publishers deposit relations we do not model (`new-version`, and others).

    Those must read as clean rather than as an unmapped alarm, and a real
    retraction alongside one must still be caught.
    """
    def response(relations):
        class R:
            @staticmethod
            def json():
                return {"message": {"type": "journal-article",
                                    "updated-by": relations}}
        return R()

    monkeypatch.setattr(retractions.http, "get",
                        lambda *a, **k: response([{"type": "new-version"}]))
    assert retractions.check_crossref("10.0/x")[0] == "clean"

    monkeypatch.setattr(retractions.http, "get", lambda *a, **k: response(
        [{"type": "new-version"}, {"type": "retraction", "DOI": "10.0/n"}]))
    assert retractions.check_crossref("10.0/x")[0] == "retracted"


# ------------------------------------------------------------ propagation

def test_a_retracted_paper_surfaces_the_belief_that_depends_on_it(brain):
    con, config, db = brain
    _paper(con, db, "P:1", title="The retracted one", doi="10.0/x")
    bid = memory.add_belief(con, "Neoantigen load predicts response",
                            confidence="high", sources=[{"paper_id": "P:1"}])
    _flag(con, "P:1")

    hits = retractions.affected(con)
    assert len(hits) == 1
    assert hits[0]["kind"] == "belief"
    assert hits[0]["id"] == bid
    assert hits[0]["status"] == "retracted"
    assert hits[0]["paper_id"] == "P:1"
    assert retractions.blocking_count(con) == 1


def test_a_clean_paper_produces_no_alarm(brain):
    con, config, db = brain
    _paper(con, db, "P:1", doi="10.0/x")
    memory.add_belief(con, "A claim", confidence="high",
                      sources=[{"paper_id": "P:1"}])
    _flag(con, "P:1", status="clean", note="")

    assert retractions.affected(con) == []
    assert retractions.blocking_count(con) == 0


def test_hypotheses_and_claim_checks_are_traced_too(brain):
    con, config, db = brain
    from neobrain import science

    _paper(con, db, "P:1", title="The retracted one")
    pid = science.create_project(con, "A project", question="Does it work?")
    hid = science.add_hypothesis(con, "Vaccination extends survival",
                                 project_id=pid,
                                 falsifier="No survival difference at 90 days")
    con.execute(
        "INSERT INTO evidence_links(kind, item_id, paper_id, stance, linked_at) "
        "VALUES ('hypothesis',?,?,'supports',?)", (hid, "P:1", db_mod.now()))
    con.execute(
        "INSERT INTO claim_checks(checked_at, claim, verdict, sources) "
        "VALUES (?,?,?,?)",
        (db_mod.now(), "Vaccination extends survival", "likely-supported",
         json.dumps([{"paper_id": "P:1"}])))
    con.commit()
    _flag(con, "P:1")

    kinds = {h["kind"] for h in retractions.affected(con)}
    assert kinds == {"hypothesis", "claim-check"}


def test_retractions_are_ordered_worst_first(brain):
    con, config, db = brain
    _paper(con, db, "P:1", title="Corrected")
    _paper(con, db, "P:2", title="Retracted")
    memory.add_belief(con, "Claim A", sources=[{"paper_id": "P:1"}])
    memory.add_belief(con, "Claim B", sources=[{"paper_id": "P:2"}])
    _flag(con, "P:1", status="corrected", note="erratum")
    _flag(con, "P:2", status="retracted")

    hits = retractions.affected(con)
    assert [h["status"] for h in hits] == ["retracted", "corrected"]


# ------------------------------------------------------------- the brief

def test_the_brief_shouts_before_it_says_anything_else(brain):
    con, config, db = brain
    _paper(con, db, "P:1", title="The retracted one")
    memory.add_belief(con, "Neoantigen load predicts response",
                      sources=[{"paper_id": "P:1"}])
    _flag(con, "P:1")

    text = memory.brief(con, config.load())
    alarm = text.index("depend on a flagged paper")
    assert alarm < text.index("Corpus state")
    assert "RETRACTED" in text
    # The instruction, not just the fact.
    assert "withdrawal of the evidence" in text


def test_the_brief_is_unchanged_when_nothing_is_flagged(brain):
    con, config, db = brain
    _paper(con, db, "P:1")
    memory.add_belief(con, "A claim", sources=[{"paper_id": "P:1"}])

    assert "flagged paper" not in memory.brief(con, config.load())


# -------------------------------------------------------------- coverage

def test_coverage_separates_never_checked_from_clean(brain):
    con, config, db = brain
    _paper(con, db, "P:1", doi="10.0/a")
    _paper(con, db, "P:2", doi="10.0/b")
    _paper(con, db, "P:3")               # no doi, no pmid — not checkable at all
    _flag(con, "P:1", status="clean", note="")

    cov = retractions.coverage(con)
    assert cov["papers"] == 3
    assert cov["checkable"] == 2
    assert cov["checked"] == 1
    assert cov["unchecked"] == 1
    assert cov["flagged"] == 0


def test_status_of_reports_unknown_rather_than_clean_for_an_unchecked_paper(brain):
    con, config, db = brain
    _paper(con, db, "P:1", doi="10.0/a")

    s = retractions.status_of(con, "P:1")
    assert s["status"] == "unknown"
    assert "never checked" in s["note"]

    _flag(con, "P:1", status="clean", note="")
    assert retractions.status_of(con, "P:1")["status"] == "clean"


def test_status_of_an_unknown_paper_is_not_an_exception(brain):
    con, config, db = brain
    assert retractions.status_of(con, "P:nope")["status"] == "unknown"


# ------------------------------------------------------------------ sweep

def test_sweep_prioritises_papers_something_actually_cites(brain, monkeypatch):
    con, config, db = brain
    _paper(con, db, "P:uncited", doi="10.0/a")
    _paper(con, db, "P:cited", doi="10.0/b")
    memory.add_belief(con, "A claim", sources=[{"paper_id": "P:cited"}])

    order: list[str] = []

    def fake_check(row):
        order.append(row["doi"])
        return {"status": "clean", "note": "", "url": "", "reachable": True}

    monkeypatch.setattr(retractions, "check_paper", fake_check)
    retractions.sweep(con, limit=2)
    assert order[0] == "10.0/b"          # the cited paper first


def test_an_unreachable_source_is_not_recorded_as_clean(brain, monkeypatch):
    con, config, db = brain
    _paper(con, db, "P:1", doi="10.0/a")

    monkeypatch.setattr(retractions, "check_paper", lambda row: {
        "status": "", "note": "", "url": "", "reachable": False})
    r = retractions.sweep(con, limit=5)

    assert r["unreachable"] == 1
    assert r["checked"] == 0
    # Still unknown, and still due next time.
    assert retractions.status_of(con, "P:1")["status"] == "unknown"


def test_a_newly_flagged_paper_is_journalled(brain, monkeypatch):
    con, config, db = brain
    from neobrain import journal

    _paper(con, db, "P:1", title="The one", doi="10.0/a")
    monkeypatch.setattr(retractions, "check_paper", lambda row: {
        "status": "retracted", "note": "retraction (2026-03-01)",
        "url": "", "reachable": True})
    retractions.sweep(con, limit=5)

    entries = journal.recall(con, "retracted")
    assert any("P:1" in e["text"] for e in entries)


def test_crossref_parses_an_updated_by_relation(monkeypatch):
    class FakeResponse:
        @staticmethod
        def json():
            return {"message": {"type": "journal-article", "updated-by": [
                {"type": "retraction", "DOI": "10.0/notice",
                 "updated": {"date-time": "2026-03-01T00:00:00Z"}}]}}

    monkeypatch.setattr(retractions.http, "get", lambda *a, **k: FakeResponse())
    status, note, url = retractions.check_crossref("10.0/x")
    assert status == "retracted"
    assert "2026-03-01" in note
    assert url == "https://doi.org/10.0/notice"


def test_crossref_failure_is_none_not_clean(monkeypatch):
    monkeypatch.setattr(retractions.http, "get", lambda *a, **k: None)
    assert retractions.check_crossref("10.0/x") is None
    assert retractions.check_paper({"doi": "10.0/x", "pmid": ""})["reachable"] is False


def test_pubmed_publication_type_is_a_fallback_signal(monkeypatch):
    class FakeResponse:
        @staticmethod
        def json():
            return {"result": {"1": {"pubtype": ["Journal Article",
                                                 "Retracted Publication"]}}}

    monkeypatch.setattr(retractions.http, "get", lambda *a, **k: FakeResponse())
    status, note, _ = retractions.check_pubmed("1")
    assert status == "retracted"
    assert "PubMed" in note


# ------------------------------------------- not reported vs never checked

def test_an_unextracted_paper_reads_not_checked_not_not_reported(brain):
    con, config, db = brain
    _paper(con, db, "P:1", methods="Mice were vaccinated.")

    m = extract.matrix(con, ["P:1"], fields=["sample_size"])
    cell = m["rows"][0]["cells"]["sample_size"]
    assert cell["state"] == extract.NOT_CHECKED
    assert m["gaps"]["sample_size"] == 0        # not counted against the paper
    assert m["unknown"]["sample_size"] == 1
    assert m["unchecked_papers"] == ["P:1"]


def test_an_extracted_paper_with_no_value_reads_not_reported(brain):
    con, config, db = brain
    _paper(con, db, "P:1", methods="Mice bearing B16F10 tumours were vaccinated.")
    extract.store(con, "P:1", extract.extract_paper(con, "P:1"))

    m = extract.matrix(con, ["P:1"], fields=["sample_size"])
    cell = m["rows"][0]["cells"]["sample_size"]
    assert cell["state"] == extract.NOT_REPORTED
    assert m["gaps"]["sample_size"] == 1
    assert m["unknown"]["sample_size"] == 0
    assert "methods text was searched" in cell["why"]


def test_absence_in_an_abstract_only_paper_is_not_a_finding(brain):
    con, config, db = brain
    _paper(con, db, "P:1", abstract="We vaccinated mice and observed a response.")
    extract.store(con, "P:1", extract.extract_paper(con, "P:1"))

    m = extract.matrix(con, ["P:1"], fields=["randomization"])
    cell = m["rows"][0]["cells"]["randomization"]
    assert cell["state"] == extract.ABSTRACT_ONLY
    assert m["gaps"]["randomization"] == 0
    assert "uninformative" in cell["why"]


def test_gap_denominators_count_only_papers_that_were_read(brain):
    con, config, db = brain
    _paper(con, db, "P:read", methods="Mice bearing B16F10 tumours were vaccinated.")
    _paper(con, db, "P:unread", methods="Mice were vaccinated.")
    extract.store(con, "P:read", extract.extract_paper(con, "P:read"))

    m = extract.matrix(con, ["P:read", "P:unread"], fields=["sample_size"])
    # One of two papers is in the table, but only one was judgeable — the
    # honest claim is "1/1 that were read", never "1/2 papers".
    assert m["gap_summary"]["sample_size"].startswith("1/1")
    assert "1 not judgeable" in m["gap_summary"]["sample_size"]


def test_a_run_that_found_nothing_still_records_the_run(brain):
    con, config, db = brain
    _paper(con, db, "P:1", methods="Nothing quantitative here at all.")
    extract.store(con, "P:1", extract.extract_paper(con, "P:1"))

    run = extract.run_for(con, "P:1")
    assert run is not None
    assert run["scope"] == "fulltext"
    assert run["n_values"] >= 0
    assert "sample_size" in run["fields"]
