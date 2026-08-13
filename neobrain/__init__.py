"""
NeoBrain — a local research brain for neoantigen cancer vaccines and drug discovery.

Everything here is designed to run on one laptop, offline except for the
literature sweep, with an agent (Claude Code, Claude Desktop, or your own loop)
sitting on top of it through the CLI or the MCP server.

Layers, from the bottom up:

    db          SQLite: papers, full text, chunks, trials, beliefs, cards, sessions
    sources     Europe PMC, PubMed, bioRxiv/medRxiv, ClinicalTrials.gov, local PDFs
    scoring     transparent additive relevance scoring driven by config/interests.yaml
    sweep       the nightly job that fills the database
    embeddings  optional local vectors (sentence-transformers or Ollama)
    retrieve    hybrid keyword + vector retrieval with reciprocal-rank fusion
    memory      three-tier memory with an approval gate on every write
    tutor       spaced-repetition teaching built from your own corpus
    cli         the `neobrain` command
    mcp_server  the same capabilities exposed as agent tools
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
