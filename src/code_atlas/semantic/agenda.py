"""Staleness and the agenda: what the semantic layer needs next, computed from the current state.

Nothing is queued; ask again after any scan or apply and the list reflects it.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..graphdata import Index, file_of, load_index
from . import store
from .changeset import evidence_hash
from .regions import UNASSIGNED, effective_regions, owner_of
from .store import REGION_PREFIX, Semantic
from .tours import tour_candidates, tour_status

PRIORITY = {"regionize": 0, "assign": 1, "refresh": 2, "link": 3, "tour": 3.5, "annotate-region": 4, "annotate-file": 5,
            "annotate-symbol": 6}


def staleness(repo: Path, idx: Index, sem: Semantic) -> dict[str, str]:
    """note/relation id → "stale" (code changed since it was written) | "orphan" (code is gone)."""
    out: dict[str, str] = {}
    for nid, n in sem.notes.items():
        if nid.startswith(REGION_PREFIX):
            continue
        node = idx.canonical(nid)
        if not idx.exists(node):
            out[nid] = "orphan"
        elif n.get("anchor") and n["anchor"] != idx.anchor(node):
            out[nid] = "stale"
    for rid, rel in sem.relations.items():
        for end in (rel["src"], rel["dst"]):
            if not end.startswith(REGION_PREFIX) and not idx.exists(idx.canonical(end)):
                out[rid] = "orphan"
        if rid in out:
            continue
        for ev, h in (rel.get("anchors") or {}).items():
            now = evidence_hash(repo, idx, ev)
            if now is None:
                out[rid] = "orphan"
                break
            if h and now != h:
                out[rid] = "stale"
    return out


def leaf_region_weights(idx: Index, assign: dict[str, str]) -> dict[tuple[str, str], float]:
    w: dict[tuple[str, str], float] = defaultdict(float)
    for (a, b), x in idx.fw.items():
        ra, rb = assign.get(a), assign.get(b)
        if ra and rb and ra != rb:
            w[(ra, rb)] += x
    return dict(w)


def agenda(repo: Path, limit: int = 20, types: list[str] | None = None, idx: Index | None = None) -> dict:
    idx = idx or load_index(repo)
    sem = store.load(repo)
    tasks: list[dict] = []
    regions, assign, _ = effective_regions(idx.mapped, sem, repo.name, dependency_weights=idx.fw)

    if not sem.regions:
        tasks.append({"type": "regionize", "target": ".", "reason": f"no regions yet — {len(idx.mapped)} files sit in "
                      f"{len(regions) - 1} draft (directory) regions", "hint": "codeatlas outline"})
    else:
        groups: dict[str, list[str]] = defaultdict(list)
        for p, rid in assign.items():
            if owner_of(regions, rid) == UNASSIGNED:
                parts = p.split("/")
                groups["/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else ".")].append(p)
        for g, files in sorted(groups.items(), key=lambda x: -len(x[1])):
            tasks.append({"type": "assign", "target": g, "reason": f"{len(files)} file(s) not in any region",
                          "files": files[:10], "hint": f"codeatlas context {g}"})

    for nid, why in staleness(repo, idx, sem).items():
        tasks.append({"type": "refresh", "target": nid, "reason": f"{why}: the code it describes changed" if why == "stale"
                      else f"{why}: the code it points to no longer exists",
                      "hint": f"codeatlas show {sem.relations[nid]['src'] if nid in sem.relations else nid} --code"})
    from .explainers import card_status, load_cards
    for cid, st in card_status(idx, sem, load_cards(repo)).items():
        tasks.append({"type": "refresh", "target": f"card:{cid}", "reason": {
            "stale": "card: the code it explains changed", "orphan": "card: code it explains no longer exists"}[st["state"]],
            "anchors": st["nodes"][:6], "hint": f"codeatlas card confirm {cid}"})
    for tid, st in tour_status(idx, sem).items():
        if st["state"]:
            bad = [sem.tours[tid]["steps"][i]["at"] for i, s in enumerate(st["steps"]) if s]
            tasks.append({"type": "refresh", "target": f"tour:{tid}", "reason": {
                "stale": "tour: the code of some steps changed", "orphan": "tour: a step's code no longer exists",
                "broken": "tour: two consecutive steps are no longer connected"}[st["state"]],
                "steps": bad[:6], "problems": st["problems"][:3], "hint": f"codeatlas path <step> <next step>"})
    if sem.regions and len(sem.tours) < 3:
        cands = tour_candidates(idx, sem)
        if cands:
            tasks.append({"type": "tour", "target": cands[0], "reason": f"{len(sem.tours)} tour(s) so far; good starting points "
                          "have wide fan-out from an interface region", "candidates": cands,
                          "hint": f"codeatlas show '{cands[0]}' --code"})

    if sem.regions:
        rel_pairs = {frozenset((r["src"], r["dst"])) for r in sem.relations.values()}
        sem_assign = {}
        for p, rid in assign.items():
            o = owner_of(regions, rid)
            sem_assign[p] = o if o.startswith(REGION_PREFIX) and o != UNASSIGNED else None
        rw = leaf_region_weights(idx, {p: r for p, r in sem_assign.items() if r})
        pair_w: dict[frozenset, float] = defaultdict(float)
        for (a, b), x in rw.items():
            pair_w[frozenset((a, b))] += x
        role = {REGION_PREFIX + r["id"]: r.get("role") for r in sem.regions}
        # links into tests/scripts are expected and rarely worth a relation: rank them last
        adjusted = {pair: x * (0.25 if any(role.get(r) == "tooling" for r in pair) else 1.0) for pair, x in pair_w.items()}
        for pair, x in sorted(adjusted.items(), key=lambda kv: -kv[1]):
            if x < 3 or pair in rel_pairs:
                continue
            a, b = sorted(pair)
            tasks.append({"type": "link", "target": f"{a} ↔ {b}", "reason": f"structural weight {x:.0f} but no semantic relation",
                          "examples": _examples(idx, sem_assign, a, b), "hint": f"codeatlas context {a}"})
        for r in sem.regions:
            rid = REGION_PREFIX + r["id"]
            if rid not in sem.notes:
                tasks.append({"type": "annotate-region", "target": rid, "reason": "region has no note",
                              "hint": f"codeatlas context {rid}"})

    for p in sorted(idx.mapped, key=lambda p: -idx.rank.get(p, 0))[:60]:
        if idx.rank.get(p, 0) >= 0.12 and p not in sem.notes and idx.mapped[p]["kind"] in ("source", "test"):
            tasks.append({"type": "annotate-file", "target": p, "reason": f"important file (rank {idx.rank[p]:.2f}) without a note",
                          "hint": f"codeatlas show {p}"})
    syms = sorted(idx.symbols.values(), key=lambda s: -idx.in_deg.get(s["id"], 0))[:80]
    for s in syms:
        n = idx.in_deg.get(s["id"], 0)
        if n >= 4 and s["id"] not in sem.notes and s["kind"] in ("class", "function", "method", "interface"):
            tasks.append({"type": "annotate-symbol", "target": s["id"], "reason": f"used {n}× without a note",
                          "hint": f"codeatlas show '{s['id']}' --code"})

    if types:
        tasks = [t for t in tasks if any(t["type"].startswith(x) for x in types)]
    tasks.sort(key=lambda t: PRIORITY.get(t["type"], 9))
    counts: dict[str, int] = defaultdict(int)
    for t in tasks:
        counts[t["type"]] += 1
    return {"ok": True, "total": len(tasks), "by_type": dict(counts), "tasks": tasks[:limit]}


def _examples(idx: Index, assign: dict, a: str, b: str, n: int = 4) -> list[str]:
    rows = []
    for e in idx.edges:
        fa, fb = file_of(e["src"]), file_of(e["dst"])
        ra, rb = assign.get(fa), assign.get(fb)
        if {ra, rb} == {a, b} and ra != rb:
            rows.append((e["count"] * e["conf"], f"{e['src']} -{e['type']}→ {e['dst']}"))
    return [r for _, r in sorted(rows, reverse=True)[:n]]
