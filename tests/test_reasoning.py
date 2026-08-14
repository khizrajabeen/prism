"""The reasoning layer: entity graph, multi-hop retrieval, evidence grading,
and contradiction detection."""

from neobrain import evidence, graph, retrieve


def _add_paper(db, con, pid, title, abstract, source="journal", score=10):
    db.upsert_paper(con, {
        "id": pid, "source": source, "provider": "test", "title": title,
        "abstract": abstract, "authors": "", "journal": "J Test",
        "pub_date": "2026-01-01", "doi": "", "pmid": "", "pmcid": "",
        "url": f"https://example.org/{pid}", "is_oa": 1, "score": score,
        "matched": "", "buckets": "core",
    })
    con.commit()


def _chunk(con, paper_id, text, heading="Body"):
    cur = con.execute(
        """INSERT INTO chunks(paper_id, doc_kind, doc_ref, heading, ord, text, n_chars)
           VALUES (?, 'paper', NULL, ?, 0, ?, ?)""",
        (paper_id, heading, text, len(text)),
    )
    con.commit()
    return int(cur.lastrowid)


# ------------------------------------------------------------ extraction

def test_extraction_finds_domain_entities():
    found = dict((name, kind) for kind, name in graph.extract(
        "We vaccinated C57BL/6 mice bearing MC38 tumours and typed HLA-A*02:01 "
        "with OptiType, then measured responses by ELISpot."
    ))
    assert found["MC38"] == "cell_line"
    assert found["C57BL/6"] == "mouse"
    assert found["OptiType"] == "tool"
    assert found["ELISpot"] == "assay"
    assert found["HLA-A*02:01"] == "hla"


def test_extraction_normalizes_aliases():
    """'HLA LOH' and the spelled-out form must become the same node, or the
    graph fragments and no path between them exists."""
    short = {n for _, n in graph.extract("tumours showing HLA LOH escape detection")}
    long = {n for _, n in graph.extract("tumours showing HLA loss of heterozygosity")}
    assert "HLA loss of heterozygosity" in short
    assert short & long


def test_extraction_does_not_invent_mutations():
    """Regex mutation matching must not fire on 'Figure S1B' style text."""
    found = {n for _, n in graph.extract("As shown in FIGURE S1B and TABLE T2A")}
    assert not any(f in found for f in ("FIGURE S1B", "TABLE T2A"))
    real = {n for _, n in graph.extract("the KRAS G12D hotspot")}
    assert "KRAS G12D" in real


# ------------------------------------------------------------ graph search

def test_graph_leg_finds_a_passage_that_shares_no_wording(brain):
    """The multi-hop case: the question's words appear in none of the corpus."""
    con, config, db = brain
    _add_paper(db, con, "MED:ct26", "CT26 immunodominance", "x")
    _chunk(con, "MED:ct26",
           "In CT26, the endogenous retroviral antigen gp70/AH1 drives an "
           "immunodominance that masks weaker neoantigen responses entirely.")
    _add_paper(db, con, "MED:other", "Unrelated", "y")
    _chunk(con, "MED:other", "Lipid nanoparticle formulation and cold chain logistics.")
    graph.index_chunks(con)

    ranked = graph.rank_chunks(con, "CT26 AH1")
    assert ranked, "graph retrieval returned nothing for a seeded query"
    top_chunk = ranked[0][0]
    row = con.execute("SELECT paper_id FROM chunks WHERE id=?", (top_chunk,)).fetchone()
    assert row["paper_id"] == "MED:ct26"


def test_graph_path_connects_through_an_intermediate(brain):
    con, config, db = brain
    _add_paper(db, con, "MED:a", "a", "x")
    _chunk(con, "MED:a", "CT26 tumours display gp70 and AH1 as dominant antigens.")
    _add_paper(db, con, "MED:b", "b", "y")
    _chunk(con, "MED:b", "AH1 reactivity produces immunodominance over neoantigens.")
    graph.index_chunks(con)

    path = graph.explain_path(con, "CT26", "immunodominance")
    assert path is not None
    assert path[0] == "CT26" and path[-1] == "immunodominance"
    assert "AH1" in path          # the intermediate is the actual mechanism


def test_pagerank_stays_near_the_seed(brain):
    """Low damping is deliberate: activation must not drift to global hubs."""
    con, config, db = brain
    _add_paper(db, con, "MED:a", "a", "x")
    _chunk(con, "MED:a", "MC38 and Adpgk are studied together.")
    _add_paper(db, con, "MED:b", "b", "y")
    _chunk(con, "MED:b", "neoantigen appears with lipid nanoparticle and mRNA vaccine.")
    graph.index_chunks(con)

    seeds = graph.seeds_for(con, "MC38")
    scores = graph.personalized_pagerank(con, seeds)
    names = {
        con.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone()["name"]: s
        for eid, s in scores.items()
    }
    assert names["MC38"] == max(names.values())
    if "lipid nanoparticle" in names:
        assert names["lipid nanoparticle"] < names["MC38"]


# --------------------------------------------------------- evidence grading

def test_grading_separates_trials_from_preprints():
    tier_rct, label = evidence.grade(
        "A randomized controlled trial of a neoantigen vaccine",
        "We randomized 300 patients in this placebo-controlled trial.")
    assert tier_rct == 5 and "randomi" in label.lower()

    tier_pre, _ = evidence.grade("A new predictor", "We trained a model on a dataset.",
                                 source="preprint")
    assert tier_pre <= 3
    assert tier_pre < tier_rct


