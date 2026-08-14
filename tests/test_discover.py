"""Guided discovery: clarification, federated search, dedup, screening, export.

Sources are mocked throughout — these test the logic that runs *after* the
network, which is where the interesting failures are. Deduplication in
particular has a failure mode that looks like success: the same paper reported
twice because two sources described it differently.
"""

import pytest

from neobrain import discover
from neobrain.sources import openalex, pubmed


# ------------------------------------------------------------ clarification

def test_clarify_only_asks_what_the_keywords_have_not_answered():
    broad = {f.id for f in discover.clarify("neoantigen vaccine")}
    assert {"system", "design", "mhc_class", "recency", "access"} <= broad

    specific = {f.id for f in discover.clarify(
        "MC38 mouse class II ELISpot randomized trial")}
    # Already stated: organism, design, MHC class, method.
    assert "system" not in specific
    assert "design" not in specific
    assert "mhc_class" not in specific
    # Recency and access are never inferable from keywords.
    assert {"recency", "access"} <= specific


def test_every_question_explains_why_it_is_being_asked():
    for f in discover.clarify("neoantigen"):
        assert f.why, f"facet {f.id} has no rationale"
        assert f.options


def test_build_query_composes_the_narrowing():
    plan = discover.build_query("neoantigen vaccine pancreatic", {
        "system": ["human"], "design": ["trials"], "mhc_class": ["ii"],
        "recency": "5", "access": "oa"})
    q = plan["query"]
    assert "neoantigen vaccine pancreatic" in q
    assert "clinical" in q and "class II" in q
    assert plan["open_access"] is True
    assert plan["from_year"] and plan["from_year"] > 2015
    assert "Clinical Trial" in plan["pubmed_types"]
    assert len(plan["narrowing"]) == 3


def test_build_query_with_no_answers_is_still_a_query():
    plan = discover.build_query("KRAS G12D", {})
    assert "KRAS G12D" in plan["query"]


def test_free_text_narrowing_is_included():
    plan = discover.build_query("neoantigen", {"extra": "immunopeptidomics"})
    assert "immunopeptidomics" in plan["query"]


# ------------------------------------------------------------- deduplication

def test_same_paper_from_three_sources_becomes_one():
    merged = discover.deduplicate([
        {"doi": "10.1/abc", "title": "A study", "provider": "openalex",
         "is_oa": True, "cited_by": 40, "pdf_url": "http://x/y.pdf"},
        {"doi": "10.1/ABC", "title": "A study", "provider": "pubmed",
         "pmid": "123", "abstract": "the abstract"},
        {"doi": "", "title": "A Study!", "provider": "europepmc"},
    ])
    assert len(merged) == 1
    m = merged[0]
    assert sorted(m["found_by"]) == ["europepmc", "openalex", "pubmed"]
    assert m["pmid"] == "123"           # identifiers unioned
    assert m["abstract"] == "the abstract"
    assert m["is_oa"] is True
    assert m["pdf_url"]


def test_doi_case_and_title_punctuation_do_not_split_records():
    merged = discover.deduplicate([
        {"doi": "10.1/X", "title": "Neoantigen prediction: a benchmark", "provider": "a"},
        {"doi": "10.1/x", "title": "Neoantigen prediction — a benchmark", "provider": "b"},
    ])
    assert len(merged) == 1


def test_distinct_papers_are_not_merged():
    merged = discover.deduplicate([
        {"doi": "10.1/a", "title": "First paper", "provider": "a"},
        {"doi": "10.1/b", "title": "Second paper", "provider": "a"},
    ])
    assert len(merged) == 2


def test_records_without_a_title_or_doi_are_dropped():
    assert discover.deduplicate([{"doi": "", "title": "", "provider": "a"}]) == []


# ------------------------------------------------------------------ search

