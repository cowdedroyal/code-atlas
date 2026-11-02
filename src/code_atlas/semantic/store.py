"""Read and write the semantic layer kept in `.codemap/` (meant to be committed and reviewed).

    .codemap/config.yaml        language for written text, etc.
    .codemap/regions.yaml       region tree: id, name, role, responsibility, parent
    .codemap/assign.yaml        ordered glob rules → region (later rules win, like .gitignore)
    .codemap/notes/<shard>.jsonl  one note per line, sorted by id; regions in _regions.jsonl,
                                files and symbols sharded by their top-level directory
    .codemap/relations.jsonl    semantic relations, one per line, sorted by id
    .codemap/tours/<id>.yaml    guided tours: an ordered walk through the code with one sentence per step
    .codemap/explainers/<id>/   explainer cards: card.yaml + index.html (see explainers.py)
    .codemap/log.jsonl          one line per applied changeset

Everything is written in a stable order so that a change shows up as a small diff.
"""

from __future__ import annotations

import base64
import contextlib
import errno
import functools
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROLES = ("interface", "orchestration", "core", "state", "integration", "presentation", "foundation", "tooling")
FAMILIES = ("control", "data", "contract", "config", "observe")
REGION_PREFIX = "region:"
SEMANTIC_FILES = ("config.yaml", "regions.yaml", "assign.yaml", "relations.jsonl", "log.jsonl")
BLOB_RE = re.compile(r"^[0-9a-f]{64}$")
LOCK_TIMEOUT = 10.0
_lock_state = threading.local()


class WriteLocked(Exception):
    """Another process is writing the semantic layer."""


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


def _lock_file(fh) -> None:
    fh.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fh) -> None:
    fh.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def write_lock(repo: Path):
    """One exclusive writer at a time; the OS releases the lock if the process dies.

    Reentrant on the calling thread, so a command that already holds the lock can call another writer safely.
    """
    held = _lock_state.held = getattr(_lock_state, "held", {})
    key = str(root(repo).resolve())
    if key in held:
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    d = root(repo) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "write.lock"
    fd = os.open(p, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "r+b") as fh:
        if os.fstat(fd).st_size == 0:  # lock a real byte so msvcrt has something to lock past EOF
            fh.write(b"\0")
            fh.flush()
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                _lock_file(fh)
                break
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
                if time.monotonic() >= deadline:
                    raise WriteLocked("another codeatlas write is in progress") from None
                time.sleep(0.05)
        held[key] = 1
        try:
            yield
        finally:
            held.pop(key, None)
            try:
                _unlock_file(fh)
            except OSError:
                pass


def locked_write(fn):
    """Run a semantic-layer writer under `write_lock`; contention becomes the usual JSON error report."""
    @functools.wraps(fn)
    def wrapper(repo, *args, **kwargs):
        try:
            with write_lock(repo):
                return fn(repo, *args, **kwargs)
        except WriteLocked as e:
            return {"ok": False, "errors": [str(e)], "warnings": []}
    return wrapper


def _unlink(path: Path) -> None:
    for attempt in range(6):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.05)


def write_atomic(path: Path, data: str | bytes, newline: str | None = None) -> None:
    """Write through a sibling temp file + os.replace, so a reader never sees a truncated file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        if isinstance(data, bytes):
            tmp.write_bytes(data)
        else:
            tmp.write_text(data, encoding="utf-8", newline=newline)
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


def _safe_name(d: Path, name: object) -> Path:
    """A snapshot key is data from disk: refuse anything that would escape `.codemap`."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"invalid snapshot path {name!r}")
    p = d / name
    if not p.resolve().is_relative_to(d.resolve()):
        raise ValueError(f"snapshot path escapes {d.name}: {name!r}")
    return p


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
    write_atomic(d / "config.yaml", _dump_yaml(sem.config, "# Code Atlas settings\n"))
    write_atomic(d / "regions.yaml", _dump_yaml(
        {"regions": [{k: r.get(k) for k in ("id", "name", "role", "responsibility", "parent")} for r in sem.regions]},
        "# Logical regions of this repository. Written by agents via `codeatlas apply`; review like code.\n"))
    write_atomic(d / "assign.yaml", _dump_yaml(
        {"rules": [{k: v for k, v in r.items() if v not in (None, [])} for r in sem.rules]},
        "# File → region rules (gitignore-style globs). Later rules win.\n"))
    nd = d / "notes"
    nd.mkdir(exist_ok=True)
    shards: dict[str, list[dict]] = {}
    for nid in sorted(sem.notes):
        shards.setdefault(shard(nid), []).append(sem.notes[nid])
    for f in nd.glob("*.jsonl"):
        if f.stem not in shards:
            _unlink(f)
    for name, rows in shards.items():
        write_atomic(nd / f"{name}.jsonl", _jsonl(rows))
    write_atomic(d / "relations.jsonl", _jsonl(sem.relations[k] for k in sorted(sem.relations)))
    td = d / "tours"
    if sem.tours or td.exists():
        td.mkdir(exist_ok=True)
        for f in td.glob("*.yaml"):
            if f.stem not in sem.tours:
                _unlink(f)
        for tid, t in sem.tours.items():
            body = {k: t[k] for k in ("id", "title", "summary", "run", "at") if t.get(k) is not None}
            body["steps"] = [{k: s[k] for k in ("at", "text", "via_relation", "anchor", "card") if s.get(k) is not None}
                             for s in t.get("steps", [])]
            header = "# A guided tour through the code. Written via `codeatlas apply`.\n"
            write_atomic(td / f"{tid}.yaml", _dump_yaml(body, header))