def test_reviews_and_editorials_are_graded_down():
    tier, label = evidence.grade("Cancer vaccines: a systematic review",
                                 "We discuss the state of the field.")
    assert tier == 0
    tier, label = evidence.grade("Promise of vaccines", "text", pub_type="Editorial")
    assert tier == 0 and label == "editorial"


def test_consensus_language_matches_the_evidence():
    assert "no evidence" in evidence.consensus_language([], 0)
    assert "established" in evidence.consensus_language([5, 4], 2)
    assert "single-paper" in evidence.consensus_language([2], 1)


def test_grading_flows_into_retrieval(brain):
    con, config, db = brain
    _add_paper(db, con, "MED:rct", "A randomized controlled trial of vaccination",
               "We randomized 300 patients with melanoma to a neoantigen vaccine.")
    _chunk(con, "MED:rct",
           "We randomized 300 patients with melanoma to receive a neoantigen vaccine "
           "or placebo, with overall survival as the primary endpoint.")
    evidence.grade_corpus(con)

    hits = retrieve.search("randomized melanoma neoantigen vaccine", cfg=config.load(), con=con)
    assert hits and hits[0].evidence_tier == 5
    assert "tier 5" in hits[0].citation()


# ----------------------------------------------------- contradiction detection

def test_contradiction_detector_flags_a_negative_result(brain):
    con, config, db = brain
    from neobrain import memory

    bid = memory.add_belief(
        con, "MC38 neoantigen vaccination improves survival",
        topic="in_vivo", sources=[{"paper_id": "MED:old"}],
    )
    _add_paper(db, con, "MED:new", "Failure to replicate MC38 vaccine benefit", "z")
    _chunk(con, "MED:new",
           "In MC38 tumours, neoantigen vaccination did not improve survival, and we "
           "were unable to confirm the previously reported benefit.")

    opened = evidence.scan(con)
    assert opened >= 1
    conflicts = evidence.open_conflicts(con)
    assert conflicts[0]["belief_id"] == bid
    assert "MC38" in conflicts[0]["cue"]


def test_agreeing_evidence_is_not_flagged(brain):
    con, config, db = brain
    from neobrain import memory

    memory.add_belief(con, "MC38 neoantigen vaccination improves survival",
                      topic="in_vivo", sources=[{"paper_id": "MED:old"}])
    _add_paper(db, con, "MED:agree", "Confirmation", "z")
    _chunk(con, "MED:agree",
           "In MC38 tumours, neoantigen vaccination improved survival substantially, "
           "consistent with earlier reports.")

    assert evidence.scan(con) == 0


def test_conflicts_can_be_dismissed(brain):
    con, config, db = brain
    from neobrain import memory

    memory.add_belief(con, "MC38 vaccination improves survival",
                      sources=[{"paper_id": "MED:old"}])
    _add_paper(db, con, "MED:new", "t", "z")
    _chunk(con, "MED:new", "In MC38, vaccination did not improve survival.")
    evidence.scan(con)

    cid = evidence.open_conflicts(con)[0]["id"]
    evidence.resolve(con, cid, "dismissed", "different dosing schedule")
    assert evidence.open_conflicts(con) == []


# --------------------------------------------------------- retrieval quality

def test_mmr_prevents_one_paper_dominating(brain):
    con, config, db = brain
    for i in range(4):
        _add_paper(db, con, "MED:same", "One paper", "x")
        _chunk(con, "MED:same", f"Neoantigen vaccination in MC38 improves survival. Variant {i}.")
    _add_paper(db, con, "MED:other", "Another paper", "y")
    _chunk(con, "MED:other", "Neoantigen vaccination in MC38 improves survival in a second cohort.")

    hits = retrieve.search("neoantigen vaccination MC38 survival", k=3,
                           cfg=config.load(), con=con)
    assert len({h.paper_id for h in hits}) > 1, "MMR failed to diversify sources"


def test_subquestion_decomposition_splits_conjunctions():
    subs = retrieve.subquestions(
        "does class II inclusion improve responses and does it change escape?"
    )
    assert len(subs) >= 3
    assert any("class II" in s for s in subs)


def test_context_pack_reports_evidence_strength_and_records_provenance(brain):
    con, config, db = brain
    _add_paper(db, con, "MED:rct", "Randomized trial of neoantigen vaccination",
               "We randomized 300 patients in a placebo-controlled trial.")
    _chunk(con, "MED:rct",
           "We randomized 300 patients to neoantigen vaccination or placebo and "
           "measured recurrence-free survival.")
    evidence.grade_corpus(con)

    pack = retrieve.context_pack("randomized neoantigen vaccination trial",
                                 cfg=config.load(), con=con)
    assert "Evidence strength:" in pack
    assert "evidence tier" in pack

    row = con.execute("SELECT question, paper_ids FROM answers ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None and "MED:rct" in row["paper_ids"]


def test_snippet_centres_on_the_match():
    hit = retrieve.Hit(
        chunk_id=1, paper_id=None, doc_kind="knowledge", doc_ref="x.md",
        heading="h", text="filler " * 100 + "the immunodominant AH1 response " + "tail " * 100,
    )
    out = hit.snippet("immunodominant AH1", width=200)
    assert "AH1" in out
    assert out.startswith("…")