def test_federated_search_merges_sources_and_reports_counts(monkeypatch):
    monkeypatch.setattr(openalex, "search", lambda *a, **k: [{"id": "W1"}])
    monkeypatch.setattr(openalex, "normalize", lambda w: {
        "doi": "10.1/a", "title": "Shared paper", "provider": "openalex",
        "is_oa": True, "cited_by": 5, "pdf_url": "p.pdf", "abstract": "x", "year": 2025})
    monkeypatch.setattr(pubmed, "search_and_fetch", lambda *a, **k: [
        {"doi": "10.1/a", "title": "Shared paper", "provider": "pubmed", "pmid": "9",
         "abstract": "x", "year": 2025},
        {"doi": "10.1/b", "title": "Only in pubmed", "provider": "pubmed", "abstract": "y",
         "year": 2024},
    ])

    plan = discover.build_query("test", {})
    r = discover.federated_search(plan, sources=("openalex", "pubmed"))

    assert r["counts"]["retrieved"] == 3
    assert r["counts"]["unique"] == 2
    assert r["counts"]["open_access"] == 1
    assert r["counts"]["per_source"] == {"openalex": 1, "pubmed": 2}
    # The paper both sources agree on ranks first.
    assert r["results"][0]["title"] == "Shared paper"


def test_a_dead_source_does_not_kill_the_search(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("openalex is down")

    monkeypatch.setattr(openalex, "search", boom)
    monkeypatch.setattr(pubmed, "search_and_fetch", lambda *a, **k: [
        {"doi": "10.1/b", "title": "Still found", "provider": "pubmed", "year": 2024}])

    r = discover.federated_search(discover.build_query("t", {}),
                                  sources=("openalex", "pubmed"))
    assert r["counts"]["unique"] == 1
    assert any("openalex" in e for e in r["errors"])


def test_retracted_records_are_dropped(monkeypatch):
    monkeypatch.setattr(pubmed, "search_and_fetch", lambda *a, **k: [
        {"doi": "10.1/r", "title": "Retracted work", "provider": "pubmed",
         "retracted": True, "year": 2020},
        {"doi": "10.1/ok", "title": "Fine work", "provider": "pubmed", "year": 2020},
    ])
    r = discover.federated_search(discover.build_query("t", {}), sources=("pubmed",))
    assert [x["title"] for x in r["results"]] == ["Fine work"]


def test_exclude_reviews_filters_them(monkeypatch):
    monkeypatch.setattr(pubmed, "search_and_fetch", lambda *a, **k: [
        {"doi": "10.1/1", "title": "A systematic review of vaccines",
         "provider": "pubmed", "type": "Review", "year": 2024},
        {"doi": "10.1/2", "title": "A trial", "provider": "pubmed",
         "type": "Clinical Trial", "year": 2024},
    ])
    plan = discover.build_query("t", {"design": ["primary"]})
    r = discover.federated_search(plan, sources=("pubmed",))
    assert [x["title"] for x in r["results"]] == ["A trial"]


# --------------------------------------------------------------- screening

def test_screening_keeps_everything_with_a_reason():
    res = [
        {"title": "Randomized phase 2 trial", "abstract": "randomized", "year": 2025, "is_oa": True},
        {"title": "Old review", "abstract": "we review", "year": 2019, "is_oa": False},
        {"title": "Unclear", "abstract": "something else", "year": 2024, "is_oa": True},
    ]
    s = discover.auto_screen(res, include_terms=["randomized"], exclude_terms=["review"],
                             min_year=2022)
    assert s["counts"] == {"include": 1, "exclude": 1, "maybe": 1}
    # Nothing discarded — an auditable screen keeps the excluded records.
    assert len(s["results"]) == 3
    assert all(r["screen_reasons"] for r in s["results"])


def test_missing_abstract_is_flagged_as_unscreenable():
    s = discover.auto_screen([{"title": "No abstract paper", "year": 2025}],
                             include_terms=["anything"])
    assert any("no abstract" in r.lower() for r in s["results"][0]["screen_reasons"])


# ------------------------------------------------------------------ export

@pytest.mark.parametrize("fmt,marker", [
    ("bibtex", "@article{"), ("ris", "TY  - JOUR"),
    ("csv", "title,authors"), ("markdown", "**"),
])
def test_export_formats(fmt, marker):
    out = discover.export([{
        "title": "A paper", "authors": "Doe J, Roe A", "journal": "J Test",
        "pub_date": "2026-01-01", "doi": "10.1/x", "url": "https://e.org",
        "abstract": "abs", "is_oa": True}], fmt)
    assert marker in out


# --------------------------------------------------------------- persistence

def test_saved_searches_keep_the_plan(brain):
    con, config, db = brain
    plan = discover.build_query("neoantigen", {"recency": "5"})
    result = {"results": [{"title": "A paper", "doi": "10.1/x", "is_oa": True,
                           "found_by": ["openalex"], "cited_by": 3}],
              "counts": {"unique": 1}}
    sid = discover.save_search(con, "my search", plan, result)

    saved = discover.get_searches(con)
    assert saved[0]["id"] == sid and saved[0]["keywords"] == "neoantigen"
    rows = discover.get_results(con, sid)
    assert rows[0]["title"] == "A paper" and rows[0]["is_oa"] == 1


# ---------------------------------------------------------------- download

def test_download_refuses_when_there_is_no_open_copy():
    r = discover.download_pdf({"title": "Paywalled", "url": "https://publisher/x"})
    assert r["ok"] is False
    assert "inbox" in r["next"].lower()


def test_download_rejects_a_landing_page(monkeypatch, tmp_path):
    class FakeResponse:
        content = b"<html>not a pdf</html>"

    monkeypatch.setattr(discover, "get", lambda *a, **k: FakeResponse())
    r = discover.download_pdf({"pdf_url": "http://x/y", "doi": "10.1/z"}, dest_dir=tmp_path)
    assert r["ok"] is False and "landing page" in r["reason"]


def test_download_writes_a_real_pdf(monkeypatch, tmp_path):
    class FakeResponse:
        content = b"%PDF-1.7 fake body"

    monkeypatch.setattr(discover, "get", lambda *a, **k: FakeResponse())
    r = discover.download_pdf({"pdf_url": "http://x/y.pdf", "doi": "10.1/z"}, dest_dir=tmp_path)
    assert r["ok"] and r["path"].endswith(".pdf")
    # Second call is served from disk rather than refetched.
    assert discover.download_pdf({"pdf_url": "http://x/y.pdf", "doi": "10.1/z"},
                                 dest_dir=tmp_path)["cached"] is True


# ------------------------------------------------------- source normalisation

def test_openalex_abstract_inverted_index_is_rebuilt():
    work = {
        "id": "https://openalex.org/W1", "display_name": "A title",
        "abstract_inverted_index": {"Neoantigen": [0], "vaccines": [1], "work": [2]},
        "doi": "https://doi.org/10.1/x", "publication_year": 2026,
        "open_access": {"is_oa": True, "oa_url": "http://x/y.pdf"},
        "authorships": [{"author": {"display_name": "Doe J"}}],
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/999"},
    }
    n = openalex.normalize(work)
    assert n["abstract"] == "Neoantigen vaccines work"
    assert n["doi"] == "10.1/x" and n["pmid"] == "999"
    assert n["is_oa"] and n["pdf_url"]


def test_pubmed_xml_parsing():
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation>
      <PMID>123</PMID>
      <Article><ArticleTitle>A neoantigen paper.</ArticleTitle>
        <Abstract><AbstractText>Findings here.</AbstractText></Abstract>
        <AuthorList><Author><LastName>Doe</LastName><Initials>J</Initials></Author></AuthorList>
        <Journal><Title>J Test</Title><JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue></Journal>
        <PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
      </Article>
      <MeshHeadingList><MeshHeading><DescriptorName>Neoplasms</DescriptorName></MeshHeading></MeshHeadingList>
    </MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    recs = pubmed._parse(xml)
    assert len(recs) == 1
    r = recs[0]
    assert r["pmid"] == "123"
    assert r["title"] == "A neoantigen paper"
    assert r["authors"] == "Doe J"
    assert r["year"] == 2026
    assert "Randomized" in r["type"]
    assert r["mesh"] == ["Neoplasms"]
