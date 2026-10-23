"""Compact views of the repository for an agent deciding regions and writing notes.

`outline` is the whole repo at a glance (directory tree with sizes, entry points, the most important
files, external packages). `context` zooms into one region, directory or file: its members with their
docstrings and main symbols, and how strongly it depends on the rest.
"""

from __future__ import annotations

import posixpath
from collections import Counter, defaultdict
from pathlib import Path

from ..graphdata import Index, file_of, load_index
from ..index import connect
from . import store
from .agenda import staleness
from .regions import UNASSIGNED, effective_regions, owner_of
from .store import REGION_PREFIX


def _top_symbols(idx: Index, path: str, n: int = 6) -> list[str]:
    syms = [s for s in idx.symbols.values() if s["path"] == path and s["parent"] == path
            and s["kind"] in ("class", "function", "interface", "type", "enum", "namespace")]
    syms.sort(key=lambda s: (-idx.in_deg.get(s["id"], 0), s["start"]))
    return [f"{s['kind'][:5]} {s['name']}" + (f" ({idx.in_deg[s['id']]}←)" if idx.in_deg.get(s["id"]) else "") for s in syms[:n]]


def _externals(repo: Path, paths: set[str] | None = None, n: int = 20) -> list[str]:
    db = connect(repo)
    c = Counter(r["pkg"] for r in db.execute("select path, pkg from externals") if paths is None or r["path"] in paths)
    db.close()
    return [f"{k} ({v})" for k, v in c.most_common(n)]


def outline(repo: Path, depth: int = 3) -> dict:
    idx = load_index(repo)
    sem = store.load(repo)
    tree: dict[str, Counter] = defaultdict(Counter)
    for p, f in idx.mapped.items():
        parts = p.split("/")[:-1]
        for d in range(0, min(depth, len(parts)) + 1):
            key = "/".join(parts[:d]) or "."
            tree[key][f["kind"]] += 1
    by_dir: dict[str, list[str]] = defaultdict(list)
    for p in idx.mapped:
        by_dir[posixpath.dirname(p) or "."].append(p)
    children: dict[str, list[str]] = defaultdict(list)
    for d in tree:
        if d != ".":
            children[posixpath.dirname(d) or "."].append(d)
    def shape(d):  # what a directory looks like: its file names and subdirectory names
        return frozenset([posixpath.basename(p) for p in by_dir.get(d, [])] + [posixpath.basename(k) for k in children.get(d, [])])
    collapsed = set()
    for d, kids in children.items():
        if d == "." or len(kids) <= 8:
            continue
        # names present in at least half the subdirectories; most subdirectories having all of them = copies
        freq = Counter(n for k in kids for n in shape(k))
        core = {n for n, c in freq.items() if c >= len(kids) / 2}
        if core and sum(1 for k in kids if core <= shape(k)) >= 0.6 * len(kids):
            collapsed.add(d)
    dirs = []
    for d in sorted(tree):
        anc = posixpath.dirname(d) or "." if d != "." else None
        hidden = False
        while anc is not None:
            if anc in collapsed:
                hidden = True
                break
            anc = (posixpath.dirname(anc) or ".") if anc != "." else None
        if hidden:
            continue
        best = sorted(by_dir.get(d, []), key=lambda p: -idx.rank.get(p, 0))[:3]
        row = {"dir": d, **dict(tree[d]), "top": [posixpath.basename(b) for b in best]}
        if d in collapsed:
            kids = sorted(children[d])
            row["subdirs"] = f"{len(kids)} similar subdirectories, e.g. " + ", ".join(posixpath.basename(k) for k in kids[:3])
        dirs.append(row)
    entries = sorted(p for p, f in idx.mapped.items() if f["entry"] and f["kind"] == "source")
    # near-identical copies (same name, docstring and symbols) are listed once
    important, groups = [], {}
    for p in sorted(idx.mapped, key=lambda p: -idx.rank.get(p, 0)):
        key = (posixpath.basename(p), idx.mapped[p].get("doc"), tuple(_top_symbols(idx, p, 4)))
        if key in groups:
            groups[key].append(p)
            continue
        groups[key] = [p]
        important.append((p, key))
        if len(important) >= 25:
            break
    return {
        "repo": repo.name, "files": len(idx.mapped), "symbols": len(idx.symbols),
        "regions_defined": len(sem.regions),
        "dirs": dirs,
        "entry_points": entries[:40] + ([f"… {len(entries) - 40} more"] if len(entries) > 40 else []),
        "important_files": [{"path": p, "rank": round(idx.rank.get(p, 0), 2), "doc": idx.mapped[p].get("doc"),
                             "symbols": _top_symbols(idx, p, 4),
                             **({"copies": len(groups[k]) - 1} if len(groups[k]) > 1 else {})} for p, k in important],
        "externals": _externals(repo),
        "next": "write regions with `codeatlas validate/apply`; zoom in with `codeatlas context <dir>`",
    }


