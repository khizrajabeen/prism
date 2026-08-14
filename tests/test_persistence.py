"""The "never forget" guarantees.

These are the tests that matter most for the user's actual requirement: once
something is learned, it must survive. Two mechanisms carry that weight — an
append-only journal that the database itself refuses to mutate, and beliefs
that are versioned rather than overwritten.
"""

import sqlite3

import pytest

from neobrain import journal, memory


# ------------------------------------------------------------ immutability

def test_journal_entries_cannot_be_edited(brain):
    """Immutability is enforced by the storage layer, not by convention."""
    con, config, db = brain
    jid = journal.record(con, "Montanide sequesters T cells at the injection site",
                         kind="preference", topic="adjuvants")

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("UPDATE journal SET text='something else' WHERE id=?", (jid,))
        con.commit()

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute("DELETE FROM journal WHERE id=?", (jid,))
        con.commit()

    con.rollback()
    row = con.execute("SELECT text FROM journal WHERE id=?", (jid,)).fetchone()
    assert row["text"].startswith("Montanide")


def test_journal_is_searchable_from_the_first_entry(brain):
    con, config, db = brain
    journal.record(con, "I do not have access to a BSL-2 facility this year",
                   kind="preference", topic="lab")
    journal.record(con, "Decided against CT26 because of the AH1 confound",
                   kind="decision", topic="models")

    hits = journal.recall(con, "AH1 confound")
    assert len(hits) == 1
    assert "CT26" in hits[0]["text"]

    prefs = journal.recall(con, "", kind="preference")
    assert len(prefs) == 1 and "BSL-2" in prefs[0]["text"]


def test_corrections_surface_in_the_brief(brain):
    """A correction the user made must not need to be made twice."""
    con, config, db = brain
    journal.record(con, "You said B16F10 is high-TMB. It is not — it is poorly "
                        "immunogenic with low MHC-I.",
                   kind="correction", topic="models", importance=4)

    brief = memory.brief(con, config.load())
    assert "B16F10" in brief
    assert "correction" in brief.lower()


def test_empty_journal_entries_are_refused(brain):
    con, config, db = brain
    with pytest.raises(ValueError):
        journal.record(con, "   ")


# ------------------------------------------------------- bitemporal beliefs

def test_revision_keeps_the_old_version(brain):
    con, config, db = brain
    first = memory.add_belief(
        con, "Class II epitopes contribute little to vaccine responses",
        confidence="moderate", topic="class II",
        sources=[{"paper_id": "MED:1"}],
    )
    second = memory.revise_belief(
        con, first,
        "Class II epitopes contribute substantially to vaccine responses",
        confidence="moderate",
        sources=[{"paper_id": "MED:2"}],
        reason="two 2026 cohorts report higher class II frequencies",
    )

    active = memory.get_beliefs(con)
    assert [b["id"] for b in active] == [second]

    history = memory.belief_history(con, second)
    assert len(history) == 2
    assert history[0]["id"] == first
    assert history[0]["status"] == "superseded"
    assert history[0]["invalidated_at"]           # we know when we stopped believing it
    assert history[1]["version"] == 2
    # The revision inherits the original evidence as well as the new.
    assert {s["paper_id"] for s in memory.get_beliefs(con)[0]["sources"]} == {"MED:1", "MED:2"}


def test_as_of_reconstructs_a_past_state_of_knowledge(brain):
    con, config, db = brain
    old = memory.add_belief(con, "an early claim", sources=[{"citation": "x"}])
    con.execute(
        "UPDATE beliefs SET asserted_at='2026-01-01T00:00:00' WHERE id=?", (old,)
    )
    con.commit()

    new = memory.revise_belief(con, old, "a corrected claim",
                               sources=[{"citation": "y"}], reason="new data")
    con.execute(
        "UPDATE beliefs SET invalidated_at='2026-06-01T00:00:00' WHERE id=?", (old,)
    )
    con.execute(
        "UPDATE beliefs SET asserted_at='2026-06-01T00:00:00' WHERE id=?", (new,)
    )
    con.commit()

    march = memory.as_of(con, "2026-03-01T00:00:00")
    assert [b["claim"] for b in march] == ["an early claim"]

    july = memory.as_of(con, "2026-07-01T00:00:00")
    assert [b["claim"] for b in july] == ["a corrected claim"]


def test_revision_still_requires_a_source(brain):
    con, config, db = brain
    bid = memory.add_belief(con, "a claim", sources=[{"citation": "x"}])
    # Inherited sources satisfy the requirement; a belief can never end up bare.
    new = memory.revise_belief(con, bid, "a revised claim", reason="rethink")
    assert memory.get_beliefs(con)[0]["sources"]
    assert new != bid


def test_belief_changes_are_journalled(brain):
    con, config, db = brain
    bid = memory.add_belief(con, "a claim", sources=[{"citation": "x"}])
    memory.revise_belief(con, bid, "a better claim", reason="new evidence")

    entries = journal.recall(con, "", limit=50)
    kinds = {e["kind"] for e in entries}
    assert "decision" in kinds      # the belief was recorded
    assert "correction" in kinds    # and later superseded


# ------------------------------------------------------- procedural memory

def test_rules_are_deduplicated_and_surface_in_the_brief(brain):
    con, config, db = brain
    a = memory.add_rule(con, "I design a mouse vaccine study",
                        "check for an adjuvant-alone arm", scope="in_vivo")
    b = memory.add_rule(con, "I design a mouse vaccine study",
                        "check for an adjuvant-alone arm", scope="in_vivo")
    assert a == b

    brief = memory.brief(con, config.load())
    assert "adjuvant-alone arm" in brief
    assert "procedural memory" in brief.lower()


def test_retired_rules_stop_being_offered_but_are_kept(brain):
    con, config, db = brain
    rid = memory.add_rule(con, "trigger", "action")
    memory.retire_rule(con, rid, "no longer relevant")

    assert memory.get_rules(con) == []
    assert len(memory.get_rules(con, active_only=False)) == 1


def test_rule_proposal_flows_through_the_approval_gate(brain):
    con, config, db = brain
    pid = memory.propose(
        con, "rule", "new",
        {"trigger": "I cite a clinical result", "action": "check the trials table first",
         "scope": "clinical"},
        rationale="I quoted a stale trial status last week",
    )
    assert memory.get_rules(con) == []      # nothing applied yet
    memory.apply_proposal(con, pid)
    assert len(memory.get_rules(con)) == 1
