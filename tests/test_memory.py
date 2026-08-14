"""The approval gate is the safety property of this system, so it gets the
most tests. A proposal that applies without approval, or applies differently
than its diff showed, is the failure that matters."""


import pytest

from neobrain import memory


def test_belief_requires_a_source(brain):
    con, config, db = brain
    with pytest.raises(ValueError, match="rumour"):
        memory.add_belief(con, "class II matters", sources=[])


def test_belief_roundtrip_keeps_provenance(brain):
    con, config, db = brain
    bid = memory.add_belief(
        con, "Expression filtering removes most predicted binders",
        confidence="high", topic="pipeline",
        sources=[{"paper_id": "MED:1", "stance": "supports"},
                 {"citation": "TESLA", "stance": "qualifies"}],
    )
    beliefs = memory.get_beliefs(con, topic="pipeline")
    assert len(beliefs) == 1
    b = beliefs[0]
    assert b["id"] == bid and b["confidence"] == "high"
    assert len(b["sources"]) == 2
    assert b["review_on"]  # a belief with no review date silently becomes dogma


def test_supersede_marks_the_old_belief(brain):
    con, config, db = brain
    old = memory.add_belief(con, "old claim", sources=[{"citation": "x"}])
    new = memory.add_belief(con, "new claim", sources=[{"citation": "y"}])
    memory.supersede_belief(con, old, new, "contradicted by newer data")

    active = [b["id"] for b in memory.get_beliefs(con)]
    assert new in active and old not in active
    superseded = memory.get_beliefs(con, status="superseded")
    assert superseded[0]["superseded_by"] == new


def test_proposal_requires_a_rationale(brain):
    con, config, db = brain
    with pytest.raises(ValueError, match="rationale"):
        memory.propose(con, "knowledge", "x.md", {"mode": "append", "text": "y"},
                       rationale="  ")


def test_proposal_does_not_touch_the_file_until_applied(brain):
    con, config, db = brain
    path = config.KNOWLEDGE_DIR / "topic.md"
    path.write_text("# Topic\n\noriginal line\n", encoding="utf-8")

    pid = memory.propose(con, "knowledge", "topic.md",
                         {"mode": "append", "text": "new claim"},
                         rationale="because", evidence="MED:1")

    assert "new claim" not in path.read_text(encoding="utf-8")
    diff = memory.proposal_diff(con, pid)
    assert "+new claim" in diff

    memory.apply_proposal(con, pid)
    assert "new claim" in path.read_text(encoding="utf-8")


def test_applied_proposal_cannot_be_applied_twice(brain):
    con, config, db = brain
    (config.KNOWLEDGE_DIR / "t.md").write_text("# T\n", encoding="utf-8")
    pid = memory.propose(con, "knowledge", "t.md",
                         {"mode": "append", "text": "a"}, rationale="r")
    memory.apply_proposal(con, pid)
    with pytest.raises(ValueError, match="already applied"):
        memory.apply_proposal(con, pid)


def test_replace_section_replaces_only_that_section(brain):
    con, config, db = brain
    path = config.KNOWLEDGE_DIR / "s.md"
    path.write_text(
        "# Doc\n\n## Keep\n\nkeep this\n\n## Replace\n\nold body\n\n## Also keep\n\nalso\n",
        encoding="utf-8",
    )
    pid = memory.propose(con, "knowledge", "s.md",
                         {"mode": "replace_section", "heading": "## Replace",
                          "text": "new body"},
                         rationale="updated evidence")
    memory.apply_proposal(con, pid)

    text = path.read_text(encoding="utf-8")
    assert "new body" in text
    assert "old body" not in text
    assert "keep this" in text and "also" in text


def test_rejected_proposal_leaves_the_file_alone(brain):
    con, config, db = brain
    path = config.KNOWLEDGE_DIR / "r.md"
    path.write_text("original\n", encoding="utf-8")
    pid = memory.propose(con, "knowledge", "r.md",
                         {"mode": "append", "text": "junk"}, rationale="r")
    memory.reject_proposal(con, pid, "not convinced")

    assert path.read_text(encoding="utf-8") == "original\n"
    assert memory.pending_proposals(con) == []


def test_belief_proposal_creates_a_sourced_belief(brain):
    con, config, db = brain
    pid = memory.propose(
        con, "belief", "new",
        {"claim": "Clonal targets outperform subclonal", "confidence": "moderate",
         "topic": "ranking", "sources": [{"paper_id": "MED:9"}]},
        rationale="two independent cohorts", evidence="MED:9",
    )
    result = memory.apply_proposal(con, pid)
    beliefs = memory.get_beliefs(con, topic="ranking")
    assert beliefs[0]["id"] == result["belief_id"]
    assert beliefs[0]["sources"][0]["paper_id"] == "MED:9"


def test_brief_is_bounded_and_carries_the_rules(brain):
    con, config, db = brain
    brief = memory.brief(con, config.load())
    assert "Core memory" in brief
    assert "Operating rules" in brief
    assert "source" in brief.lower()
    # The whole point of tiering is that this stays small.
    assert len(brief) < 20000


def test_brief_surfaces_pending_proposals(brain):
    con, config, db = brain
    memory.propose(con, "core", "CORE.md", {"mode": "append", "text": "x"},
                   rationale="a change that must not be assumed accepted")
    brief = memory.brief(con, config.load())
    assert "Awaiting my approval" in brief
    assert "must not be assumed accepted" in brief


def test_session_roundtrip(brain):
    con, config, db = brain
    sid = memory.start_session(con, "class II design")
    memory.end_session(con, sid, summary="Decided to include class II epitopes",
                       open_threads="need NetMHCIIpan version check")
    recent = memory.recent_sessions(con)
    assert recent[0]["summary"].startswith("Decided")
    assert "NetMHCIIpan" in recent[0]["open_threads"]
