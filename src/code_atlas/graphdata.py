"""The structure index loaded into memory, plus the derived graph quantities everyone needs:
file-to-file weights, PageRank importance and in-degrees."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .cluster import MAPPED_KINDS
from .index import connect, meta_get


def file_of(node: str) -> str:
    return node.split("#", 1)[0]


@dataclass
class Index:
    repo: Path
    files: dict[str, dict]                 # every discovered file
    mapped: dict[str, dict]                # files that go on the map
    symbols: dict[str, dict]               # id → symbol (mapped files only)
    edges: list[dict]
    fw: dict[tuple[str, str], float] = field(default_factory=dict)       # file → file weight
    ftypes: dict[tuple[str, str], set] = field(default_factory=dict)
    rank: dict[str, float] = field(default_factory=dict)                 # file PageRank, max = 1
    in_deg: dict[str, int] = field(default_factory=dict)                 # node id → incoming edge count
    aliases: dict[str, str] = field(default_factory=dict)
    meta: dict[str, str | None] = field(default_factory=dict)

    def exists(self, node: str) -> bool:
        return node in self.mapped or node in self.symbols

    def canonical(self, node: str) -> str:
        """Follow rename aliases to the node's current id."""
        seen = set()
        while node not in self.mapped and node not in self.symbols and node in self.aliases and node not in seen:
            seen.add(node)
            node = self.aliases[node]
        return node

    def anchor(self, node: str) -> str | None:
        """Content hash an annotation of `node` is written against."""
        if node in self.symbols:
            return self.symbols[node]["hash"]
        if node in self.mapped:
            return self.mapped[node].get("anchor_hash") or self.mapped[node]["hash"]
        return None


def pagerank(nodes: list[str], weight: dict[tuple[str, str], float]) -> dict[str, float]:
    import igraph
    if not nodes:
        return {}
    idx = {n: i for i, n in enumerate(nodes)}
    es = [(idx[a], idx[b]) for (a, b) in weight if a in idx and b in idx]
    g = igraph.Graph(n=len(nodes), edges=es, directed=True)
    pr = g.pagerank(weights=[weight[(nodes[a], nodes[b])] for a, b in es] if es else None)
    top = max(pr) or 1.0
    return {n: pr[i] / top for n, i in idx.items()}


def load_index(repo: Path) -> Index:
    db = connect(repo)
    files = {r["path"]: dict(r) for r in db.execute("select * from files")}
    mapped = {p: f for p, f in files.items() if f["kind"] in MAPPED_KINDS}
    symbols = {r["id"]: dict(r) for r in db.execute("select * from symbols") if r["path"] in mapped}
    edges = [dict(r) for r in db.execute("select * from edges")]
    aliases = {r["old"]: r["new"] for r in db.execute("select old, new from aliases")}
    meta = {k: meta_get(db, k) for k in ("commit", "scanned_at")}
    db.close()
    fw: dict[tuple[str, str], float] = defaultdict(float)
    ftypes: dict[tuple[str, str], set] = defaultdict(set)
    in_deg: dict[str, int] = defaultdict(int)
    for e in edges:
        in_deg[e["dst"]] += e["count"]
        a, b = file_of(e["src"]), file_of(e["dst"])
        if a != b and a in mapped and b in mapped:
            fw[(a, b)] += e["count"] * e["conf"]
            ftypes[(a, b)].add(e["type"])
    return Index(repo, files, mapped, symbols, edges, dict(fw), dict(ftypes), pagerank(list(mapped), fw),
                 dict(in_deg), aliases, meta)