def _member_files(idx: Index, regions, assign, target: str) -> tuple[list[str], dict]:
    if target.startswith(REGION_PREFIX) or target in regions:
        rid = target
        if rid not in regions:
            return [], {"error": f"unknown region {target}"}
        def under(r):
            out = list(regions[r].files)
            for c in regions[r].children:
                out.extend(under(c))
            return out
        r = regions[rid]
        return under(rid), {"region": rid, "name": r.name, "role": r.role, "responsibility": r.responsibility,
                            "children": [{"id": c, "name": regions[c].name} for c in r.children]}
    if target in idx.mapped:
        return [target], {"file": target}
    prefix = target.rstrip("/") + "/" if target not in (".", "") else ""
    files = [p for p in idx.mapped if p.startswith(prefix)]
    return files, {"dir": target}


def context(repo: Path, target: str, limit: int = 40) -> dict:
    idx = load_index(repo)
    sem = store.load(repo)
    regions, assign, _ = effective_regions(idx.mapped, sem, repo.name)
    files, head = _member_files(idx, regions, assign, target)
    if "error" in head:
        return {"ok": False, **head}
    if not files:
        return {"ok": False, "error": f"nothing mapped under {target!r}"}
    fset = set(files)
    stale = staleness(repo, idx, sem)

    def owner(p):
        o = owner_of(regions, assign[p]) if p in assign else ""
        return o if o.startswith(REGION_PREFIX) and o != UNASSIGNED else posixpath.dirname(p) or "."

    out_w: Counter = Counter()
    in_w: Counter = Counter()
    for (a, b), w in idx.fw.items():
        if a in fset and b not in fset:
            out_w[owner(b)] += w
        elif b in fset and a not in fset:
            in_w[owner(a)] += w
    ranked = sorted(files, key=lambda p: -idx.rank.get(p, 0))
    members = []
    for p in ranked[:limit]:
        f = idx.mapped[p]
        m = {"path": p, "kind": f["kind"], "lines": f["lines"], "rank": round(idx.rank.get(p, 0), 2)}
        if f.get("doc"):
            m["doc"] = f["doc"]
        if f["entry"]:
            m["entry"] = True
        syms = _top_symbols(idx, p)
        if syms:
            m["symbols"] = syms
        if p in sem.notes:
            m["note"] = sem.notes[p]["text"] + (" [STALE]" if p in stale else "")
        members.append(m)
    note = sem.notes.get(head.get("region", ""), {}).get("text")
    rels = [r for r in sem.relations.values() if head.get("region") in (r["src"], r["dst"])]
    return {
        "ok": True, **head, **({"note": note} if note else {}),
        "files": len(files), "kinds": dict(Counter(idx.mapped[p]["kind"] for p in files)),
        "members": members + ([f"… {len(files) - limit} more files"] if len(files) > limit else []),
        "depends_on": [{"on": k, "weight": round(v, 1)} for k, v in out_w.most_common(12)],
        "used_by": [{"by": k, "weight": round(v, 1)} for k, v in in_w.most_common(12)],
        "externals": _externals(repo, fset, 12),
        "relations": [{k: r[k] for k in ("id", "src", "dst", "family", "label")} for r in rels],
    }


def semantic_status(repo: Path, idx: Index | None = None) -> dict:
    idx = idx or load_index(repo)
    sem = store.load(repo)
    regions, assign, _ = effective_regions(idx.mapped, sem, repo.name)
    unassigned = sum(1 for r in assign.values() if owner_of(regions, r) == UNASSIGNED)
    stale = staleness(repo, idx, sem)
    return {"regions": len(sem.regions), "assigned_files": len(idx.mapped) - unassigned if sem.regions else 0,
            "unassigned_files": unassigned if sem.regions else len(idx.mapped),
            "notes": len(sem.notes), "relations": len(sem.relations), "tours": len(sem.tours),
            "stale": sum(1 for v in stale.values() if v == "stale"), "orphaned": sum(1 for v in stale.values() if v == "orphan"),
            "runs": len(store.read_log(repo))}
