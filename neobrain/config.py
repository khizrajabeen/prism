"""Paths and configuration.

NeoBrain keeps all state inside one directory so it is trivially backed up,
version-controlled, and deleted. Resolution order for that directory:

1. ``$NEOBRAIN_HOME`` if set
2. the repository root (the parent of this package) if it has a ``config/`` dir
3. ``~/.neobrain``
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - dependency check lives in cli
    yaml = None


# --------------------------------------------------------------------- paths

def _resolve_home() -> Path:
    env = os.environ.get("NEOBRAIN_HOME")
    if env:
        return Path(env).expanduser().resolve()
    repo_root = Path(__file__).resolve().parent.parent
    if (repo_root / "config").is_dir():
        return repo_root
    return Path.home() / ".neobrain"


HOME = _resolve_home()

DB_PATH = HOME / "brain.db"
CONFIG_DIR = HOME / "config"
INTERESTS = CONFIG_DIR / "interests.yaml"
SETTINGS = CONFIG_DIR / "settings.yaml"
MEMORY_DIR = HOME / "memory"
CORE_MEMORY = MEMORY_DIR / "CORE.md"
KNOWLEDGE_DIR = HOME / "knowledge"
DIGEST_DIR = HOME / "digests"
INBOX_DIR = HOME / "inbox"          # drop PDFs here; `neobrain ingest-pdf --inbox`
WORKSPACE_DIR = HOME / "workspace"  # the only place the agent should write freely
LOG_DIR = HOME / "logs"

ALL_DIRS = [
    CONFIG_DIR, MEMORY_DIR, KNOWLEDGE_DIR, DIGEST_DIR,
    INBOX_DIR, WORKSPACE_DIR, LOG_DIR,
]

USER_AGENT = (
    "neobrain/1.0 (personal research assistant; "
    "https://github.com/khizrajabeen/prism; mailto:neobrain-user@localhost)"
)


def ensure_dirs() -> None:
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ defaults

DEFAULT_SETTINGS: dict[str, Any] = {
    "embeddings": {
        # "none" | "sentence-transformers" | "ollama"
        "backend": "none",
        "model": "sentence-transformers/all-MiniLM-L6-v2",
        "ollama_url": "http://localhost:11434",
        "batch_size": 32,
        "chunk_chars": 1400,
        "chunk_overlap": 200,
    },
    "retrieval": {
        "k": 12,
        "rrf_k": 60,          # reciprocal-rank-fusion damping constant
        "keyword_weight": 1.0,
        "vector_weight": 1.0,
        "graph_weight": 0.8,   # entity-graph leg; see config/settings.yaml
        "snippet_chars": 420,
    },
    "sweep": {
        "sources": ["europepmc", "clinicaltrials", "preprints"],
        "max_per_query": 300,
        "polite_delay": 0.34,   # seconds between API calls
        "timeout": 30,
        "fetch_fulltext": True,
        "fulltext_top_n": 15,   # only pull OA full text for the best new papers
        "fulltext_min_score": 8,
    },
    "digest": {
        "max_per_bucket": 15,
        "include_below_threshold_count": True,
    },
    "memory": {
        # Nothing is ever written to CORE.md or knowledge/ without going
        # through the proposals table first. This is the drift guard.
        "require_approval_for_writes": True,
        "core_soft_token_cap": 2000,
        "belief_review_days": 120,
    },
    "tutor": {
        "new_cards_per_day": 8,
        "max_reviews_per_day": 60,
    },
    "network": {
        # Advisory allowlist. `neobrain doctor` checks it; the sandbox docs
        # explain how to enforce it at the OS level.
        "allow": [
            "www.ebi.ac.uk",
            "europepmc.org",
            "eutils.ncbi.nlm.nih.gov",
            "clinicaltrials.gov",
            "api.biorxiv.org",
            "api.crossref.org",
            "api.openalex.org",
        ],
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("pyyaml is not installed — run: pip install -r requirements.txt")
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@dataclass
class Config:
    settings: dict = field(default_factory=dict)
    interests: dict = field(default_factory=dict)

    # -- convenience accessors -------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.settings
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def boosts(self) -> dict[str, int]:
        return self.interests.get("boost_terms", {}) or {}

    @property
    def penalties(self) -> dict[str, int]:
        return self.interests.get("penalty_terms", {}) or {}

    @property
    def threshold(self) -> int:
        return int(self.interests.get("score_threshold", 5))

    @property
    def lookback_days(self) -> int:
        return int(self.interests.get("lookback_days", 7))

    @property
    def queries(self) -> dict[str, list[str]]:
        return self.interests.get("queries", {}) or {}

    @property
    def trial_terms(self) -> list[str]:
        return self.interests.get("trial_terms", []) or []


def load() -> Config:
    return Config(
        settings=_deep_merge(DEFAULT_SETTINGS, _load_yaml(SETTINGS)),
        interests=_load_yaml(INTERESTS),
    )
