"""`codeatlas scan`: discover → extract (cached by content hash, in parallel) → resolve → write index.

Parsing is the only expensive step and it is incremental: a file is re-parsed only when its content
hash or the extractor version changes. Resolution always runs over the whole repository, so an
incremental scan produces exactly the same index as a full one.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .discover import FileEntry, discover
from .extract import EXTRACTOR_VERSION, empty_facts, extract
from .extract.ts import prefetch
from .index import connect, meta_get, meta_set
from .resolve import Resolver

PARALLEL_THRESHOLD = 64


@dataclass
class ScanReport:
    files: int = 0
    parsed: int = 0
    reused: int = 0
    failed: list[str] = field(default_factory=list)
    symbols: int = 0
    edges: int = 0
    renamed: int = 0
    seconds: float = 0.0
    stats: dict = field(default_factory=dict)


def _parse_one(args):
    path, lang, tier, text = args
    try:
        return path, extract(path, lang, tier, text), None
    except Exception as e:  # one bad file must not sink the scan
        return path, empty_facts(), f"{type(e).__name__}: {e}"


def _grammar(entry: FileEntry) -> list[str]:
    if entry.tier == "1":
        return ["typescript", "javascript"] if entry.lang in ("vue", "svelte") else [entry.lang]
    if entry.tier == "2":
        return [entry.lang]
    return []


def scan(repo: Path, full: bool = False, workers: int | None = None, scip: list | None = None) -> ScanReport:
    t0 = time.time()
    repo = repo.resolve()
    rep = ScanReport()
    db = connect(repo)
    prev = {} if full else {r["path"]: dict(r) for r in db.execute("select path, size, mtime, kind, hash, lines from files")}
    entries = discover(repo, {p: (r["size"], r["mtime"], r["kind"]) for p, r in prev.items() if r["mtime"]})
    by_path = {e.path: e for e in entries}
    rep.files = len(entries)
    cached = {} if full else {r["path"]: (r["hash"], r["version"], r["json"])
                              for r in db.execute("select path, hash, version, json from facts_cache")}

    facts: dict[str, dict] = {}
    todo = []
    hashes: dict[str, str] = {}
    lines: dict[str, int] = {}
    for e in entries:
        if not e.parse and e.kind not in ("docs", "config"):
            continue
        p = prev.get(e.path)
        c = cached.get(e.path)
        if p and p["mtime"] and (p["size"], p["mtime"]) == (e.size, e.mtime) and p["hash"]:
            # unchanged on disk: reuse hash, line count and facts without opening the file
            hashes[e.path], lines[e.path] = p["hash"], p["lines"]
            if not e.parse:
                continue
            if c and c[0] == p["hash"] and c[1] == EXTRACTOR_VERSION:
                facts[e.path] = json.loads(c[2])
                rep.reused += 1
                continue
        try:
            raw = (repo / e.path).read_bytes()
        except OSError:
            continue
        h = hashlib.sha1(raw).hexdigest()
        hashes[e.path] = h
        text = raw.decode("utf-8", "replace")
        lines[e.path] = text.count("\n") + 1
        if not e.parse:
            continue
        if c and c[0] == h and c[1] == EXTRACTOR_VERSION:
            facts[e.path] = json.loads(c[2])
            rep.reused += 1
        else:
            todo.append((e.path, e.lang, e.tier, text))

    if todo:
        prefetch(g for p, *_ in todo for g in _grammar(by_path[p]))
        if len(todo) >= PARALLEL_THRESHOLD and (workers or os.cpu_count() or 1) > 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(_parse_one, todo, chunksize=16))
        else:
            results = [_parse_one(t) for t in todo]
        for path, fx, err in results:
            facts[path] = fx
            if err:
                rep.failed.append(f"{path}: {err}")
        rep.parsed = len(todo)

    res = Resolver(repo, by_path, facts).run()
    rep.stats = dict(res.stats)
    scip_paths = [str(Path(p).resolve()) for p in scip] if scip else json.loads(meta_get(db, "scip", "[]") or "[]")
    if scip_paths:
        res.edges, rep.stats["scip"] = _apply_scip(res.edges, scip_paths, facts)

    parsed = {t[0] for t in todo}
    # a file's symbol rows depend only on its own facts: rewrite just the re-parsed and the removed files
    stale_files = parsed | (set(prev) - set(facts)) if not full else None
    with db:
        if stale_files is None:
            old_rows = db.execute("select id, hash, kind, name from symbols")
        else:
            db.execute("create temp table touched (path text primary key)")
            db.executemany("insert into touched values (?)", [(p,) for p in stale_files])
            old_rows = db.execute("select id, hash, kind, name from symbols where path in (select path from touched)")
        old = {r["id"]: (r["hash"], r["kind"], r["name"]) for r in old_rows}
        new_syms = [_symbol_row(path, s) for path in (facts if stale_files is None else parsed) for s in facts[path]["symbols"]]
        rep.renamed = _track_renames(db, old, new_syms, _git_renames(repo, meta_get(db, "commit")))
        if stale_files is None:
            db.execute("delete from symbols")
        else:
            db.execute("delete from symbols where path in (select path from touched)")
            db.execute("drop table touched")
        db.executemany("insert into symbols values (?,?,?,?,?,?,?,?,?,?,?,?)", new_syms)
        db.execute("delete from files")
        db.executemany("insert into files (path, kind, lang, tier, size, lines, hash, entry, doc, mtime) values (?,?,?,?,?,?,?,?,?,?)",
                       [(e.path, e.kind, e.lang, e.tier, e.size, lines.get(e.path), hashes.get(e.path),
                         int(facts.get(e.path, {}).get("entry", False)), facts.get(e.path, {}).get("doc"), e.mtime)
                        for e in entries])
        _write_edges(db, [(e.src, e.dst, e.type, e.conf, e.via, e.count) for e in res.edges])
        db.execute("delete from externals")
        db.executemany("insert into externals values (?,?)",
                       [(p, pkg) for p, pkgs in res.externals.items() for pkg in sorted(pkgs)])
        db.execute("create temp table keep (path text primary key)")
        db.executemany("insert into keep values (?)", [(p,) for p in facts])
        db.execute("delete from facts_cache where path not in (select path from keep)")
        db.execute("drop table keep")
        db.executemany("insert into facts_cache values (?,?,?,?) on conflict(path) do update set "
                       "hash = excluded.hash, version = excluded.version, json = excluded.json",
                       [(p, hashes.get(p), EXTRACTOR_VERSION, json.dumps(fx, ensure_ascii=False))
                        for p, fx in facts.items() if p in parsed])
        meta_set(db, "scanned_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
        meta_set(db, "commit", _git_head(repo) or "")
        meta_set(db, "stats", json.dumps(rep.stats))
        meta_set(db, "scip", json.dumps(scip_paths))
    rep.symbols = sum(len(fx["symbols"]) for fx in facts.values())
    rep.edges = len(res.edges)
    rep.seconds = round(time.time() - t0, 2)
    db.close()
    return rep


def _symbol_row(path: str, s: dict) -> tuple:
    return (f"{path}#{s['q']}", path, s["q"], s["n"], s["k"], s["s"], s["e"], s["sig"],
            f"{path}#{s['p']}" if s["p"] else path, s["h"], s["doc"], int(bool(s["x"])))


def _write_edges(db, rows: list[tuple]) -> None:
    """Edges are re-resolved globally each scan, but between two scans few of them change: write the diff."""
    old = set(db.execute("select src, dst, type, conf, via, count from edges").fetchall())
    new = set(rows)
    gone, added = old - new, new - old
    if len(gone) + len(added) > len(new) // 2:  # big change: a rewrite is cheaper than a diff
        db.execute("delete from edges")
        db.executemany("insert into edges values (?,?,?,?,?,?)", rows)
        return
    db.executemany("delete from edges where src = ? and dst = ? and type = ? and conf = ? and via = ? and count = ?", gone)
    db.executemany("insert into edges values (?,?,?,?,?,?)", added)


def _apply_scip(edges: list, scip_paths: list[str], facts: dict[str, dict]) -> tuple[list, dict]:
    """Replace symbol-level edges of files covered by SCIP indexes with the indexer's references."""
    from . import scip
    from .resolve import Edge
    docs = []
    missing = []
    for p in scip_paths:
        if Path(p).exists():
            docs.extend(scip.load(p))
        else:
            missing.append(p)
    symbols = [{"id": f"{path}#{s['q']}", "path": path, "name": s["n"], "kind": s["k"], "start": s["s"], "end": s["e"]}
               for path, fx in facts.items() for s in fx["symbols"]]
    # Some real Windows indexers emit absolute paths despite SCIP's relative_path
    # contract. Normalize them against project roots inferred from known suffixes.
    for doc in docs:
        normalized = doc.path.replace("\\", "/")
        if normalized not in facts:
            candidates = [path for path in facts if normalized.endswith("/" + path)]
            if len(candidates) == 1:
                normalized = candidates[0]
        doc.path = normalized
    precise, covered = scip.edges(docs, symbols, set(facts))
    keep = [e for e in edges if e.src.split("#", 1)[0] not in covered or e.type in ("imports", "tests", "inherits", "implements")]
    keep += [Edge(s, d, t, 1.0, "scip", c) for s, d, t, c in precise]
    return keep, {"documents": len(docs), "covered_files": len(covered), "edges": len(precise), "missing": missing}


