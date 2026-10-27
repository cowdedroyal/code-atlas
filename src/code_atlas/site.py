"""Build the static site: viewer assets + JSON data. No server process is needed to browse it.

    site/index.html, assets/…             the viewer (built from frontend/, shipped inside the wheel)
    site/data/graph.json                  regions, files, file edges, region edges, relations, tours (L0/L1)
    site/data/search.json                 every symbol's name, kind and box — fetched on first search
    site/data/chunks/<n>.json             symbols + symbol edges of a group of files, ≤ CHUNK_SYMBOLS symbols (L2/L3)
    site/data/code/<n>.json               source text of that chunk's files (omitted with --no-source)
    site/data/explainers/<id>.html        explainer cards, packed (kit and CSP inlined); listed in graph.json
"""

from __future__ import annotations

import json
import hashlib
import shutil
from collections import defaultdict
from pathlib import Path

from . import __version__
from .cluster import MAPPED_KINDS
from .graphdata import file_of, load_index
from .layout import align_translation, layout
from .index import cache_dir
from .semantic import store
from .explainer_kit.pack import inline_media, lint_card, load_media, media_refs, pack_html
from .semantic.agenda import staleness
from .semantic.explainers import card_status, cards_dir, load_cards
from .semantic.tours import tour_status
from .semantic.regions import effective_regions
from .semantic.store import REGION_PREFIX
from .highlight import has_browser_grammar, source_tokens
from .module_scope import declaration_folds, execution_symbols

WEB_DIR = Path(__file__).parent / "web"


def _node_ref(node: str, r_index: dict, f_index: dict) -> dict | None:
    if node.startswith(REGION_PREFIX):
        return {"t": "region", "i": r_index[node]} if node in r_index else None
    path = file_of(node)
    if path not in f_index:
        return None
    return {"t": "symbol", "id": node, "file": f_index[path]} if "#" in node else {"t": "file", "i": f_index[path]}


