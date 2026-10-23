"""Which region each file is in: the agent-written regions when there are any, the draft regions otherwise.

Files no rule claims yet go into an "Unassigned" region that keeps their draft (directory) structure,
so the map is always complete and the gap is visible.
"""

from __future__ import annotations

import posixpath
from functools import lru_cache

import pathspec

from ..cluster import Region, draft_regions
from .store import REGION_PREFIX, Semantic

UNASSIGNED = REGION_PREFIX + "_unassigned"


@lru_cache(maxsize=4096)
def compile_glob(pattern: str) -> pathspec.PathSpec:
    return pathspec.PathSpec.from_lines("gitignore", [pattern])


def match_rules(rules: list[dict], paths) -> tuple[dict[str, str], dict[str, list[str]]]:
    """→ (path → region slug, path → secondary slugs). Later rules win."""
    primary: dict[str, str] = {}
    also: dict[str, list[str]] = {}
    specs = [(compile_glob(r["match"]), r) for r in rules]
    for p in paths:
        for spec, r in specs:
            if spec.match_file(p):
                primary[p] = r["region"]
                also[p] = list(r.get("also") or [])
    return primary, also


def effective_regions(mapped: dict[str, dict], sem: Semantic, repo_name: str, unassigned_name: str = "Unassigned", dependency_weights: dict | None = None):
    """→ (regions by id, file → region id, file → secondary region ids)."""
    if not sem.regions:
        regions, assign = draft_regions(list(mapped.values()), repo_name, dependency_weights)
        return regions, assign, {}
    root = Region("r:", repo_name, "", None, auto=False)
    regions: dict[str, Region] = {root.id: root}
    for r in sem.regions:
        rid = REGION_PREFIX + r["id"]
        regions[rid] = Region(rid, r["name"], "", None, role=r.get("role") or "core", auto=False,
                              responsibility=r.get("responsibility"))
    for r in sem.regions:
        rid = REGION_PREFIX + r["id"]
        parent = REGION_PREFIX + r["parent"] if r.get("parent") and REGION_PREFIX + r["parent"] in regions else root.id
        regions[rid].parent = parent
        regions[parent].children.append(rid)
    primary, also = match_rules(sem.rules, sorted(mapped))
    assign: dict[str, str] = {}
    secondary: dict[str, list[str]] = {}
    left = []
    for p in sorted(mapped):
        slug = primary.get(p)
        rid = REGION_PREFIX + slug if slug else None
        if rid in regions:
            assign[p] = rid
            regions[rid].files.append(p)
            secondary[p] = [REGION_PREFIX + s for s in also.get(p, []) if REGION_PREFIX + s in regions]
        else:
            left.append(p)
    for rid in [r for r in regions if r.startswith(REGION_PREFIX)]:
        _subgroup(regions, regions[rid], mapped, assign)
    if left:
        un = Region(UNASSIGNED, unassigned_name, "", root.id, role="unassigned", auto=True)
        regions[un.id] = un
        root.children.append(un.id)
        draft, dassign = draft_regions([mapped[p] for p in left], repo_name, dependency_weights)
        draft_root = next(r for r in draft.values() if r.parent is None)
        for d in draft.values():
            if d.id == draft_root.id:
                continue
            if d.parent == draft_root.id:
                d.parent = un.id
                un.children.append(d.id)
            regions[d.id] = d
        un.files = list(draft_root.files)
        for p, did in dassign.items():
            assign[p] = un.id if did == draft_root.id else did
    _set_paths(regions, root.id)
    return regions, assign, secondary


SUBGROUP_MIN_FILES = 12


def owner_of(regions: dict[str, Region], rid: str) -> str:
    """The semantic region (or UNASSIGNED / the root) a possibly auto-generated group belongs to."""
    r = regions[rid]
    while not r.id.startswith(REGION_PREFIX) and r.parent:
        r = regions[r.parent]
    return r.id


def _subgroup(regions: dict[str, Region], r: Region, mapped: dict[str, dict], assign: dict[str, str]) -> None:
    """A big region keeps its directory structure inside it, as unnamed (auto) groups, so its files
    don't collapse into one flat grid. The agent decides the region's boundary; directories decide the layout within."""
    if len(r.files) < SUBGROUP_MIN_FILES:
        return
    draft, dassign = draft_regions([mapped[p] for p in r.files], r.name)
    droot = next(d for d in draft.values() if d.parent is None)
    if not droot.children:
        return
    rename = {d.id: f"r:{r.id[len(REGION_PREFIX):]}|{d.path}" for d in draft.values() if d.id != droot.id}
    for old, d in draft.items():
        if old == droot.id:
            continue
        d.id = rename[old]
        d.parent = r.id if d.parent == droot.id else rename[d.parent]
        d.children = [rename[c] for c in d.children]
        d.role = r.role
        regions[d.id] = d
    r.children = [rename[c] for c in droot.children] + r.children
    r.files = list(droot.files)
    for p, did in dassign.items():
        assign[p] = r.id if did == droot.id else rename[did]


def _set_paths(regions: dict[str, Region], rid: str) -> list[str]:
    """A semantic region's `path` is the deepest directory containing all its files."""
    r = regions[rid]
    files = list(r.files)
    for c in r.children:
        files.extend(_set_paths(regions, c))
    if not r.path and files and r.id.startswith(REGION_PREFIX):
        dirs = [posixpath.dirname(f) for f in files]
        common = posixpath.commonpath(dirs) if all(dirs) else ""
        r.path = common
    return files