def append_log(repo: Path, entry: dict) -> None:
    with open(root(repo) / "log.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def read_log(repo: Path) -> list[dict]:
    return _read_jsonl(root(repo) / "log.jsonl")


def _blob_dir(repo: Path) -> Path:
    return root(repo) / "cache" / "runs" / "blobs"


def _blob_path(repo: Path, digest: object) -> Path:
    if not isinstance(digest, str) or not BLOB_RE.match(digest):
        raise ValueError(f"invalid snapshot blob reference {digest!r}")
    return _blob_dir(repo) / digest


def _blob(repo: Path, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    p = _blob_path(repo, digest)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(p, data)
    return digest


def _snapshot_value(f: Path, repo: Path) -> str | dict:
    """Text when the bytes decode as UTF-8, else a content-addressed blob (written once, so runs only reference it)."""
    data = f.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return {"blob": _blob(repo, data)}


def snapshot(repo: Path) -> dict[str, str | dict]:
    """All semantic files, down to nested card assets such as `media/scenes/<name>.yaml`."""
    d = root(repo)
    out = {}
    for name in SEMANTIC_FILES:
        if (d / name).exists():
            out[name] = (d / name).read_text("utf-8")
    for sub, pattern in (("notes", "*.jsonl"), ("tours", "*.yaml")):
        if (d / sub).exists():
            for f in (d / sub).glob(pattern):
                if f.is_file():
                    out[f.relative_to(d).as_posix()] = f.read_text("utf-8")
    if (d / "explainers").exists():
        for f in (d / "explainers").rglob("*"):
            if f.is_file() and not f.is_symlink():
                out[f.relative_to(d).as_posix()] = _snapshot_value(f, repo)
    return out


def fingerprint(files: dict[str, str | dict]) -> dict[str, str]:
    """Format-independent identity, so a run file written with base64 envelopes still compares equal to blobs."""
    out = {}
    for name, value in files.items():
        if isinstance(value, dict):
            if "blob" in value:
                out[name] = str(value["blob"])
            elif value.get("encoding") == "base64":
                out[name] = hashlib.sha256(base64.b64decode(value["data"], validate=True)).hexdigest()
            else:
                raise ValueError(f"unsupported snapshot encoding for {name}")
        else:
            out[name] = value
    return out


def restore(repo: Path, files: dict[str, str | dict]) -> None:
    d = root(repo)
    targets: dict[str, Path] = {}
    for name, text in files.items():
        if name == "log.jsonl":
            continue
        targets[name] = _safe_name(d, name)  # validate the whole snapshot before touching anything
        if isinstance(text, dict):
            if "blob" in text:
                _blob_path(repo, text["blob"])
            elif text.get("encoding") != "base64":
                raise ValueError(f"unsupported snapshot encoding for {name}")
        elif not isinstance(text, str):
            raise ValueError(f"unsupported snapshot value for {name}")
    current = snapshot(repo)
    for name in current:
        if name not in files and name != "log.jsonl":
            _unlink(_safe_name(d, name))
    for name, p in targets.items():
        text = files[name]
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, dict):
            if "blob" in text:
                write_atomic(p, _blob_path(repo, text["blob"]).read_bytes())
            else:
                write_atomic(p, base64.b64decode(text["data"], validate=True))
        else:
            write_atomic(p, text, newline="\n" if name.startswith("explainers/") else None)
    cards = d / "explainers"
    for sub in sorted(cards.rglob("*"), key=lambda p: len(p.parts), reverse=True) if cards.exists() else []:
        if sub.is_dir() and not any(sub.iterdir()):
            sub.rmdir()
