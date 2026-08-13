"""SM-2 scheduling. Getting this wrong makes the deck useless without any
visible error, so the arithmetic is pinned down here."""

from neobrain import tutor


def test_add_card_is_deduplicated(brain):
    con, config, db = brain
    a = tutor.add_card(con, "What is DAI?", "Differential agretopicity index", topic="ranking")
    b = tutor.add_card(con, "What is DAI?", "Something else", topic="ranking")
    assert a == b
    assert con.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"] == 1


def test_successful_reviews_lengthen_the_interval(brain):
    con, config, db = brain
    cid = tutor.add_card(con, "q", "a")
    first = tutor.grade_card(con, cid, 5)
    second = tutor.grade_card(con, cid, 5)
    third = tutor.grade_card(con, cid, 5)
    assert first["interval"] == 1
    assert second["interval"] == 6
    assert third["interval"] > second["interval"]


def test_a_lapse_resets_the_interval_and_lowers_ease(brain):
    con, config, db = brain
    cid = tutor.add_card(con, "q", "a")
    tutor.grade_card(con, cid, 5)
    tutor.grade_card(con, cid, 5)
    good = con.execute("SELECT ease FROM cards WHERE id=?", (cid,)).fetchone()["ease"]

    lapsed = tutor.grade_card(con, cid, 1)
    assert lapsed["interval"] == 1
    assert lapsed["lapses"] == 1
    assert lapsed["ease"] < good


def test_ease_has_a_floor(brain):
    con, config, db = brain
    cid = tutor.add_card(con, "q", "a")
    for _ in range(20):
        result = tutor.grade_card(con, cid, 0)
    assert result["ease"] >= 1.3


def test_due_cards_excludes_the_scheduled_future(brain):
    con, config, db = brain
    cid = tutor.add_card(con, "q", "a")
    assert len(tutor.due_cards(con)) == 1
    tutor.grade_card(con, cid, 5)      # now due tomorrow
    assert tutor.due_cards(con) == []


def test_deck_stats_group_by_topic(brain):
    con, config, db = brain
    tutor.add_card(con, "q1", "a", topic="ranking")
    tutor.add_card(con, "q2", "a", topic="ranking")
    tutor.add_card(con, "q3", "a", topic="in_vivo")
    stats = tutor.deck_stats(con)
    assert stats["total"] == 3
    assert {r["topic"] for r in stats["by_topic"]} == {"ranking", "in_vivo"}


def test_lesson_plan_flags_an_empty_corpus(brain):
    con, config, db = brain
    packet = tutor.lesson_plan("HLA loss of heterozygosity", con=con, cfg=config.load())
    assert "Nothing in the corpus" in packet
    assert "How to teach this" in packet
