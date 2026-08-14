"""The biology model registry.

Answers three questions that otherwise cost an afternoon each:

1. **What exists** for a given task, and what is the honest state of the art?
2. **Can I actually run it here** — is the package installed, is there a GPU,
   is the licence compatible with how my work is funded?
3. **When should I not use it?** Every entry carries a caveat, because in this
   field the difference between a useful prediction and a misleading one is
   almost always a scope condition someone ignored.

The registry itself is `config/models.yaml` — data, not code, so you can extend
it without touching this module. Availability detection is real: it imports the
module or looks for the binary, so `--available` tells you what is genuinely
runnable on this machine rather than what is theoretically possible.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass
from typing import Any

from . import config

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

REGISTRY_PATH = config.CONFIG_DIR / "models.yaml"


@dataclass
class Model:
    id: str
    name: str
    task: str
    summary: str = ""
    modality: str = ""
    license: str = ""
    weights: str = ""
    check: str = ""
    hardware: str = ""
    install: str = ""
    use_when: str = ""
    caveat: str = ""
    see_also: list[str] | None = None

    @property
    def available(self) -> bool:
        return check_available(self.check)

    @property
    def availability_note(self) -> str:
        kind, _, target = (self.check or "").partition(":")
        if kind == "web":
            return "hosted only — no local inference"
        if not target:
            return "unknown"
        if self.available:
            return f"installed ({kind}:{target})"
        hint = f" — {self.install}" if self.install else ""
        return f"not installed ({kind}:{target}){hint}"

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id, "name": self.name, "task": self.task,
            "modality": self.modality, "summary": self.summary,
            "license": self.license, "weights": self.weights,
            "hardware": self.hardware, "install": self.install,
            "use_when": self.use_when, "caveat": self.caveat,
            "see_also": self.see_also or [],
            "available": self.available,
            "availability": self.availability_note,
        }
        return d


def check_available(check: str) -> bool:
    """Is this model runnable on this machine right now?"""
    kind, _, target = (check or "").partition(":")
    if kind == "python" and target:
        try:
            return importlib.util.find_spec(target) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            return False
    if kind == "cli" and target:
        return shutil.which(target) is not None
    return False


def _load() -> dict[str, Any]:
    if yaml is None or not REGISTRY_PATH.exists():
        return {}
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}


def all_models() -> list[Model]:
    data = _load()
    out = []
    for entry in data.get("models", []) or []:
        out.append(Model(
            id=entry.get("id", ""), name=entry.get("name", ""),
            task=entry.get("task", ""), summary=(entry.get("summary") or "").strip(),
            modality=entry.get("modality", ""), license=entry.get("license", ""),
            weights=entry.get("weights", ""), check=entry.get("check", ""),
            hardware=entry.get("hardware", ""), install=entry.get("install", ""),
            use_when=(entry.get("use_when") or "").strip(),
            caveat=(entry.get("caveat") or "").strip(),
            see_also=entry.get("see_also") or [],
        ))
    return out


def tasks() -> list[str]:
    return _load().get("tasks", []) or sorted({m.task for m in all_models()})


def guidance(task: str | None = None) -> dict[str, str] | str:
    g = {k: (v or "").strip() for k, v in (_load().get("guidance", {}) or {}).items()}
    if task:
        return g.get(task, "")
    return g


def find(query: str) -> list[Model]:
    """Search by id, name, task, or free text across the entry."""
    q = query.lower().strip()
    if not q:
        return all_models()
    exact = [m for m in all_models() if m.id.lower() == q or m.name.lower() == q]
    if exact:
        return exact
    return [
        m for m in all_models()
        if q in m.id.lower() or q in m.name.lower() or q in m.task.lower()
        or q in m.summary.lower() or q in m.use_when.lower() or q in m.modality.lower()
    ]


def for_task(task: str, *, available_only: bool = False) -> list[Model]:
    out = [m for m in all_models() if m.task == task]
    if available_only:
        out = [m for m in out if m.available]
    return out


def recommend(task: str) -> dict[str, Any]:
    """A ranked suggestion for a task, with the caveat attached.

    Ranking prefers models that are (a) installed here, (b) openly licensed,
    because a model you cannot run or cannot legally use is not a
    recommendation — it is trivia.
    """
    candidates = for_task(task)
    if not candidates:
        return {
            "task": task,
            "error": f"no models registered for task {task!r}",
            "known_tasks": tasks(),
        }

    def rank(m: Model) -> tuple:
        open_weights = "open" in (m.weights or "").lower() and "restricted" not in (m.weights or "").lower()
        permissive = any(w in (m.license or "").lower() for w in ("mit", "apache", "bsd"))
        return (m.available, open_weights, permissive)

    ordered = sorted(candidates, key=rank, reverse=True)
    return {
        "task": task,
        "guidance": guidance(task),
        "recommended": ordered[0].to_dict(),
        "alternatives": [m.to_dict() for m in ordered[1:]],
    }


def environment() -> dict[str, Any]:
    """What this machine can actually run."""
    gpu = False
    vram = None
    try:  # torch is not a dependency; this is best-effort
        import torch  # type: ignore

        gpu = bool(torch.cuda.is_available())
        if gpu:
            vram = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
    except Exception:
        pass

    models = all_models()
    return {
        "gpu": gpu,
        "vram_gb": vram,
        "installed": [m.id for m in models if m.available],
        "registered": len(models),
        "note": (
            "No GPU detected — CPU-only. MHCflurry, NetMHCpan and the peptide "
            "tooling all run fine; structure models and the larger protein "
            "language models will not."
            if not gpu else
            f"GPU detected ({vram} GB). Structure prediction and PLM embeddings are feasible."
        ),
    }


def format_model(m: Model, *, verbose: bool = False) -> str:
    mark = "✓" if m.available else " "
    lines = [f"{mark} {m.name}  [{m.task}]"]
    if m.summary:
        lines.append(f"    {m.summary}")
    lines.append(f"    licence: {m.license or '?'} · weights: {m.weights or '?'} "
                 f"· {m.availability_note}")
    if verbose:
        if m.hardware:
            lines.append(f"    hardware: {m.hardware}")
        if m.use_when:
            lines.append(f"    use when: {m.use_when}")
        if m.caveat:
            lines.append(f"    CAVEAT:   {m.caveat}")
        if m.see_also:
            lines.append(f"    see also: {', '.join(m.see_also)}")
    return "\n".join(lines)