def build_data(repo: Path, include_source: bool = True, stable_layout: bool = False, repack: bool = False) -> dict:
    idx = load_index(repo)
    sem = store.load(repo)
    files, mapped, edges, fw, ftypes, rank = idx.files, idx.mapped, idx.edges, idx.fw, idx.ftypes, idx.rank
    syms, symbol_edges, module_scopes = execution_symbols(mapped, idx.symbols, edges)
    original_by_file: dict[str, list[dict]] = defaultdict(list)
    for s in idx.symbols.values():
        original_by_file[s["path"]].append(s)
    zh = str(sem.config.get("language", "en")).lower().startswith("zh")
    regions, assign, secondary = effective_regions(mapped, sem, repo.name, "未归属" if zh else "Unassigned", fw)
    stale = staleness(repo, idx, sem)
    note = lambda nid: ({"note": sem.notes[nid]["text"], **({"stale": stale[nid]} if nid in stale else {})}
                        if nid in sem.notes else {})
    layout_cache = cache_dir(repo) / "layout.json"
    previous = {}
    if stable_layout and layout_cache.exists():
        try: previous = json.loads(layout_cache.read_text("utf-8"))
        except (ValueError, OSError): pass
    boxes = layout(regions, mapped, syms, fw, rank, None if repack else previous)
    if repack:
        boxes = align_translation(boxes, previous, list(mapped))

    # region tree helpers
    depth: dict[str, int] = {}
    def d(rid):
        if rid not in depth:
            p = regions[rid].parent
            depth[rid] = 0 if p is None else d(p) + 1
        return depth[rid]
    root_id = next(r for r in regions if regions[r].parent is None)

    total: dict[str, int] = {}
    def count(rid):  # files in the region and everything under it
        if rid not in total:
            total[rid] = len(regions[rid].files) + sum(count(c) for c in regions[rid].children)
        return total[rid]
    region_list = sorted(regions.values(), key=lambda r: (d(r.id), r.id))
    r_index = {r.id: i for i, r in enumerate(region_list)}
    file_list = sorted(mapped, key=lambda p: boxes[p][1] * 1e6 + boxes[p][0])
    f_index = {p: i for i, p in enumerate(file_list)}
    chunk_of_file = _chunk_files(regions, root_id, syms, boxes)
    n_chunks = max(chunk_of_file.values(), default=-1) + 1

    # region edges: aggregate each file edge onto the two sibling regions under their common ancestor
    def chain(rid):
        out = [rid]
        while regions[out[-1]].parent is not None:
            out.append(regions[out[-1]].parent)
        return out[::-1]
    redge: dict[tuple[str, str], float] = defaultdict(float)
    for (a, b), wgt in fw.items():
        ca, cb = chain(assign[a]), chain(assign[b])
        i = 0
        while i < min(len(ca), len(cb)) and ca[i] == cb[i]:
            i += 1
        if i < len(ca) and i < len(cb):
            redge[(ca[i], cb[i])] += wgt

    in_deg = idx.in_deg

    graph = {
        "version": __version__,
        "meta": {"repo": repo.name, "commit": idx.meta.get("commit"), "scanned_at": idx.meta.get("scanned_at"),
                 "semantic": bool(sem.regions), "language": sem.config.get("language", "en"),
                 "files_total": len(files), "files_mapped": len(mapped), "symbols": len(syms), "edges": len(edges),
                 "assets": _asset_counts(files)},
        "regions": [{"id": r.id, "name": r.name, "path": r.path, "parent": r_index.get(r.parent) if r.parent else None,
                     "role": r.role, "auto": r.auto, "depth": d(r.id), "box": _round(boxes[r.id]),
                     "files": count(r.id),
                     **({"responsibility": r.responsibility} if r.responsibility else {}), **note(r.id)}
                    for r in region_list],
        "files": [{"id": p, "region": r_index[assign[p]], "kind": mapped[p]["kind"], "lang": mapped[p]["lang"],
                   "box": _round(boxes[p]), "lines": mapped[p]["lines"], "rank": round(rank.get(p, 0), 4),
                   "entry": bool(mapped[p]["entry"]), "chunk": chunk_of_file[p],
                   "in": in_deg.get(p, 0), **({"doc": mapped[p]["doc"]} if mapped[p].get("doc") else {}),
                   **({"module_scope": module_scopes[p]} if p in module_scopes else {}),
                   **({"also": [r_index[x] for x in secondary[p]]} if secondary.get(p) else {}), **note(p)}
                  for p in file_list],
        "file_edges": [[f_index[a], f_index[b], round(w, 2), sorted(ftypes[(a, b)])] for (a, b), w in fw.items()],
        "region_edges": [[r_index[a], r_index[b], round(w, 2)] for (a, b), w in redge.items()],
        "chunks": n_chunks,
        "relations": [rel for rel in (
            {"id": r["id"], "src": _node_ref(r["src"], r_index, f_index), "dst": _node_ref(r["dst"], r_index, f_index),
             "family": r["family"], "label": r["label"], "rationale": r["rationale"], "evidence": r["evidence"],
             **({"stale": stale[r["id"]]} if r["id"] in stale else {})}
            for r in sem.relations.values()) if rel["src"] and rel["dst"]],
        "tours": _tours(sem, tour_status(idx, sem), boxes, r_index, f_index),
    }
    cards = load_cards(repo)
    graph["explainers"], card_pages, skipped_cards = _explainers(repo, cards, card_status(idx, sem, cards))
    # search index (name, kind, file index, id, box): its own file, fetched on first search
    search = [[s["name"], s["kind"], f_index[s["path"]], s["id"], *_round(boxes[s["id"]])] for s in syms]
    chunks = []
    syntax = []
    by_chunk_syms: dict[int, list] = defaultdict(list)
    for s in syms:
        by_chunk_syms[graph["files"][f_index[s["path"]]]["chunk"]].append(s)
    sym_ids = {s["id"] for s in syms}
    by_chunk_edges: dict[int, list] = defaultdict(list)
    symbol_in: dict[str, int] = defaultdict(int)
    for e in symbol_edges:  # stored with both endpoints' chunks, so callers are found from either side
        if not (e["src"] in sym_ids or e["dst"] in sym_ids):
            continue
        row = [e["src"], e["dst"], e["type"], round(e["conf"], 2), e["count"]]
        symbol_in[e["dst"]] += e["count"]
        cs = {graph["files"][f_index[f]]["chunk"] for f in (file_of(e["src"]), file_of(e["dst"])) if f in f_index}
        for c in cs:
            by_chunk_edges[c].append(row)
    files_by_chunk: dict[int, list[str]] = defaultdict(list)
    for p in file_list:
        files_by_chunk[chunk_of_file[p]].append(p)
    for ci in range(n_chunks):
        chunk = {"symbols": [{"id": s["id"], "file": f_index[s["path"]], "name": s["name"], "kind": s["kind"],
                              "parent": s["parent"] if s["parent"] != s["path"] else None, "box": _round(boxes[s["id"]]),
                              "sig": s["sig"], "doc": s["doc"], "start": s["start"], "end": s["end"],
                              "in": symbol_in.get(s["id"], 0), **note(s["id"])}
                             for s in by_chunk_syms.get(ci, [])],
                 "edges": by_chunk_edges.get(ci, [])}
        code = {}
        syntax_chunk = {}
        if include_source:
            for p in files_by_chunk.get(ci, []):
                try:
                    raw = (repo / p).read_bytes()
                    if hashlib.sha1(raw).hexdigest() != mapped[p]["hash"]:
                        raise ValueError(f"Source changed since scan: {p}. Run codeatlas scan and build again.")
                    code[p] = raw.decode("utf-8", "replace")
                    if p in module_scopes:
                        try:
                            folds = declaration_folds(code[p], mapped[p]["lang"], original_by_file[p])
                            scope = next(s for s in chunk["symbols"] if s["id"] == module_scopes[p])
                            scope["folds"] = folds
                        except Exception:
                            pass  # retain context if a grammar cannot determine safe folds
                    if mapped[p].get("lang") and not has_browser_grammar(mapped[p]["lang"]):
                        try:
                            syntax_chunk[p] = source_tokens(code[p], mapped[p]["lang"])
                        except Exception:
                            pass
                except OSError:
                    pass
        chunks.append((chunk, code))
        syntax.append(syntax_chunk)
    if stable_layout:
        layout_cache.write_text(json.dumps(boxes), "utf-8")
    return {"graph": graph, "chunks": chunks, "search": search, "syntax": syntax, "cards": card_pages,
            "skipped_cards": skipped_cards}


