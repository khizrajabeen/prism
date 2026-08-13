"""Network behaviour and the agent tool surface.

No test here hits the network. The point is the failure paths — a sweep on a
disconnected laptop, and the guarantee that the agent cannot approve its own
memory edits.
"""

import pytest
import requests

from neobrain import retrieve
from neobrain.sources import europepmc, fulltext, http, preprints


# ------------------------------------------------------------ circuit breaker

def test_circuit_trips_after_repeated_unreachability(monkeypatch):
    http.reset_circuits()
    calls = {"n": 0}

    class Boom:
        def get(self, *a, **k):
            calls["n"] += 1
            raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(http, "session", lambda: Boom())
    monkeypatch.setattr(http.time, "sleep", lambda s: None)

    for _ in range(5):
        assert http.get("https://example.org/x", delay=0) is None

    # Three failures trip the breaker; the last two calls short-circuit.
    assert calls["n"] == http.FAILURE_LIMIT
    assert "example.org" in http.circuit_state()
    http.reset_circuits()


def test_a_success_closes_the_circuit(monkeypatch):
    http.reset_circuits()
    state = {"fail": True}

    class Flaky:
        def get(self, *a, **k):
            if state["fail"]:
                raise requests.ConnectionError("down")

            class R:
                status_code = 200

                def raise_for_status(self):
                    return None

            return R()

    monkeypatch.setattr(http, "session", lambda: Flaky())
    monkeypatch.setattr(http.time, "sleep", lambda s: None)

    http.get("https://example.org/x", delay=0)
    state["fail"] = False
    assert http.get("https://example.org/x", delay=0) is not None
    assert http.circuit_state() == {}
    http.reset_circuits()


# ------------------------------------------------------------- normalization

def test_europepmc_normalize_handles_a_preprint():
    row = europepmc.normalize({
        "source": "PPR", "id": "PPR812345", "title": "A neoantigen preprint.",
        "abstractText": "<p>Body with <i>markup</i>.</p>", "doi": "10.1101/2026.01.01",
        "firstPublicationDate": "2026-01-02", "isOpenAccess": "Y",
    })
    assert row["id"] == "PPR:PPR812345"
    assert row["source"] == "preprint"
    assert row["title"] == "A neoantigen preprint"   # trailing period stripped
    assert "<" not in row["abstract"]                # markup stripped
    assert row["is_oa"] is True


def test_europepmc_normalize_survives_a_junk_record():
    """One malformed record must not kill a whole sweep."""
    rows = list(europepmc.iter_normalized([{"source": "MED"}, None]))
    assert len(rows) == 1


def test_preprint_interest_filter():
    rec = {"title": "Neoantigen prediction", "abstract": "class I"}
    assert preprints.matches_interest(rec, ["neoantigen"])
    assert not preprints.matches_interest(rec, ["organoid"])


def test_section_headings_are_classified():
    assert fulltext.classify_heading("Materials and Methods") == "methods"
    assert fulltext.classify_heading("Statistical analysis") == "methods"
    assert fulltext.classify_heading("2. Results") == "results"
    assert fulltext.classify_heading("Data availability") == "data"
    assert fulltext.classify_heading("Wombats") == "other"


def test_jats_parsing_extracts_sections():
    xml = """<article><front><article-meta><permissions><license
        xlink:href="https://creativecommons.org/licenses/by/4.0/"
        xmlns:xlink="http://www.w3.org/1999/xlink"/></permissions>
        <abstract><p>%s</p></abstract></article-meta></front>
        <body><sec><title>Methods</title><p>%s</p></sec></body></article>""" % (
        "Abstract text. " * 20, "We vaccinated mice with peptide. " * 20,
    )
    sections, licence = fulltext.parse_sections(xml)
    kinds = {s["kind"] for s in sections}
    assert "abstract" in kinds and "methods" in kinds
    assert "creativecommons" in licence


# ------------------------------------------------------- trial keyword search

def test_keywords_drop_question_words():
    assert retrieve.keywords("why is the adjuvant important for MC38?") == [
        "important", "adjuvant", "mc38",
    ]
    assert retrieve.keywords("what is it?") == []


def test_matching_trials_needs_a_content_word(brain):
    con, config, db = brain
    db.upsert_trial(con, {
        "nct_id": "NCT9", "title": "Neoantigen vaccine in melanoma", "status": "Recruiting",
        "phase": "Phase 1", "conditions": "Melanoma", "interventions": "Biological: vaccine",
        "sponsor": "S", "enrollment": 10, "start_date": "", "last_update": "2026-01-01",
        "url": "u", "summary": "",
    })
    con.commit()
    assert [r["nct_id"] for r in retrieve.matching_trials(con, "melanoma vaccine")] == ["NCT9"]
    assert retrieve.matching_trials(con, "why is that") == []


# ------------------------------------------------------- local ingestion

def test_short_documents_are_not_silently_emptied():
    """Sectioning must never lose content just because the sections are short."""
    from neobrain.sources import local

    text = "Abstract\nShort.\n\nMethods\nAlso short.\n"
    sections = local.split_sections(text)
    assert sections, "a document with headings but short bodies must still be stored"
    assert sections[0]["text"]


def test_ingested_document_is_searchable_immediately(brain, tmp_path):
    """A paper you ingested but cannot find is worse than one you never ingested."""
    from neobrain import retrieve
    from neobrain.sources import local

    con, config, db = brain
    doc = tmp_path / "paper.md"
    doc.write_text(
        "A study of MC38 vaccination\n\n"
        "Abstract\n" + "We vaccinated mice with a long peptide and poly-ICLC. " * 5 + "\n\n"
        "Methods\n" + "Mice were randomized at sixty cubic millimetres. " * 5 + "\n",
        encoding="utf-8",
    )
    report = local.ingest_pdf(con, doc)
    assert report["ok"] and report["chunks"] > 0

    hits = retrieve.search("randomized sixty cubic millimetres", cfg=config.load(), con=con)
    assert hits and hits[0].paper_id == report["id"]
    assert "methods" in (hits[0].heading or "").lower()


def test_extraction_failure_is_distinguished_from_a_short_file(brain, tmp_path):
    from neobrain.sources import local

    con, config, db = brain
    tiny = tmp_path / "tiny.md"
    tiny.write_text("hello", encoding="utf-8")
    report = local.ingest_pdf(con, tiny)
    assert not report["ok"]
    assert "characters extracted" in report["error"]


# ------------------------------------------------------------- agent surface

def test_agent_cannot_apply_its_own_memory_edits():
    """The safety property of the whole design, asserted at the tool boundary."""
    mcp = pytest.importorskip("mcp", reason="MCP SDK optional")
    import asyncio

    from neobrain import mcp_server

    tools = asyncio.run(mcp_server._server().list_tools())
    names = {t.name for t in tools}

    assert "propose_memory_edit" in names
    assert not any("apply" in n or "approve" in n for n in names), (
        "an agent that can approve its own belief changes has no drift guard"
    )


def test_knowledge_reads_cannot_escape_the_directory():
    pytest.importorskip("mcp", reason="MCP SDK optional")
    import asyncio

    from neobrain import mcp_server

    srv = mcp_server._server()
    result = asyncio.run(srv.call_tool("read_knowledge", {"filename": "../../etc/passwd"}))
    text = "".join(c.text for c in result.content)
    assert "root:" not in text
    assert "No such note" in text
