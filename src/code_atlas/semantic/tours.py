"""Tours: an ordered walk through the code, one sentence per step.

A tour is only as good as its links, so every pair of consecutive steps must be connected: either the
scanner finds a call path from one to the next (through any code inside the earlier step, within
MAX_HOPS), or the step names a semantic relation (`via_relation`) whose ends cover both steps — the
way to cross links static analysis cannot see (HTTP, WebSocket, subprocess, files on disk).
"""

from __future__ import annotations

from collections import defaultdict, deque

from ..graphdata import Index, file_of
from .store import REGION_PREFIX, Semantic

MAX_HOPS = 6
FLOW_TYPES = {"calls", "instantiates", "references", "imports"}
LIMITS = {"title": 60, "summary": 300, "text": 220}
MIN_STEPS, MAX_STEPS = 2, 20


class PathFinder:
    """BFS over structural edges, where a node stands for its whole body (outgoing edges of any symbol
    nested inside it count) and reaching anything nested inside the target counts as reaching it."""

    def __init__(self, idx: Index):
        self.idx = idx
        self.out: dict[str, list[str]] = defaultdict(list)
        for e in idx.edges:
            if e["type"] in FLOW_TYPES:
                self.out[e["src"]].append(e["dst"])
        self.children: dict[str, list[str]] = defaultdict(list)
        for s in idx.symbols.values():
            self.children[s["parent"]].append(s["id"])

    def body(self, node: str) -> list[str]:
        out, stack = [], [node]
        while stack:
            n = stack.pop()
            out.append(n)
            stack.extend(self.children.get(n, ()))
        return out

    @staticmethod
    def covers(target: str, node: str) -> bool:
        if node == target:
            return True
        if "#" in target:
            return node.startswith(target + ".")
        return file_of(node) == target

    def path(self, src: str, dst: str, max_hops: int = MAX_HOPS) -> list[str] | None:
        """→ [src, hop…, node covered by dst], or None."""
        if self.covers(dst, src) or self.covers(src, dst):
            return [src, dst]
        prev: dict[str, str | None] = {src: None}
        q = deque([(src, 0)])
        while q:
            node, d = q.popleft()
            if d >= max_hops:
                continue
            for part in self.body(node):
                for nxt in self.out.get(part, ()):
                    if nxt in prev:
                        continue
                    prev[nxt] = node
                    # reaching a class (instantiating it) reaches its methods; reaching a file by import does not
                    if self.covers(dst, nxt) or (self.idx.symbols.get(nxt, {}).get("kind") == "class" and dst.startswith(nxt + ".")):
                        chain = [nxt]
                        while prev[chain[-1]] is not None:
                            chain.append(prev[chain[-1]])
                        return chain[::-1]
                    q.append((nxt, d + 1))
        return None


def relation_covers(idx: Index, regions_of: dict[str, set[str]], end: str, node: str) -> bool:
    """Does relation end `end` (region, file or symbol) include step node `node`?"""
    if end.startswith(REGION_PREFIX):
        return end in regions_of.get(file_of(node), set())
    return PathFinder.covers(end, node) or PathFinder.covers(node, end)


def regions_by_file(idx: Index, sem: Semantic) -> dict[str, set[str]]:
    """file → every semantic region it is in (primary with its ancestors, plus secondary ones)."""
    from .regions import effective_regions
    regions, assign, secondary = effective_regions(idx.mapped, sem, "repo")
    out: dict[str, set[str]] = {}
    for p, rid in assign.items():
        ids = set(secondary.get(p, []))
        r = regions.get(rid)
        while r is not None:
            if r.id.startswith(REGION_PREFIX):
                ids.add(r.id)
            r = regions.get(r.parent) if r.parent else None
        out[p] = ids
    return out


def check_tours(idx: Index, sem: Semantic, tour_ids, err, cards: dict[str, dict] | None = None) -> dict[str, list[list[str]]]:
    regions_of = regions_by_file(idx, sem)
    finder = PathFinder(idx)
    return {tid: check_tour(idx, sem, sem.tours[tid], f"tour {tid}", err, regions_of, finder, cards)
            for tid in sorted(tour_ids) if tid in sem.tours}


def tour_status(idx: Index, sem: Semantic, cards: dict[str, dict] | None = None) -> dict[str, dict]:
    """tour id → {"state": None | "stale" | "orphan" | "broken", "steps": [per-step state], "chains": [...]}.

    stale: a step's code changed since the tour was written; orphan: a step's code is gone;
    broken: two consecutive steps are no longer connected."""
    if not sem.tours:
        return {}
    regions_of = regions_by_file(idx, sem)
    finder = PathFinder(idx)
    out = {}
    for tid, t in sem.tours.items():
        problems: list[str] = []
        steps = [dict(s) for s in t.get("steps", [])]
        states = []
        for s in steps:
            node = idx.canonical(s.get("at", ""))
            if not idx.exists(node):
                states.append("orphan")
                continue
            s["at"] = node
            states.append("stale" if s.get("anchor") and s["anchor"] != idx.anchor(node) else None)
        chains = check_tour(idx, sem, {**t, "steps": steps}, tid, lambda w, m: problems.append(m), regions_of, finder, cards)
        broken = any("no call path" in m or "does not connect" in m or "card" in m for m in problems)
        state = "orphan" if "orphan" in states else "broken" if broken else "stale" if "stale" in states else None
        out[tid] = {"state": state, "steps": states, "chains": chains, "problems": problems}
    return out


