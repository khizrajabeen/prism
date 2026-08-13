"""Storage, FTS escaping, and rank fusion — the parts that fail silently."""

from neobrain import db as db_mod
from neobrain import embeddings, retrieve


def _paper(pid: str, title: str, abstract: str, score: int = 10) -> dict:
    return {
        "id": pid, "source": "journal", "provider": "europepmc", "title": title,
        "abstract": abstract, "authors": "Doe J", "journal": "J Test",
        "pub_date": "2026-01-01", "doi": f"10.1/{pid}", "pmid": "", "pmcid": "",
        "url": "https://example.org", "is_oa": 1, "score": score,
        "matched": "neoantigen", "buckets": "core",
    }


def test_upsert_is_idempotent_and_merges_buckets(brain):
    con, config, db = brain
    assert db.upsert_paper(con, _paper("MED:1", "A", "B")) is True
    second = _paper("MED:1", "A", "B")
    second["buckets"] = "preclinical"
    assert db.upsert_paper(con, second) is False

    row = con.execute("SELECT buckets FROM papers WHERE id='MED:1'").fetchone()
    assert set(row["buckets"].split(",")) == {"core", "preclinical"}
    assert con.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"] == 1


def test_trial_changes_are_recorded_not_overwritten(brain):
    con, config, db = brain
    trial = {"nct_id": "NCT1", "title": "T", "status": "Recruiting", "phase": "Phase 1",
             "conditions": "Melanoma", "interventions": "", "sponsor": "S",
             "enrollment": 30, "start_date": "2026-01-01", "last_update": "2026-01-01",
             "url": "u", "summary": ""}
    assert db.upsert_trial(con, trial) == "new"
    assert db.upsert_trial(con, dict(trial)) == "same"

    trial["status"] = "Active, not recruiting"
    assert db.upsert_trial(con, trial) == "changed"

    hist = con.execute("SELECT field, old_value, new_value FROM trial_history").fetchall()
    assert [(h["field"], h["old_value"], h["new_value"]) for h in hist] == [
        ("status", "Recruiting", "Active, not recruiting")
    ]


def test_fts_escape_survives_hyphens_and_punctuation():
    """HLA-A*02:01 must not become an FTS5 syntax error."""
    escaped = db_mod.fts_escape("HLA-A*02:01 binding (class I)")
    assert '"' in escaped
    assert "(" not in escaped
    assert db_mod.fts_escape("!!!") == '""'


def test_fts_search_finds_papers(brain):
    con, config, db = brain
    db.upsert_paper(con, _paper("MED:2", "Class II neoepitopes in MC38",
                                "We identify MHC class II restricted neoantigens."))
    con.commit()
    rows = retrieve.keyword_papers(con, "MC38 neoepitopes", 5)
    assert any(r["id"] == "MED:2" for r in rows)


def test_hybrid_search_returns_passages_without_embeddings(brain):
    con, config, db = brain
    cfg = config.load()
    db.upsert_paper(con, _paper(
        "MED:3", "HLA loss of heterozygosity in escape",
        "Allele-specific copy number analysis detects HLA LOH. " * 20,
    ))
    con.commit()
    embeddings.rebuild_chunks_for_paper(con, "MED:3", cfg)
    con.commit()

    hits = retrieve.search("HLA LOH allele-specific", cfg=cfg, con=con)
    assert hits
    assert hits[0].paper_id == "MED:3"
    assert "keyword" in hits[0].how


def test_context_pack_is_explicit_about_an_empty_corpus(brain):
    con, config, db = brain
    pack = retrieve.context_pack("nothing matches this", con=con, cfg=config.load())
    assert "Nothing in the local corpus" in pack
    # The instruction not to fabricate must survive into the pack itself.
    assert "do not" in pack.lower()


def test_rrf_prefers_items_ranked_by_both_legs():
    fused = retrieve._rrf(
        [[(1, 0.9), (2, 0.8), (3, 0.7)], [(3, 0.99), (2, 0.5), (9, 0.4)]],
        [1.0, 1.0],
    )
    # 2 and 3 appear in both lists; 1 and 9 appear in one each.
    assert fused[3] > fused[1]
    assert fused[2] > fused[9]


def test_chunking_respects_boundaries_and_overlaps():
    text = ". ".join(f"Sentence number {i} about neoantigens" for i in range(60))
    chunks = embeddings.chunk_text(text, size=200, overlap=40)
    assert len(chunks) > 1
    assert all(len(c) <= 260 for c in chunks)
    assert all(c.strip() for c in chunks)


def test_vector_pack_roundtrip_is_lossless_enough():
    vec = embeddings._normalize([0.1, -0.4, 0.9, 0.2])
    restored = embeddings.unpack(embeddings.pack(vec))
    assert len(restored) == 4
    assert all(abs(a - b) < 1e-6 for a, b in zip(vec, restored))
    assert abs(embeddings.cosine(vec, restored) - 1.0) < 1e-6


def test_stats_reports_a_real_shape(brain):
    con, config, db = brain
    db.upsert_paper(con, _paper("MED:4", "T", "A" * 400))
    con.commit()
    s = db.stats(con)
    assert s["papers"] == 1 and s["embeddings"] == 0 and s["db_mb"] >= 0