def _track_renames(db, old: dict, new_syms: list[tuple], renamed_files: dict[str, str] | None = None) -> int:
    """A symbol that vanished while one with the same body hash and kind appeared was moved or renamed."""
    new_ids = {s[0] for s in new_syms}
    gone = {i: v for i, v in old.items() if i not in new_ids and v[0]}
    if not gone:
        return 0
    by_hash: dict[tuple, list[str]] = {}
    for s in new_syms:
        if s[0] not in old and s[9]:
            by_hash.setdefault((s[9], s[4]), []).append(s[0])
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    n = 0
    for old_id, (h, kind, _name) in gone.items():
        cands = by_hash.get((h, kind), [])
        old_path, qual = old_id.split("#", 1)
        moved_path = (renamed_files or {}).get(old_path)
        moved_id = f"{moved_path}#{qual}" if moved_path else None
        if moved_id in new_ids and any(s[0] == moved_id and s[4] == kind for s in new_syms):
            cands = [moved_id]
        if len(cands) == 1:
            db.execute("insert into aliases values (?,?,?) on conflict(old) do update set new = excluded.new, scan = excluded.scan",
                       (old_id, cands[0], stamp))
            db.execute("update aliases set new = ? where new = ?", (cands[0], old_id))
            n += 1
    return n


def _git_renames(repo: Path, commit: str | None) -> dict[str, str]:
    if not commit:
        return {}
    try:
        result = subprocess.run(["git", "diff", "--name-status", "-z", "-M", commit, "--"], cwd=repo,
                                capture_output=True, check=True)
        fields = result.stdout.decode("utf-8", "replace").split("\0")
        mapping = {}
        i = 0
        while i < len(fields) and fields[i]:
            status = fields[i]
            if status.startswith(("R", "C")):
                if status.startswith("R"):
                    mapping[fields[i + 1]] = fields[i + 2]
                i += 3
            else:
                i += 2
        return mapping
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        return {}


def _git_head(repo: Path) -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
