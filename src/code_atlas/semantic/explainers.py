"""Explainer cards: visual answers to a reader's questions, hung on the nodes they explain.

    .codemap/explainers/<id>/card.yaml        manifest (format and rules: explainer_kit/card.schema.json)
    .codemap/explainers/<id>/index.html       the page, written with the component kit; packed at build time
    .codemap/explainers/<id>/media/<name>.json, <name>.webp
                                              baked animation/chart assets the page names with `atlas-media:<name>`

`apply` is the only writer: it validates the card against the current index, records a content hash for every
anchor (so later code edits make the card stale, like notes and relations), logs the run and keeps a snapshot
for `revert`. The page is stored as written, with the kit referenced by placeholder, so diffs stay small;
`build` inlines the kit, the media and the Content-Security-Policy. Only the baked media is copied: the authored
sources (frames, a Motion Canvas project, a Vega-Lite spec) stay where the author keeps them, which is why a
missing source is not treated as stale.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

import yaml

from ..explainer_kit.pack import lint_card, load_manifest
from ..graphdata import Index, load_index
from ..index import cache_dir
from . import store
from .store import REGION_PREFIX

DIRNAME = "explainers"
MANIFEST_KEYS = ("kit", "id", "title", "questions", "summary", "anchors", "tags", "language", "anchor_hashes", "run", "at")
CONTENT_KEYS = ("kit", "title", "questions", "summary", "anchors", "tags", "language")


def cards_dir(repo: Path) -> Path:
    return store.root(repo) / DIRNAME


def load_cards(repo: Path) -> dict[str, dict]:
    out = {}
    d = cards_dir(repo)
    for f in sorted(d.glob("*/card.yaml")) if d.exists() else []:
        try:
            m = yaml.safe_load(f.read_text("utf-8")) or {}
        except yaml.YAMLError:
            continue
        if isinstance(m, dict) and m.get("id") == f.parent.name:
            out[m["id"]] = m
    return out


def _node_hash(idx: Index, node: str) -> str | None:
    return idx.anchor(node)


def _canonical(idx: Index, anchor: str) -> str:
    return anchor if anchor.startswith(REGION_PREFIX) else idx.canonical(anchor)


def _anchor_exists(idx: Index, sem: store.Semantic, node: str) -> bool:
    if node.startswith(REGION_PREFIX):
        return sem.region(node[len(REGION_PREFIX):]) is not None
    return idx.exists(node)


def card_status(idx: Index, sem: store.Semantic, cards: dict[str, dict]) -> dict[str, dict]:
    """card id → {"state": "stale"|"orphan", "nodes": [anchors that changed or vanished]}; clean cards are absent."""
    out = {}
    for cid, card in cards.items():
        gone, changed = [], []
        hashes = card.get("anchor_hashes") or {}
        for a in card.get("anchors", []):
            node = _canonical(idx, a)
            if not _anchor_exists(idx, sem, node):
                gone.append(a)
            elif not node.startswith(REGION_PREFIX) and hashes.get(a) and hashes[a] != _node_hash(idx, node):
                changed.append(a)
        if gone or changed:
            out[cid] = {"state": "orphan" if gone else "stale", "nodes": gone + changed}
    return out


def validate_card(repo: Path, src: Path, idx: Index | None = None) -> dict:
    """Dry run: lint the files, then check every anchor against the current index."""
    idx = idx or load_index(repo)
    if not idx.mapped:
        return {"ok": False, "errors": ["the structure index is empty — run `codeatlas scan` first"], "warnings": []}
    if not (src / "card.yaml").exists():
        return {"ok": False, "errors": [f"{src}: no card.yaml"], "warnings": []}
    errors = lint_card(src)
    warnings: list[str] = []
    manifest = load_manifest(src)
    sem = store.load(repo)
    anchors = manifest.get("anchors") if isinstance(manifest.get("anchors"), list) else []
    for a in anchors:
        if isinstance(a, str) and not _anchor_exists(idx, sem, _canonical(idx, a)):
            errors.append(f"card.yaml: anchor {a!r} does not exist in the index (run `codeatlas scan` if the code changed; "
                          "regions are written `region:<id>`)")
    cid = manifest.get("id")
    if isinstance(cid, str) and cid in load_cards(repo):
        warnings.append(f"a card `{cid}` already exists and will be replaced")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "id": cid}


def _write_card(dst: Path, manifest: dict, html: str) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    body = {k: manifest[k] for k in MANIFEST_KEYS if manifest.get(k) not in (None, [], {})}
    text = "# An explainer card. Written via `codeatlas card apply`.\n" + yaml.safe_dump(
        body, allow_unicode=True, sort_keys=False, width=110, default_flow_style=False)
    (dst / "card.yaml").write_text(text, encoding="utf-8", newline="\n")
    (dst / "index.html").write_text(html, encoding="utf-8", newline="\n")


def baked_media(card: Path) -> dict[str, bytes]:
    """The assets a card carries: baked manifests and sprite sheets, never the authored sources.

    A Vega-Lite spec (`*.vl.json`) and the frames a sheet was made from are inputs to `bake`, not part of the card:
    copying them would mean committing a Motion Canvas project (with `node_modules`) to the mapped repository.
    """
    d = card / "media"
    if not d.is_dir():
        return {}
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())
            if p.is_file() and (p.suffix == ".webp" or (p.suffix == ".json" and not p.name.endswith(".vl.json")))}


def _write_media(dst: Path, files: dict[str, bytes]) -> None:
    d = dst / "media"
    if not files:
        if d.is_dir():
            shutil.rmtree(d)
        return
    d.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (d / name).write_bytes(data)
    for p in d.iterdir():                      # a re-applied card must not keep a sheet it no longer uses
        if p.is_file() and p.name not in files:
            p.unlink()


def _record(repo: Path, run: str, at: str, before: dict, change: dict, intent: str) -> None:
    runs = cache_dir(repo) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"{run}.json").write_text(json.dumps({"before": before, "after": store.snapshot(repo)}, ensure_ascii=False),
                                      encoding="utf-8")
    store.append_log(repo, {"run": run, "at": at, "intent": intent, "changes": change})


def apply_card(repo: Path, src: Path, run: str | None = None, idx: Index | None = None) -> dict:
    idx = idx or load_index(repo)
    report = validate_card(repo, src, idx)
    if not report["ok"]:
        return report
    manifest = load_manifest(src)
    html = (src / "index.html").read_text("utf-8").replace("\r\n", "\n")
    cid = manifest["id"]
    old = load_cards(repo).get(cid)
    anchors = [_canonical(idx, a) for a in manifest["anchors"]]
    manifest["anchors"] = anchors
    hashes = {a: _node_hash(idx, a) for a in anchors if not a.startswith(REGION_PREFIX)}
    media = baked_media(src)
    if old:
        old_html = (cards_dir(repo) / cid / "index.html").read_text("utf-8")
        same = (all(old.get(k) == manifest.get(k) for k in CONTENT_KEYS) and old_html == html
                and baked_media(cards_dir(repo) / cid) == media)
        if same:  # identical content keeps its original anchors and run, like notes
            return {**report, "unchanged": True, "run": old.get("run")}
    run = run or time.strftime("run-%Y%m%d-%H%M%S")
    at = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest.update({"anchor_hashes": hashes, "run": run, "at": at})
    before = store.snapshot(repo)
    _write_card(cards_dir(repo) / cid, manifest, html)
    _write_media(cards_dir(repo) / cid, media)
    _record(repo, run, at, before, {"cards": 1}, "card")
    return {**report, "run": run}


def confirm_card(repo: Path, cid: str, run: str | None = None) -> dict:
    """Refresh a card's anchor hashes after reviewing it against the current code; its text is untouched."""
    idx = load_index(repo)
    sem = store.load(repo)
    card = load_cards(repo).get(cid)
    if not card:
        return {"ok": False, "errors": [f"no card {cid!r}"]}
    gone = [a for a in card.get("anchors", []) if not _anchor_exists(idx, sem, _canonical(idx, a))]
    if gone:
        return {"ok": False, "errors": [f"anchor {a!r} no longer exists; repair the card with `card apply` first" for a in gone]}
    hashes = {a: _node_hash(idx, _canonical(idx, a)) for a in card["anchors"] if not a.startswith(REGION_PREFIX)}
    if hashes == (card.get("anchor_hashes") or {}):
        return {"ok": True, "unchanged": True}
    run = run or time.strftime("run-%Y%m%d-%H%M%S")
    at = time.strftime("%Y-%m-%dT%H:%M:%S")
    before = store.snapshot(repo)
    html = (cards_dir(repo) / cid / "index.html").read_text("utf-8")
    _write_card(cards_dir(repo) / cid, {**card, "anchor_hashes": hashes, "run": run, "at": at}, html)
    _record(repo, run, at, before, {"cards": 1}, "card-confirm")
    return {"ok": True, "run": run}


