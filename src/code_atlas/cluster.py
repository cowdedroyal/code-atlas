"""Draft regions: what the map shows before any agent has looked at the code.

The directory tree is the author's own first cut at "what belongs together", so draft regions follow
it, with two clean-ups: chains of single-child directories collapse into one region (`src/app/core`),
and directories too small to be worth a region fold into their parent. Each draft region gets a role
guessed from its name so the map is coloured from the start; agents replace both with real ones.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

MAPPED_KINDS = {"source", "test", "config", "docs"}
MIN_REGION_FILES = 3

ROLE_HINTS = [
    ("tooling", r"^(tests?|__tests__|spec|e2e|scripts?|tools?|bin|ci|\.github|deploy|docker|infra|benchmarks?|examples?|docs?|notebooks?|experiments?|analy[sz]e)$"),
    ("presentation", r"^(web|ui|frontend|front|client-web|components?|views?|pages?|screens?|widgets?|templates?|styles?|static|public|assets?|app-web)$"),
    ("interface", r"^(api|apis|routes?|routers?|server|servers|handlers?|controllers?|endpoints?|cli|cmd|commands?|gateway|rpc|grpc|graphql|mcp_servers?)$"),
    ("state", r"^(models?|schemas?|entities|db|database|migrations?|store|stores|storage|repositor(y|ies)|dao|orm|data|types?|dto)$"),
    ("integration", r"^(clients?|sdk|adapters?|integrations?|providers?|connectors?|external|vendors?|llm|plugins?)$"),
    ("orchestration", r"^(pipelines?|workflows?|jobs?|tasks?|workers?|orchestrat\w*|scheduler|flows?|agents?|runners?)$"),
    ("foundation", r"^(utils?|util|common|shared|_shared|lib|libs|core-utils|helpers?|config|configs|settings|logging|internal|pkg|base)$"),
]


@dataclass
class Region:
    id: str
    name: str
    path: str                 # directory prefix ("" = repo root)
    parent: str | None
    role: str = "core"
    auto: bool = True
    children: list[str] = field(default_factory=list)
    responsibility: str | None = None
    files: list[str] = field(default_factory=list)


def guess_role(name: str) -> str:
    last = name.split("/")[-1].lower()
    for role, pat in ROLE_HINTS:
        if re.match(pat, last):
            return role
    return "core"


def draft_regions(files: list[dict], repo_name: str, dependency_weights: dict | None = None) -> tuple[dict[str, Region], dict[str, str]]:
    """files: [{"path", "kind"}] → (regions by id, file path → region id)."""
    mapped = [f["path"] for f in files if f["kind"] in MAPPED_KINDS]
    tree: dict[str, set[str]] = {"": set()}
    direct: dict[str, list[str]] = {"": []}
    for p in mapped:
        d = posixpath.dirname(p)
        direct.setdefault(d, []).append(p)
        while True:
            tree.setdefault(d, set())
            if d == "":
                break
            parent = posixpath.dirname(d)
            tree.setdefault(parent, set()).add(d)
            d = parent
    weight: dict[str, int] = {}

    def w(d):
        if d not in weight:
            weight[d] = len(direct.get(d, [])) + sum(w(c) for c in tree.get(d, ()))
        return weight[d]
    w("")

    regions: dict[str, Region] = {}
    assign: dict[str, str] = {}
    root = Region("r:", repo_name, "", None, role="core")
    regions[root.id] = root

    def files_under(d):
        out = list(direct.get(d, []))
        for c in tree.get(d, ()):
            out.extend(files_under(c))
        return out

    def build(d: str, region: Region):
        for p in direct.get(d, []):
            region.files.append(p)
            assign[p] = region.id
        for c in sorted(tree.get(d, ())):
            if w(c) < MIN_REGION_FILES:
                for p in files_under(c):
                    region.files.append(p)
                    assign[p] = region.id
                continue
            # collapse a chain of single-child, file-less directories
            end = c
            while not direct.get(end) and len(tree.get(end, ())) == 1:
                end = next(iter(tree[end]))
            name = c.split("/")[-1] if end == c else c.split("/")[-1] + "/" + end[len(c) + 1:]
            r = Region(f"r:{end}", name, end, region.id, role=guess_role(end))
            regions[r.id] = r
            region.children.append(r.id)
            build(end, r)

    build("", root)
    # a root whose only content is one region: that region is the root
    if not root.files and len(root.children) == 1:
        only = regions.pop(root.children[0])
        root.children, root.files = only.children, only.files
        for cid in root.children:
            regions[cid].parent = root.id
        for p in root.files:
            assign[p] = root.id
    if dependency_weights:
        _communities(regions, assign, dependency_weights)
    return regions, assign


def _communities(regions, assign, weights):
    """Split large directory leaves by weighted Leiden communities, never agent regions."""
    import igraph
    import random
    igraph.set_random_number_generator(random.Random(0))
    try:
        for region in list(regions.values()):
            if region.children or len(region.files) < 12:
                continue
            paths = sorted(region.files)
            index = {p: i for i, p in enumerate(paths)}
            pairs = {}
            for (a, b), weight in weights.items():
                if a in index and b in index and a != b:
                    pair = tuple(sorted((index[a], index[b])))
                    pairs[pair] = pairs.get(pair, 0) + weight
            if len(pairs) < len(paths) // 2:
                continue
            graph = igraph.Graph(n=len(paths), edges=list(pairs), directed=False)
            groups = graph.community_leiden(objective_function="modularity", weights=list(pairs.values()), n_iterations=4)
            meaningful = sorted(([paths[i] for i in group] for group in groups if len(group) >= MIN_REGION_FILES), key=lambda g: g[0])
            if len(meaningful) < 2:
                continue
            for i, members in enumerate(meaningful, 1):
                rid = region.id + f"|community-{i}"
                child = Region(rid, f"{region.name} · group {i}", region.path, region.id, role=region.role, files=members)
                regions[rid] = child
                region.children.append(rid)
                for p in members:
                    assign[p] = rid
                    region.files.remove(p)
    finally:
        igraph.set_random_number_generator(None)