def tour_candidates(idx: Index, sem: Semantic, n: int = 5) -> list[str]:
    """Symbols where a reader would start: in interface regions (or entry files), with the widest fan-out."""
    regions_of = regions_by_file(idx, sem)
    interface = {"region:" + r["id"] for r in sem.regions if r.get("role") == "interface"}
    started = {t["steps"][0]["at"] for t in sem.tours.values() if t.get("steps")}
    fan: dict[str, set[str]] = defaultdict(set)
    for e in idx.edges:
        if e["type"] in ("calls", "instantiates") and e["src"] in idx.symbols:
            fan[e["src"]].add(e["dst"])
    def ok(sid):
        p = file_of(sid)
        return (regions_of.get(p, set()) & interface or idx.mapped[p]["entry"]) and idx.symbols[sid]["kind"] in ("function", "method")
    cands = sorted((s for s in fan if ok(s) and s not in started), key=lambda s: -len(fan[s]))
    return cands[:n]


def _anchor_explains(idx: Index, regions_of: dict[str, set[str]], anchor: str, node: str) -> bool:
    """Does a card anchored at `anchor` explain `node` (a step's file or symbol) — directly or by containment?"""
    if anchor.startswith(REGION_PREFIX):
        return anchor in regions_of.get(file_of(node), set())
    a = idx.canonical(anchor)
    return a == node or a.startswith(node + "#") or node.startswith(a + "#")


def check_tour(idx: Index, sem: Semantic, tour: dict, where: str, err, regions_of: dict[str, set[str]],
               finder: PathFinder, cards: dict[str, dict] | None = None) -> list[list[str]]:
    """Report problems through err(where, msg); → the hop chain between each pair of steps."""
    for k in ("title", "summary"):
        v = tour.get(k)
        if k == "title" and not v:
            err(where, "`title` is required")
        if v and len(v) > LIMITS[k]:
            err(where, f"`{k}` is {len(v)} chars, limit {LIMITS[k]}")
    steps = tour.get("steps")
    if not isinstance(steps, list) or not (MIN_STEPS <= len(steps) <= MAX_STEPS):
        err(where, f"a tour needs {MIN_STEPS}–{MAX_STEPS} steps")
        return []
    chains: list[list[str]] = []  # one per step: the hop chain from the earlier step it is reached from
    for i, s in enumerate(steps):
        w = f"{where}.steps[{i}]"
        at = s.get("at") if isinstance(s, dict) else None
        if not isinstance(at, str) or not idx.exists(idx.canonical(at)):
            err(w, f"`at` {at!r} is not a file or symbol in the index")
            chains.append([])
            continue
        at = idx.canonical(at)
        s["at"] = at
        text = s.get("text")
        if not text:
            err(w, "`text` is required: say what happens here and why it matters")
        elif len(text) > LIMITS["text"]:
            err(w, f"`text` is {len(text)} chars, limit {LIMITS['text']}")
        card_id = s.get("card")
        if card_id is not None:
            if not isinstance(card_id, str) or not card_id.strip():
                err(w, "`card` must be an explainer card id")
            elif cards is not None:
                if card_id not in cards:
                    err(w, f"card {card_id!r} does not exist — write it first with `codeatlas card apply`")
                else:
                    anchors = cards[card_id].get("anchors") or []
                    if not any(_anchor_explains(idx, regions_of, a, at) for a in anchors):
                        err(w, f"card {card_id!r} does not explain this step: its anchors are {anchors or 'none'}, "
                               f"but the step is {at} — anchor the card on the step's symbol (or on the file that holds it)")
        # earlier steps, nearest first: a flow often returns to an orchestrator before the next call
        earlier = [x["at"] for x in reversed(steps[:i]) if isinstance(x, dict) and idx.exists(x.get("at", ""))]
        if i == 0 or not earlier:
            chains.append([])
            continue
        rel_id = s.get("via_relation")
        if rel_id:
            rel = sem.relations.get(rel_id)
            src = next((e for e in earlier if rel and relation_covers(idx, regions_of, rel["src"], e)), None)
            if rel is None:
                err(w, f"via_relation {rel_id!r} does not exist")
            elif src is None or not relation_covers(idx, regions_of, rel["dst"], at):
                err(w, f"relation {rel_id} ({rel['src']} → {rel['dst']}) does not connect an earlier step to {at}")
            chains.append([src or earlier[0], at])
            continue
        chain = next((c for c in (finder.path(e, at) for e in earlier) if c), None)
        if chain is None:
            err(w, f"no call path to {at} from any earlier step within {MAX_HOPS} hops (nearest: {earlier[0]}) — add "
                   f"the step in between (`codeatlas path` helps), or name the semantic relation that links them as `via_relation`")
            chains.append([])
        else:
            chains.append(chain)
    return chains