CHUNK_SYMBOLS = 2500  # a chunk is fetched when any of its files opens to L2; keep each one small


def _chunk_files(regions: dict, root_id: str, syms: list[dict], boxes: dict) -> dict[str, int]:
    """file → chunk id. Walk the region tree; a subtree under the symbol cap is one chunk, a bigger one is
    split into its children, and a region's own files are packed into chunks in layout order."""
    per_file: dict[str, int] = defaultdict(int)
    for s in syms:
        per_file[s["path"]] += 1
    weight: dict[str, int] = {}

    def w(rid):
        if rid not in weight:
            r = regions[rid]
            weight[rid] = sum(per_file[f] for f in r.files) + sum(w(c) for c in r.children)
        return weight[rid]

    out: dict[str, int] = {}
    nxt = [0]

    def files_under(rid):
        r = regions[rid]
        return list(r.files) + [f for c in r.children for f in files_under(c)]

    def assign(rid):
        r = regions[rid]
        if w(rid) <= CHUNK_SYMBOLS:
            for f in files_under(rid):
                out[f] = nxt[0]
            nxt[0] += 1
            return
        for c in r.children:
            assign(c)
        batch = 0
        for f in sorted(r.files, key=lambda p: (boxes[p][1], boxes[p][0])):
            if batch and batch + per_file[f] > CHUNK_SYMBOLS:
                nxt[0] += 1
                batch = 0
            out[f] = nxt[0]
            batch += per_file[f]
        if r.files:
            nxt[0] += 1

    assign(root_id)
    return out


