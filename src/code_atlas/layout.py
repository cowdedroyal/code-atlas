"""Nested layout: sizes bottom-up (symbol → file → region), positions top-down.

Every file reserves the rectangle its symbols need, so zooming from L1 (file = dot) to L2
(file = box of symbols) never makes boxes overlap. Inside a region, files are ordered so that files
which depend on each other sit next to each other, then packed in rows.
Units are abstract; one leaf symbol is LEAF_W wide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

LEAF_W = 160.0
GAP = 8.0
PAD = 10.0
HEADER = 24.0
REGION_HEADER = 56.0
REGION_PAD = 28.0
SMALL_KINDS = {"constant", "variable", "field", "key", "stage", "target"}


@dataclass
class Box:
    id: str
    w: float = 0.0
    h: float = 0.0
    x: float = 0.0
    y: float = 0.0
    children: list["Box"] = field(default_factory=list)


def shelf(items: list[Box], max_w: float, gap: float = GAP) -> tuple[float, float]:
    """Place items left→right in rows (positions relative to the container's content origin)."""
    x = y = row_h = used_w = 0.0
    for b in items:
        if x > 0 and x + b.w > max_w:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        b.x, b.y = x, y
        x += b.w + gap
        row_h = max(row_h, b.h)
        used_w = max(used_w, x - gap)
    return used_w, y + row_h


def _target_width(items: list[Box], aspect: float = 1.25) -> float:
    area = sum((b.w + GAP) * (b.h + GAP) for b in items)
    return max(max((b.w for b in items), default=LEAF_W), math.sqrt(area * aspect))


def leaf_height(lines: int) -> float:
    return min(140.0, 22.0 + 4.0 * math.sqrt(max(1, lines)))


def align_translation(boxes: dict, previous: dict, anchors: list[str]) -> dict:
    """Constrained Procrustes alignment: translation only preserves axis-aligned packing."""
    common = [key for key in anchors if key in boxes and key in previous]
    if not common:
        return boxes
    dx = sum(previous[k][0] + previous[k][2] / 2 - boxes[k][0] - boxes[k][2] / 2 for k in common) / len(common)
    dy = sum(previous[k][1] + previous[k][3] / 2 - boxes[k][1] - boxes[k][3] / 2 for k in common) / len(common)
    return {k: (b[0] + dx, b[1] + dy, b[2], b[3]) for k, b in boxes.items()}


def symbol_box(sym: dict, kids: dict[str, list[dict]]) -> Box:
    b = Box(sym["id"])
    children = kids.get(sym["id"], [])
    if children:
        b.children = [symbol_box(c, kids) for c in children]
        w, h = shelf(b.children, _target_width(b.children, 1.6))
        b.w, b.h = w + 2 * PAD, h + HEADER + PAD
    elif sym["kind"] in SMALL_KINDS:
        b.w, b.h = LEAF_W, 18.0
    else:
        b.w, b.h = LEAF_W, leaf_height(sym["end"] - sym["start"] + 1)
    return b


def file_box(path: str, lines: int, top_syms: list[dict], kids: dict[str, list[dict]]) -> Box:
    b = Box(path)
    if top_syms:
        b.children = [symbol_box(s, kids) for s in top_syms]
        w, h = shelf(b.children, _target_width(b.children, 1.4))
        b.w, b.h = max(w + 2 * PAD, LEAF_W), h + HEADER + PAD
    else:
        b.w, b.h = LEAF_W, HEADER + min(40.0, 4.0 * math.sqrt(max(1, lines or 1)))
    return b


def dependency_order(files: list[str], weight: dict[tuple[str, str], float], rank: dict[str, float]) -> list[str]:
    """Greedy walk: start at the most important file, keep appending its strongest unplaced neighbour."""
    nbr: dict[str, dict[str, float]] = {f: {} for f in files}
    for (a, b), wgt in weight.items():
        if a in nbr and b in nbr:
            nbr[a][b] = nbr[a].get(b, 0) + wgt
            nbr[b][a] = nbr[b].get(a, 0) + wgt
    left = set(files)
    order: list[str] = []
    by_rank = sorted(files, key=lambda f: (-rank.get(f, 0), f))
    frontier: dict[str, float] = {}
    while left:
        if frontier:
            nxt = max(frontier, key=lambda f: (frontier[f], rank.get(f, 0), f))
        else:
            nxt = next(f for f in by_rank if f in left)
        frontier.pop(nxt, None)
        left.discard(nxt)
        order.append(nxt)
        for m, wgt in nbr[nxt].items():
            if m in left:
                frontier[m] = frontier.get(m, 0) + wgt
    return order


def layout(regions: dict, files: dict[str, dict], symbols: list[dict],
           file_weight: dict[tuple[str, str], float], rank: dict[str, float], previous: dict | None = None) -> dict[str, tuple[float, float, float, float]]:
    """→ id → (x, y, w, h) for every region, file and symbol."""
    kids: dict[str, list[dict]] = {}
    top: dict[str, list[dict]] = {}
    previous = previous or {}

    def pack(b: Box, header: float, pad: float, gap: float):
        old = previous.get(b.id)
        occupied = []
        pending = []
        def clear(x, y, child):
            return all(x >= q.x + q.w + gap or x + child.w + gap <= q.x or
                       y >= q.y + q.h + gap or y + child.h + gap <= q.y for q in occupied)
        # Unchanged siblings retain their local coordinates first. Growing siblings
        # that would overlap are relocated; removing a child does not shrink a parent.
        for child in sorted(b.children, key=lambda c: (not bool(previous.get(c.id)), c.id)):
            prior = previous.get(child.id)
            if old and prior:
                x, y = prior[0] - old[0] - pad, prior[1] - old[1] - header
                if x >= 0 and y >= 0 and clear(x, y, child):
                    child.x, child.y = x, y
                    occupied.append(child)
                    continue
            pending.append(child)
        for child in pending:
            if not occupied:
                child.x = child.y = 0
            else:
                # Candidate gaps beside existing neighbors, rather than recomputing rows.
                candidates = [(q.x + q.w + gap, q.y) for q in occupied] + [(q.x, q.y + q.h + gap) for q in occupied]
                valid = [(x, y) for x, y in candidates if clear(x, y, child)]
                child.x, child.y = min(valid, key=lambda pt: (max(pt[0] + child.w, pt[1] + child.h), pt[1], pt[0]))
            occupied.append(child)
        b.w = max([LEAF_W, *(c.x + c.w + 2 * pad for c in occupied), old[2] if old else 0])
        b.h = max([header, *(c.y + c.h + header + pad for c in occupied), old[3] if old else 0])

    def stabilize(b: Box):
        for child in b.children:
            stabilize(child)
        if b.children and b.id in previous:
            pack(b, HEADER, PAD, GAP)
        elif b.id in previous:
            b.w, b.h = max(b.w, previous[b.id][2]), max(b.h, previous[b.id][3])
    for s in sorted(symbols, key=lambda s: (s["start"], s["id"])):
        if s["parent"] == s["path"]:
            top.setdefault(s["path"], []).append(s)
        else:
            kids.setdefault(s["parent"], []).append(s)

    def region_box(rid: str) -> Box:
        r = regions[rid]
        b = Box(rid)
        subs = sorted((region_box(c) for c in r.children), key=lambda x: (-x.h, x.id))
        order = dependency_order([f for f in r.files if f in files], file_weight, rank)
        fboxes = [file_box(p, files[p].get("lines") or 1, top.get(p, []), kids) for p in order]
        for f in fboxes:
            stabilize(f)
        items = subs + fboxes
        if not items:
            b.w, b.h = LEAF_W, REGION_HEADER
            return b
        b.children = items
        if b.id in previous:
            pack(b, REGION_HEADER, REGION_PAD, REGION_PAD if subs else GAP * 2)
        else:
            w, h = shelf(items, _target_width(items, 1.5), gap=REGION_PAD if subs else GAP * 2)
            b.w, b.h = w + 2 * REGION_PAD, h + REGION_HEADER + REGION_PAD
        return b

    root_id = next(rid for rid, r in regions.items() if r.parent is None)
    root = region_box(root_id)
    out: dict[str, tuple[float, float, float, float]] = {}

    def place(b: Box, ox: float, oy: float, header: float, pad: float):
        out[b.id] = (ox, oy, b.w, b.h)
        for c in b.children:
            is_region = c.id in regions
            cx, cy = ox + pad + c.x, oy + header + c.y
            if is_region:
                place(c, cx, cy, REGION_HEADER, REGION_PAD)
            else:
                place(c, cx, cy, HEADER, PAD)

    place(root, 0.0, 0.0, REGION_HEADER, REGION_PAD)
    return out
