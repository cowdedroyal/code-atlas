"""Changesets: the only way agents change the semantic layer.

    {"intent": "regionize",
     "regions":   [{"op": "upsert", "id": "inference-api", "name": "…", "role": "integration",
                    "responsibility": "…", "parent": null}, {"op": "delete", "id": "…"}],
     "assign":    [{"op": "add", "match": "mcp_servers/nano_banana/", "region": "inference-api", "also": []},
                   {"op": "remove", "match": "…"}],
     "notes":     [{"op": "upsert", "target": "region:inference-api" | "path" | "path#Qual", "text": "…"},
                   {"op": "delete", "target": "…"}],
     "relations": [{"op": "upsert", "src": "region:a", "dst": "region:b", "family": "data", "label": "…",
                    "rationale": "…", "evidence": ["path#Qual", "path:120-140"]}, {"op": "delete", "id": "rel-…"}]}

`validate` dry-runs the whole changeset against a copy and reports every problem at once; `apply`
writes it, records the content hashes the text was written against (so later edits make it stale),
logs the run and keeps a snapshot for `revert`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from pathlib import Path

from ..graphdata import Index, load_index
from ..index import cache_dir
from . import store
from .regions import compile_glob, match_rules
from .store import FAMILIES, REGION_PREFIX, ROLES, Semantic
from .tours import check_tours

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
RANGE = re.compile(r"^(?P<path>.+):(?P<a>\d+)(?:-(?P<b>\d+))?$")
LIMITS = {"name": 40, "responsibility": 140, "label": 48, "rationale": 200,
          "note_region": 500, "note_file": 360, "note_symbol": 280}


def relation_id(src: str, dst: str, family: str, label: str) -> str:
    return "rel-" + hashlib.sha1(f"{src}|{dst}|{family}|{label}".encode()).hexdigest()[:10]


class Checker:
    def __init__(self, idx: Index, sem: Semantic):
        self.idx = idx
        self.sem = sem
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.touched_tours: set[str] = set()
        self.chains: dict[str, list[list[str]]] = {}

    def err(self, where: str, msg: str):
        self.errors.append(f"{where}: {msg}")

    def length(self, where: str, field: str, value, limit_key: str | None = None, required: bool = True):
        if value in (None, ""):
            if required:
                self.err(where, f"`{field}` is required")
            return
        if not isinstance(value, str):
            self.err(where, f"`{field}` must be a string")
            return
        limit = LIMITS[limit_key or field]
        if len(value) > limit:
            self.err(where, f"`{field}` is {len(value)} chars, limit {limit} — say less, link to code instead")
        if "```" in value:
            self.err(where, f"`{field}` must not contain code blocks")

    def node_exists(self, node: str) -> bool:
        if node.startswith(REGION_PREFIX):
            return self.sem.region(node[len(REGION_PREFIX):]) is not None
        return self.idx.exists(self.idx.canonical(node))

    def evidence_ok(self, ev: str) -> bool:
        if self.idx.exists(self.idx.canonical(ev)):
            return True
        m = RANGE.match(ev)
        if m and m.group("path") in self.idx.mapped:
            a, b = int(m.group("a")), int(m.group("b") or m.group("a"))
            return 1 <= a <= b <= (self.idx.mapped[m.group("path")]["lines"] or 0)
        return False


def _apply_ops(ck: Checker, cs: dict) -> dict:
    """Mutate ck.sem in place; returns counts."""
    sem = ck.sem
    counts = {"regions": 0, "assign": 0, "notes": 0, "relations": 0, "tours": 0}
    cfg = cs.get("config") or {}
    if cfg:
        lang = cfg.get("language")
        if lang is not None and not (isinstance(lang, str) and re.match(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$", lang)):
            ck.err("config", f"language {lang!r} must be a code like `en`, `zh`, `pt-BR`")
        unknown = set(cfg) - {"language"}
        if unknown:
            ck.err("config", f"unknown keys {sorted(unknown)}")
        if lang:
            sem.config["language"] = lang
    for i, r in enumerate(cs.get("regions") or []):
        w = f"regions[{i}]"
        op = r.get("op", "upsert")
        rid = r.get("id")
        if not isinstance(rid, str) or not SLUG.match(rid):
            ck.err(w, f"id {rid!r} must be a lowercase slug like `inference-api`")
            continue
        if op == "delete":
            if sem.region(rid) is None:
                ck.err(w, f"region {rid} does not exist")
                continue
            sem.regions = [x for x in sem.regions if x["id"] != rid]
            gone = REGION_PREFIX + rid
            dropped = [k for k, rel in sem.relations.items() if gone in (rel["src"], rel["dst"])]
            for k in dropped:
                del sem.relations[k]
            if sem.notes.pop(gone, None) or dropped:
                ck.warnings.append(f"{w}: deleting {rid} also removed its note and {len(dropped)} relation(s)")
        elif op == "upsert":
            ck.length(w, "name", r.get("name"))
            ck.length(w, "responsibility", r.get("responsibility"))
            if r.get("role") not in ROLES:
                ck.err(w, f"role {r.get('role')!r} must be one of {', '.join(ROLES)}")
            parent = r.get("parent")
            if parent is not None and (not isinstance(parent, str) or parent == rid):
                ck.err(w, f"parent {parent!r} is not a valid region id")
            entry = {"id": rid, "name": r.get("name"), "role": r.get("role"),
                     "responsibility": r.get("responsibility"), "parent": parent}
            old = sem.region(rid)
            if old is not None:
                old.update(entry)
            else:
                sem.regions.append(entry)
        else:
            ck.err(w, f"unknown op {op!r}")
            continue
        counts["regions"] += 1

    for i, a in enumerate(cs.get("assign") or []):
        w = f"assign[{i}]"
        op = a.get("op", "add")
        match = a.get("match")
        if not isinstance(match, str) or not match.strip():
            ck.err(w, "`match` glob is required")
            continue
        if op == "remove":
            before = len(sem.rules)
            sem.rules = [x for x in sem.rules if x["match"] != match]
            if len(sem.rules) == before:
                ck.err(w, f"no rule with match {match!r}")
        elif op == "add":
            rule = {"match": match, "region": a.get("region"), "also": list(a.get("also") or [])}
            sem.rules = [x for x in sem.rules if x["match"] != match] + [rule]  # re-adding moves it last (wins)
        else:
            ck.err(w, f"unknown op {op!r}")
            continue
        counts["assign"] += 1

    for i, n in enumerate(cs.get("notes") or []):
        w = f"notes[{i}]"
        op = n.get("op", "upsert")
        target = n.get("target")
        if not isinstance(target, str):
            ck.err(w, "`target` is required")
            continue
        target = target if target.startswith(REGION_PREFIX) else ck.idx.canonical(target)
        if op == "delete":
            if sem.notes.pop(target, None) is None:
                ck.err(w, f"no note on {target}")
        elif op == "upsert":
            kind = "note_region" if target.startswith(REGION_PREFIX) else ("note_symbol" if "#" in target else "note_file")
            ck.length(w, "text", n.get("text"), kind)
            sem.notes[target] = {"id": target, "text": n.get("text")}
        elif op == "confirm":
            if target not in sem.notes:
                ck.err(w, f"no note on {target}")
            if set(n) - {"op", "target"}:
                ck.err(w, "confirm accepts only op and target; use upsert to change content")
        else:
            ck.err(w, f"unknown op {op!r}")
            continue
        counts["notes"] += 1

    for i, r in enumerate(cs.get("relations") or []):
        w = f"relations[{i}]"
        op = r.get("op", "upsert")
        if op == "delete":
            if sem.relations.pop(r.get("id"), None) is None:
                ck.err(w, f"no relation {r.get('id')!r}")
            counts["relations"] += 1
            continue
        if op == "confirm":
            rid = r.get("id")
            rel = sem.relations.get(rid)
            if not rel:
                ck.err(w, f"no relation {rid!r}")
            else:
                for ev in rel["evidence"]:
                    if not ck.evidence_ok(ev):
                        ck.err(w, f"evidence {ev!r} no longer exists; repair the relation before confirming")
            if set(r) - {"op", "id"}:
                ck.err(w, "confirm accepts only op and id")
            counts["relations"] += 1
            continue
        if op != "upsert":
            ck.err(w, f"unknown op {op!r}")
            continue
        src, dst = r.get("src"), r.get("dst")
        if not isinstance(src, str) or not isinstance(dst, str):
            ck.err(w, "`src` and `dst` are required")
            continue
        src = src if src.startswith(REGION_PREFIX) else ck.idx.canonical(src)
        dst = dst if dst.startswith(REGION_PREFIX) else ck.idx.canonical(dst)
        if src == dst:
            ck.err(w, "a relation needs two different ends")
        if r.get("family") not in FAMILIES:
            ck.err(w, f"family {r.get('family')!r} must be one of {', '.join(FAMILIES)}")
        ck.length(w, "label", r.get("label"))
        ck.length(w, "rationale", r.get("rationale"))
        ev = r.get("evidence")
        if not isinstance(ev, list) or not ev:
            ck.err(w, "`evidence` must list at least one symbol id, file path or `path:start-end`")
            ev = []
        for e in ev:
            if not isinstance(e, str) or not ck.evidence_ok(e):
                ck.err(w, f"evidence {e!r} does not exist in the index (run `codeatlas scan` if the code changed)")
        rel_id = relation_id(src, dst, r.get("family") or "", r.get("label") or "")
        sem.relations[rel_id] = {"id": rel_id, "src": src, "dst": dst, "family": r.get("family"),
                                 "label": r.get("label"), "rationale": r.get("rationale"), "evidence": list(ev)}
        counts["relations"] += 1

    for i, t in enumerate(cs.get("tours") or []):
        w = f"tours[{i}]"
        op = t.get("op", "upsert")
        tid = t.get("id")
        if not isinstance(tid, str) or not SLUG.match(tid):
            ck.err(w, f"id {tid!r} must be a lowercase slug like `checkout-request`")
            continue
        if op == "delete":
            if sem.tours.pop(tid, None) is None:
                ck.err(w, f"no tour {tid}")
        elif op == "upsert":
            steps = [{k: s.get(k) for k in ("at", "text", "via_relation") if s.get(k) is not None}
                     if isinstance(s, dict) else s for s in (t.get("steps") or [])]
            sem.tours[tid] = {"id": tid, "title": t.get("title"), "summary": t.get("summary"), "steps": steps}
            ck.touched_tours.add(tid)
        elif op == "confirm":
            if tid not in sem.tours:
                ck.err(w, f"no tour {tid}")
            else:
                ck.touched_tours.add(tid)
            if set(t) - {"op", "id"}:
                ck.err(w, "confirm accepts only op and id")
        else:
            ck.err(w, f"unknown op {op!r}")
            continue
        counts["tours"] += 1
    return counts


def _check_state(ck: Checker) -> dict:
    """Whole-state invariants after all ops."""
    sem, idx = ck.sem, ck.idx
    ids = {r["id"] for r in sem.regions}
    for r in sem.regions:
        if r.get("parent") and r["parent"] not in ids:
            ck.err(f"region {r['id']}", f"parent {r['parent']!r} does not exist")
    for r in sem.regions:  # cycles
        seen, cur = set(), r
        while cur and cur.get("parent"):
            if cur["id"] in seen:
                ck.err(f"region {r['id']}", "parent chain forms a cycle")
                break
            seen.add(cur["id"])
            cur = sem.region(cur["parent"])
    paths = sorted(idx.mapped)
    for i, rule in enumerate(sem.rules):
        if rule.get("region") not in ids:
            ck.err(f"rule {rule['match']!r}", f"region {rule.get('region')!r} does not exist")
        for a in rule.get("also") or []:
            if a not in ids:
                ck.err(f"rule {rule['match']!r}", f"secondary region {a!r} does not exist")
        spec = compile_glob(rule["match"])
        if not any(spec.match_file(p) for p in paths):
            ck.err(f"rule {rule['match']!r}", "matches no mapped file")
    for nid in sem.notes:
        if not ck.node_exists(nid):
            ck.err(f"note {nid}", "target does not exist")
    for rid, rel in sem.relations.items():
        for end in (rel["src"], rel["dst"]):
            if not ck.node_exists(end):
                ck.err(f"relation {rid}", f"end {end!r} does not exist")
    if ck.touched_tours:  # only tours in this changeset must pass; broken old ones surface in `agenda`
        ck.chains = check_tours(idx, sem, ck.touched_tours, ck.err)
    summary = {}
    if sem.regions:
        primary, _ = match_rules(sem.rules, paths)
        unassigned = [p for p in paths if primary.get(p) not in ids]
        used = set(primary.values()) | {r.get("parent") for r in sem.regions}
        empty = sorted(ids - used)
        if empty:
            ck.warnings.append(f"regions with no files and no children: {', '.join(empty)}")
        if unassigned:
            dirs: dict[str, int] = {}
            for p in unassigned:
                d = p.rsplit("/", 1)[0] if "/" in p else "."
                dirs[d] = dirs.get(d, 0) + 1
            top = sorted(dirs.items(), key=lambda x: -x[1])[:8]
            ck.warnings.append(f"{len(unassigned)} file(s) still unassigned, e.g. " + ", ".join(f"{d}/ ({n})" for d, n in top))
        summary = {"regions": len(ids), "assigned": len(paths) - len(unassigned), "unassigned": len(unassigned)}
    return summary


def validate(repo: Path, cs: dict, idx: Index | None = None) -> tuple[dict, Semantic, dict]:
    idx = idx or load_index(repo)
    if not idx.mapped:
        return {"ok": False, "errors": ["the structure index is empty — run `codeatlas scan` first"], "warnings": []}, store.load(repo), {}
    ck = Checker(idx, copy.deepcopy(store.load(repo)))
    counts = _apply_ops(ck, cs)
    summary = _check_state(ck)
    return ({"ok": not ck.errors, "errors": ck.errors, "warnings": ck.warnings, "changes": counts, "state": summary},
            ck.sem, counts)


def _range_hash(repo: Path, ev: str) -> str | None:
    m = RANGE.match(ev)
    if not m:
        return None
    try:
        lines = (repo / m.group("path")).read_text("utf-8", "replace").splitlines()
    except OSError:
        return None
    a, b = int(m.group("a")), int(m.group("b") or m.group("a"))
    return hashlib.sha1(re.sub(r"\s+", " ", "\n".join(lines[a - 1: b])).strip().encode()).hexdigest()[:16]


def evidence_hash(repo: Path, idx: Index, ev: str) -> str | None:
    node = idx.canonical(ev)
    return idx.anchor(node) if idx.exists(node) else _range_hash(repo, ev)


def apply(repo: Path, cs: dict, run: str | None = None) -> dict:
    idx = load_index(repo)
    before = store.snapshot(repo)
    report, sem, counts = validate(repo, cs, idx)
    if not report["ok"]:
        return report
    run = run or cs.get("run") or time.strftime("run-%Y%m%d-%H%M%S")
    at = time.strftime("%Y-%m-%dT%H:%M:%S")
    old = store.load(repo)
    confirmed = {
        "notes": {n["target"] if n["target"].startswith(REGION_PREFIX) else idx.canonical(n["target"]) for n in cs.get("notes", []) if n.get("op") == "confirm"},
        "relations": {r["id"] for r in cs.get("relations", []) if r.get("op") == "confirm"},
        "tours": {t["id"] for t in cs.get("tours", []) if t.get("op") == "confirm"},
    }
    for nid, n in sem.notes.items():
        prev = old.notes.get(nid)
        if prev and prev.get("text") == n.get("text") and (nid not in confirmed["notes"] or prev.get("anchor") == idx.anchor(nid)):
            sem.notes[nid] = prev  # unchanged text keeps its original anchor and run
            continue
        n.update({"anchor": idx.anchor(nid), "run": run, "at": at})
    for rid, rel in sem.relations.items():
        prev = old.relations.get(rid)
        anchors = {e: evidence_hash(repo, idx, e) for e in rel["evidence"]}
        if prev and {k: prev.get(k) for k in ("rationale", "evidence")} == {k: rel.get(k) for k in ("rationale", "evidence")} and (rid not in confirmed["relations"] or prev.get("anchors") == anchors):
            sem.relations[rid] = prev
            continue
        rel.update({"anchors": anchors, "run": run, "at": at})
    for tid, t in sem.tours.items():
        prev = old.tours.get(tid)
        same = prev and [{k: s.get(k) for k in ("at", "text", "via_relation")} for s in prev.get("steps", [])] == \
            [{k: s.get(k) for k in ("at", "text", "via_relation")} for s in t["steps"]] and \
            (prev.get("title"), prev.get("summary")) == (t.get("title"), t.get("summary"))
        if same and (tid not in confirmed["tours"] or all(s.get("anchor") == idx.anchor(s["at"]) for s in prev["steps"])):
            sem.tours[tid] = prev
            continue
        for s in t["steps"]:
            s["anchor"] = idx.anchor(s["at"])
        t.update({"run": run, "at": at})
    store.save(repo, sem)
    after = store.snapshot(repo)
    runs = cache_dir(repo) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"{run}.json").write_text(json.dumps({"before": before, "after": after}, ensure_ascii=False), encoding="utf-8")
    store.append_log(repo, {"run": run, "at": at, "intent": cs.get("intent"), "changes": counts,
                            "state": report.get("state")})
    return {**report, "run": run}


def revert(repo: Path, run: str) -> dict:
    f = cache_dir(repo) / "runs" / f"{run}.json"
    if not f.exists():
        return {"ok": False, "error": f"no snapshot for {run} on this machine"}
    snap = json.loads(f.read_text("utf-8"))
    current = {k: v for k, v in store.snapshot(repo).items() if k != "log.jsonl"}
    after = {k: v for k, v in snap["after"].items() if k != "log.jsonl"}
    if current != after:
        return {"ok": False, "error": f"the semantic layer changed after {run}; revert later runs first"}
    store.restore(repo, snap["before"])
    store.append_log(repo, {"run": f"revert:{run}", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "intent": "revert"})
    return {"ok": True, "reverted": run}
