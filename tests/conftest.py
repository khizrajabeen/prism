"""Test fixtures.

Every test runs against a throwaway NEOBRAIN_HOME so nothing touches a real
brain. The env var is set before `neobrain.config` is imported, because module
import is when paths are resolved.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    """A fully initialized, empty brain in a temp directory."""
    home = tmp_path / "brain_home"
    (home / "config").mkdir(parents=True)
    (home / "knowledge").mkdir()
    (home / "memory").mkdir()
    monkeypatch.setenv("NEOBRAIN_HOME", str(home))

    # Re-import so module-level path constants pick up the new home.
    import neobrain.config as config
    importlib.reload(config)
    for name in ("neobrain.db", "neobrain.digest", "neobrain.memory",
                 "neobrain.embeddings", "neobrain.retrieve", "neobrain.tutor",
                 "neobrain.scoring"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])

    import neobrain.db as db
    importlib.reload(db)

    config.ensure_dirs()
    (config.CORE_MEMORY).write_text("# CORE memory\n\nTest brain.\n", encoding="utf-8")
    (config.SETTINGS).write_text("embeddings:\n  backend: none\n", encoding="utf-8")
    (config.INTERESTS).write_text(
        "lookback_days: 7\n"
        "queries:\n  core:\n    - 'neoantigen'\n"
        "boost_terms:\n  neoantigen: 5\n  MC38: 3\n"
        "penalty_terms:\n  editorial: -5\n"
        "score_threshold: 6\n"
        "trial_terms:\n  - neoantigen vaccine\n",
        encoding="utf-8",
    )

    con = db.connect()
    yield con, config, db
    con.close()