def _tours(sem, status: dict, boxes: dict, r_index: dict, f_index: dict) -> list[dict]:
    """Tours with everything the player needs: each step's box, and the hop points of the call chain into it."""
    center = lambda b: [round(b[0] + b[2] / 2, 1), round(b[1] + b[3] / 2, 1)]
    out = []
    for tid in sorted(sem.tours):
        t, st = sem.tours[tid], status.get(tid, {})
        chains = st.get("chains", [])
        steps = []
        ats = [s.get("at") for s in t.get("steps", [])]
        for i, s in enumerate(t.get("steps", [])):
            ref = _node_ref(s["at"], r_index, f_index)
            if ref is None or s["at"] not in boxes:
                continue
            chain = chains[i] if i < len(chains) else []
            # the leg starts at the earlier step the chain was found from (often the orchestrator, not i-1)
            frm = next((j for j in range(i - 1, -1, -1) if chain and ats[j] == chain[0]), i - 1 if i else None)
            hops = [center(boxes[h]) for h in chain[1:-1] if h in boxes]
            steps.append({"at": s["at"], "ref": ref, "text": s.get("text"), "box": _round(boxes[s["at"]]), "hops": hops,
                          **({"from": frm} if frm is not None else {}),
                          **({"via_relation": s["via_relation"]} if s.get("via_relation") else {}),
                          **({"state": st["steps"][i]} if i < len(st.get("steps", [])) and st["steps"][i] else {})})
        if len(steps) >= 2:
            out.append({"id": tid, "title": t.get("title"), "summary": t.get("summary"), "steps": steps,
                        **({"state": st["state"]} if st.get("state") else {})})
    return out


def _explainers(repo: Path, cards: dict[str, dict], state: dict[str, dict]) -> tuple[list[dict], dict[str, str], dict]:
    """Card list for graph.json, the packed page of each, and the cards left out because they no longer lint."""
    listed, pages, skipped = [], {}, {}
    for cid in sorted(cards):
        c, d = cards[cid], cards_dir(repo) / cid
        problems = lint_card(d)
        if problems:
            skipped[cid] = problems[:5]
            continue
        html = (d / "index.html").read_text("utf-8")
        manifests, errors = load_media(d)
        if errors:
            skipped[cid] = errors[:5]
            continue
        pages[cid] = pack_html(html, c, inline_media(d, manifests, media_refs(html)))
        listed.append({"id": cid, "title": c["title"], "summary": c["summary"], "questions": c["questions"],
                       "anchors": c["anchors"], "tags": c.get("tags", []), "language": c.get("language"),
                       "at": c.get("at"), **({"state": state[cid]["state"], "stale_nodes": state[cid]["nodes"]}
                                             if cid in state else {})})
    return listed, pages, skipped


def _asset_counts(files: dict) -> dict:
    out: dict[str, int] = defaultdict(int)
    for f in files.values():
        if f["kind"] not in MAPPED_KINDS:
            out[f["kind"]] += 1
    return dict(out)


def _round(b):
    return [round(v, 1) for v in b]


def build_site(repo: Path, out: Path, include_source: bool = True, refresh_index: bool = True, repack: bool = False) -> dict:
    if refresh_index:
        from .scan import scan
        scan(repo)
    data = build_data(repo, include_source, stable_layout=True, repack=repack)
    out.mkdir(parents=True, exist_ok=True)
    if WEB_DIR.exists():
        if (out / "assets").exists():  # old hashed bundles would pile up otherwise
            shutil.rmtree(out / "assets")
        for item in WEB_DIR.iterdir():
            dst = out / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
    ddir = out / "data"
    if ddir.exists():
        shutil.rmtree(ddir)
    (ddir / "chunks").mkdir(parents=True)
    (ddir / "code").mkdir()
    if include_source:
        (ddir / "syntax").mkdir()
    dump = lambda obj, path: path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    dump(data["graph"], ddir / "graph.json")
    dump(data["search"], ddir / "search.json")
    for i, (chunk, code) in enumerate(data["chunks"]):
        dump(chunk, ddir / "chunks" / f"{i}.json")
        if include_source:
            dump(code, ddir / "code" / f"{i}.json")
            if data["syntax"][i]:
                dump(data["syntax"][i], ddir / "syntax" / f"{i}.json")
    if data["cards"]:
        (ddir / "explainers").mkdir()
        for cid, page in data["cards"].items():
            (ddir / "explainers" / f"{cid}.html").write_text(page, encoding="utf-8")
    g = data["graph"]
    return {"ok": True, "out": str(out), "regions": len(g["regions"]), "files": len(g["files"]),
            "symbols": g["meta"]["symbols"], "chunks": g["chunks"], "viewer": WEB_DIR.exists(),
            "explainers": len(g["explainers"]), **({"skipped_cards": data["skipped_cards"]} if data["skipped_cards"] else {})}
