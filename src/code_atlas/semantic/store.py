"""Read and write the semantic layer kept in `.codemap/` (meant to be committed and reviewed).

    .codemap/config.yaml        language for written text, etc.
    .codemap/regions.yaml       region tree: id, name, role, responsibility, parent
    .codemap/assign.yaml        ordered glob rules → region (later rules win, like .gitignore)
    .codemap/notes/<shard>.jsonl  one note per line, sorted by id; regions in _regions.jsonl,
                                files and symbols sharded by their top-level directory
    .codemap/relations.jsonl    semantic relations, one per line, sorted by id
    .codemap/tours/<id>.yaml    guided tours: an ordered walk through the code with one sentence per step
    .codemap/log.jsonl          one line per applied changeset

Everything is written in a stable order so that a change shows up as a small diff.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROLES = ("interface", "orchestration", "core", "state", "integration", "presentation", "foundation", "tooling")
FAMILIES = ("control", "data", "contract", "config", "observe")
REGION_PREFIX = "region:"
SEMANTIC_FILES = ("config.yaml", "regions.yaml", "assign.yaml", "relations.jsonl", "log.jsonl")


@dataclass
class Semantic:
    config: dict = field(default_factory=lambda: {"language": "en"})
    regions: list[dict] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)
    notes: dict[str, dict] = field(default_factory=dict)
    relations: dict[str, dict] = field(default_factory=dict)
    tours: dict[str, dict] = field(default_factory=dict)

    def region(self, rid: str) -> dict | None:
        return next((r for r in self.regions if r["id"] == rid), None)


def root(repo: Path) -> Path:
    return repo / ".codemap"


def _read_yaml(path: Path, key: str) -> list | dict | None:
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    return data.get(key) if isinstance(data, dict) else None


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def load(repo: Path) -> Semantic:
    d = root(repo)
    sem = Semantic()
    cfg = d / "config.yaml"
    if cfg.exists():
        sem.config.update(yaml.safe_load(cfg.read_text("utf-8")) or {})
    sem.regions = list(_read_yaml(d / "regions.yaml", "regions") or [])
    sem.rules = list(_read_yaml(d / "assign.yaml", "rules") or [])
    for f in sorted((d / "notes").glob("*.jsonl")) if (d / "notes").exists() else []:
        for n in _read_jsonl(f):
            sem.notes[n["id"]] = n
    for r in _read_jsonl(d / "relations.jsonl"):
        sem.relations[r["id"]] = r
    if (d / "tours").exists():
        for f in sorted((d / "tours").glob("*.yaml")):
            t = yaml.safe_load(f.read_text("utf-8")) or {}
            if t.get("id"):
                sem.tours[t["id"]] = t
    return sem


def shard(node: str) -> str:
    if node.startswith(REGION_PREFIX):
        return "_regions"
    path = node.split("#", 1)[0]
    top = path.split("/", 1)[0] if "/" in path else "_root"
    return re.sub(r"[^\w.-]", "_", top) or "_root"


def _dump_yaml(obj, header: str) -> str:
    body = yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, width=110, default_flow_style=False)
    return header + body


def _jsonl(rows) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def save(repo: Path, sem: Semantic) -> None:
    d = root(repo)
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(_dump_yaml(sem.config, "# Code Atlas settings\n"), encoding="utf-8")
    (d / "regions.yaml").write_text(_dump_yaml(
        {"regions": [{k: r.get(k) for k in ("id", "name", "role", "responsibility", "parent")} for r in sem.regions]},
        "# Logical regions of this repository. Written by agents via `codeatlas apply`; review like code.\n"),
        encoding="utf-8")
    (d / "assign.yaml").write_text(_dump_yaml(
        {"rules": [{k: v for k, v in r.items() if v not in (None, [])} for r in sem.rules]},
        "# File → region rules (gitignore-style globs). Later rules win.\n"), encoding="utf-8")
    nd = d / "notes"
    nd.mkdir(exist_ok=True)
    shards: dict[str, list[dict]] = {}
    for nid in sorted(sem.notes):
        shards.setdefault(shard(nid), []).append(sem.notes[nid])
    for f in nd.glob("*.jsonl"):
        if f.stem not in shards:
            f.unlink()
    for name, rows in shards.items():
        (nd / f"{name}.jsonl").write_text(_jsonl(rows), encoding="utf-8")
    (d / "relations.jsonl").write_text(_jsonl(sem.relations[k] for k in sorted(sem.relations)), encoding="utf-8")
    td = d / "tours"
    if sem.tours or td.exists():
        td.mkdir(exist_ok=True)
        for f in td.glob("*.yaml"):
            if f.stem not in sem.tours:
                f.unlink()
        for tid, t in sem.tours.items():
            body = {k: t[k] for k in ("id", "title", "summary", "run", "at") if t.get(k) is not None}
            body["steps"] = [{k: s[k] for k in ("at", "text", "via_relation", "anchor") if s.get(k) is not None}
                             for s in t.get("steps", [])]
            header = "# A guided tour through the code. Written via `codeatlas apply`.\n"
            (td / f"{tid}.yaml").write_text(_dump_yaml(body, header), encoding="utf-8")


def append_log(repo: Path, entry: dict) -> None:
    with open(root(repo) / "log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def read_log(repo: Path) -> list[dict]:
    return _read_jsonl(root(repo) / "log.jsonl")


def snapshot(repo: Path) -> dict[str, str]:
    """All semantic files as text (for run revert)."""
    d = root(repo)
    out = {}
    for name in SEMANTIC_FILES:
        if (d / name).exists():
            out[name] = (d / name).read_text("utf-8")
    for sub, pattern in (("notes", "*.jsonl"), ("tours", "*.yaml")):
        if (d / sub).exists():
            for f in (d / sub).glob(pattern):
                out[f"{sub}/{f.name}"] = f.read_text("utf-8")
    return out


def restore(repo: Path, files: dict[str, str]) -> None:
    d = root(repo)
    current = snapshot(repo)
    for name in current:
        if name not in files and name != "log.jsonl":
            (d / name).unlink()
    for name, text in files.items():
        if name == "log.jsonl":
            continue
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")
