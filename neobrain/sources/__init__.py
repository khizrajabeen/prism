"""Literature and trial sources.

Each module here exposes plain functions that return normalized dicts, so the
sweep orchestrator does not need to know anything about the upstream API
shapes. Every one of them fails soft: a network error logs and returns empty
rather than aborting the run, because a half-finished sweep is far better than
no sweep at 6am.
"""

from . import europepmc, clinicaltrials, preprints, fulltext, local  # noqa: F401

__all__ = ["europepmc", "clinicaltrials", "preprints", "fulltext", "local"]