def delete_card(repo: Path, cid: str, run: str | None = None) -> dict:
    if cid not in load_cards(repo):
        return {"ok": False, "errors": [f"no card {cid!r}"]}
    run = run or time.strftime("run-%Y%m%d-%H%M%S")
    at = time.strftime("%Y-%m-%dT%H:%M:%S")
    before = store.snapshot(repo)
    shutil.rmtree(cards_dir(repo) / cid)
    _record(repo, run, at, before, {"cards": -1}, "card-delete")
    return {"ok": True, "run": run}


def _terms(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,;，。；、？?!！:：/\\]+", text.casefold()) if t]


def search_cards(repo: Path, query: str = "", anchor: str | None = None, limit: int = 10) -> dict:
    """Find cards by words in the title, questions, summary and tags, or by an anchor they cover.
    An empty query lists every card, newest first."""
    idx = load_index(repo)
    sem = store.load(repo)
    cards = load_cards(repo)
    status = card_status(idx, sem, cards)
    terms = _terms(query)
    want = _canonical(idx, anchor) if anchor else None
    rows = []
    for cid, c in cards.items():
        anchors = [_canonical(idx, a) for a in c.get("anchors", [])]
        if want and not any(a == want or a.startswith(want + "#") or want.startswith(a + "#") for a in anchors):
            continue
        fields = {"title": c.get("title", ""), "questions": " ".join(c.get("questions", [])),
                  "summary": c.get("summary", ""), "tags": " ".join(c.get("tags", []))}
        score = 0
        for t in terms:
            score += sum(w for k, w in (("title", 5), ("questions", 4), ("tags", 3), ("summary", 2)) if t in fields[k].casefold())
        if terms and not score:
            continue
        rows.append((score, c.get("at") or "", {"id": cid, "title": c.get("title"), "summary": c.get("summary"),
                                                "questions": c.get("questions"), "anchors": anchors,
                                                **({"state": status[cid]["state"]} if cid in status else {})}))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return {"ok": True, "total": len(rows), "cards": [r[2] for r in rows[:limit]]}
