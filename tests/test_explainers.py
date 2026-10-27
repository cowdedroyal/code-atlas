import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from code_atlas.cli import app
from code_atlas.graphdata import load_index
from code_atlas.scan import scan
from code_atlas.semantic import store
from code_atlas.semantic.agenda import agenda
from code_atlas.semantic.changeset import revert
from code_atlas.semantic.context import semantic_status
from code_atlas.semantic.explainers import (apply_card, card_status, cards_dir, confirm_card, delete_card, load_cards,
                                            search_cards, validate_card)
from code_atlas.site import build_data, build_site

from test_tours import setup

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="atlas-kit:1.css"></head>
<body><h1>How a user is made</h1>
<ex-qa q="Why does greet save first?"><p>Because the model owns persistence.</p>
<ex-table source="illustrative"><table><tr><th>name</th></tr><tr><td>ada</td></tr></table></ex-table></ex-qa>
<script src="atlas-kit:1.js"></script></body></html>
"""


def draft(tmp_path, cid="user-flow", anchors=("app/models.py#User", "region:services"), page=PAGE, **extra):
    d = tmp_path.parent / f"{tmp_path.name}-drafts" / cid
    d.mkdir(parents=True, exist_ok=True)
    manifest = {"kit": 1, "id": cid, "title": "How a user is made", "questions": ["Why does greet save first?"],
                "summary": "Follows one user from creation to the saved record.", "anchors": list(anchors),
                "tags": ["users"], "language": "en", **extra}
    (d / "card.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True), "utf-8")
    (d / "index.html").write_text(page, "utf-8")
    return d


def edit_source(repo, rel, old, new):
    p = repo / rel
    p.write_text(p.read_text("utf8").replace(old, new), "utf8")
    scan(repo)


def test_apply_writes_card_with_anchor_hashes_and_logs_run(tmp_path):
    repo = setup(tmp_path)
    d = draft(tmp_path)
    report = apply_card(repo, d, run="c1")
    assert report["ok"], report
    card = load_cards(repo)["user-flow"]
    idx = load_index(repo)
    assert card["anchor_hashes"] == {"app/models.py#User": idx.anchor("app/models.py#User")}  # regions have no hash
    assert card["run"] == "c1" and card["at"]
    assert (cards_dir(repo) / "user-flow" / "index.html").read_text("utf-8") == PAGE
    assert store.read_log(repo)[-1]["intent"] == "card"
    assert "explainers/user-flow/card.yaml" in store.snapshot(repo)


def test_identical_apply_changes_nothing_but_changed_page_replaces(tmp_path):
    repo = setup(tmp_path)
    d = draft(tmp_path)
    apply_card(repo, d, run="c1")
    runs = len(store.read_log(repo))
    again = apply_card(repo, d, run="c2")
    assert again["ok"] and again["unchanged"] and again["run"] == "c1"
    assert len(store.read_log(repo)) == runs
    (d / "index.html").write_text(PAGE.replace("persistence", "saving"), "utf-8")
    changed = apply_card(repo, d, run="c3")
    assert changed["ok"] and "unchanged" not in changed
    assert load_cards(repo)["user-flow"]["run"] == "c3"
    assert "already exists" in " ".join(validate_card(repo, d)["warnings"])


def test_validate_rejects_missing_anchors_and_unsafe_pages(tmp_path):
    repo = setup(tmp_path)
    r = validate_card(repo, draft(tmp_path, anchors=("app/models.py#Nope", "region:ghost")))
    assert not r["ok"] and sum("does not exist" in e for e in r["errors"]) == 2
    r = validate_card(repo, draft(tmp_path, "bad-page", page=PAGE.replace("<h1>", '<img src="https://x.test/a.png"><h1>')))
    assert not r["ok"] and any("points outside" in e for e in r["errors"])
    r = validate_card(repo, draft(tmp_path, "mismatch"))
    (tmp_path.parent / f"{tmp_path.name}-drafts" / "mismatch").rename(tmp_path.parent / f"{tmp_path.name}-drafts" / "other")
    r = validate_card(repo, tmp_path.parent / f"{tmp_path.name}-drafts" / "other")
    assert any("must equal the directory name" in e for e in r["errors"])
    assert not (cards_dir(repo) / "bad-page").exists()


def test_code_change_marks_card_stale_agenda_refreshes_and_confirm_clears(tmp_path):
    repo = setup(tmp_path)
    apply_card(repo, draft(tmp_path), run="c1")
    assert not card_status(load_index(repo), store.load(repo), load_cards(repo))
    edit_source(repo, "app/models.py", "return self.save()", "return self.save() or None")
    status = card_status(load_index(repo), store.load(repo), load_cards(repo))
    assert status["user-flow"]["state"] == "stale" and status["user-flow"]["nodes"] == ["app/models.py#User"]
    tasks = [t for t in agenda(repo)["tasks"] if t["target"] == "card:user-flow"]
    assert tasks and tasks[0]["type"] == "refresh" and "card confirm user-flow" in tasks[0]["hint"]
    assert semantic_status(repo)["explainers_stale"] == 1
    assert confirm_card(repo, "user-flow", run="c2")["ok"]
    assert not card_status(load_index(repo), store.load(repo), load_cards(repo))
    assert confirm_card(repo, "user-flow")["unchanged"]


def test_removed_anchor_makes_card_orphan_and_blocks_confirm(tmp_path):
    repo = setup(tmp_path)
    apply_card(repo, draft(tmp_path, anchors=("app/models.py#User",)))
    edit_source(repo, "app/models.py", "class User", "class Person")
    assert card_status(load_index(repo), store.load(repo), load_cards(repo))["user-flow"]["state"] == "orphan"
    r = confirm_card(repo, "user-flow")
    assert not r["ok"] and "no longer exists" in r["errors"][0]


def test_revert_removes_applied_card_and_restores_deleted_one(tmp_path):
    repo = setup(tmp_path)
    apply_card(repo, draft(tmp_path), run="c1")
    assert revert(repo, "c1")["ok"]
    assert not load_cards(repo) and not (cards_dir(repo) / "user-flow").exists()
    apply_card(repo, draft(tmp_path), run="c2")
    assert delete_card(repo, "user-flow", run="d1")["ok"]
    assert not load_cards(repo)
    assert revert(repo, "d1")["ok"]
    assert "user-flow" in load_cards(repo)
    assert not delete_card(repo, "ghost")["ok"]


def test_search_by_words_and_by_anchor(tmp_path):
    repo = setup(tmp_path)
    apply_card(repo, draft(tmp_path), run="c1")
    apply_card(repo, draft(tmp_path, "other", anchors=("region:web",), title="Browser rendering", questions=["Where is the UI drawn?"],
                           summary="What the web region shows."), run="c2")
    assert [c["id"] for c in search_cards(repo, "greet save")["cards"]] == ["user-flow"]
    assert [c["id"] for c in search_cards(repo, "UI drawn")["cards"]] == ["other"]
    assert search_cards(repo, "nothing-like-this")["total"] == 0
    assert [c["id"] for c in search_cards(repo, anchor="app/models.py")["cards"]] == ["user-flow"]  # a file covers its symbols
    assert {c["id"] for c in search_cards(repo)["cards"]} == {"user-flow", "other"}


def test_build_lists_cards_and_packs_pages(tmp_path):
    repo = setup(tmp_path)
    apply_card(repo, draft(tmp_path), run="c1")
    edit_source(repo, "app/models.py", "return self.save()", "return self.save() or None")
    data = build_data(repo)
    listed = data["graph"]["explainers"]
    assert [c["id"] for c in listed] == ["user-flow"]
    assert listed[0]["state"] == "stale" and listed[0]["stale_nodes"] == ["app/models.py#User"]
    assert listed[0]["anchors"] == ["app/models.py#User", "region:services"]
    page = data["cards"]["user-flow"]
    assert "atlas-kit:" not in page and "Content-Security-Policy" in page and "atlas:height" in page
    out = tmp_path / "site"
    result = build_site(repo, out, refresh_index=False)
    assert result["explainers"] == 1
    assert (out / "data" / "explainers" / "user-flow.html").read_text("utf-8") == page


def test_build_skips_a_card_that_no_longer_lints(tmp_path):
    repo = setup(tmp_path)
    apply_card(repo, draft(tmp_path), run="c1")
    p = cards_dir(repo) / "user-flow" / "index.html"
    p.write_text(PAGE.replace("<h1>", '<script>fetch("https://x.test")</script><h1>'), "utf-8")
    data = build_data(repo)
    assert data["graph"]["explainers"] == [] and "user-flow" in data["skipped_cards"]


def test_cli_card_commands(tmp_path):
    repo = setup(tmp_path)
    d = draft(tmp_path)
    run = CliRunner()
    r = run.invoke(app, ["card", "apply", str(d), "-r", str(repo)])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["ok"]
    r = run.invoke(app, ["card", "search", "greet", "-r", str(repo)])
    assert json.loads(r.output)["cards"][0]["id"] == "user-flow"
    r = run.invoke(app, ["card", "validate", str(draft(tmp_path, "bad", anchors=("nope.py",))), "-r", str(repo)])
    assert r.exit_code == 1
    assert run.invoke(app, ["status", "-r", str(repo)]).exit_code == 0
    assert run.invoke(app, ["card", "delete", "user-flow", "-r", str(repo)]).exit_code == 0


MEDIA_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="atlas-kit:1.css"></head>
<body><h1>How a user is made</h1>
<ex-anim src="atlas-media:flow" source="illustrative" caption="a dot moving"></ex-anim>
<script src="atlas-kit:1.js"></script></body></html>
"""

# a real 8×8 WebP: the tests never bake with Pillow, they only check that a baked asset is carried along
WEBP = base64.b64decode("UklGRjgAAABXRUJQVlA4ICwAAADQAQCdASoIAAgAAUAmJaACdLoB+AADsAD+71Ev/iGej1Lw//lKvBY3N8AAAA==")


def _baked_anim(card: Path, sheet: bytes = WEBP) -> None:
    media = card / "media"
    media.mkdir(exist_ok=True)
    (media / "flow.json").write_text(json.dumps({
        "kind": "anim", "id": "flow", "sheet": "flow.webp", "width": 8, "height": 8, "columns": 1,
        "frames": 1, "fps": 4, "ms": 250, "captions": [], "sources": {}}), "utf-8")
    (media / "flow.webp").write_bytes(sheet)


def test_apply_carries_baked_media_and_build_inlines_it(tmp_path):
    repo = setup(tmp_path)
    d = draft(tmp_path, page=MEDIA_PAGE)
    _baked_anim(d)
    assert apply_card(repo, d, run="c1")["ok"]
    assert (cards_dir(repo) / "user-flow" / "media" / "flow.webp").read_bytes() == WEBP
    page = build_data(repo)["cards"]["user-flow"]
    assert "atlas-media-registry" in page and "data:image/webp;base64," in page
    assert apply_card(repo, d, run="c2")["unchanged"]          # same page, same sheet
    _baked_anim(d, WEBP + b"\0")                               # re-baked: a new sheet is a change
    assert "unchanged" not in apply_card(repo, d, run="c3")
    assert delete_card(repo, "user-flow", run="c4")["ok"]
    assert not (cards_dir(repo) / "user-flow").exists()        # media included, not just the two files


def test_a_card_whose_media_is_not_baked_cannot_be_applied(tmp_path):
    repo = setup(tmp_path)
    report = validate_card(repo, draft(tmp_path, page=MEDIA_PAGE))
    assert not report["ok"] and any("not baked" in e for e in report["errors"])


def test_cli_card_bake_explains_that_there_is_nothing_to_bake(tmp_path):
    d = draft(tmp_path)
    r = CliRunner().invoke(app, ["card", "bake", str(d)])
    assert r.exit_code == 1
    assert "nothing to bake" in r.output


def test_vendored_kit_matches_atlas_explainer_when_available():
    script = Path(__file__).parent.parent / "scripts" / "sync_explainer_kit.py"
    root = script.parent.parent
    candidates = [Path(os.environ["ATLAS_EXPLAINER_DIR"])] if os.environ.get("ATLAS_EXPLAINER_DIR") else [
        root / "extensions" / "atlas-explainer", root.parent / "atlas-explainer"]
    if not any((c / "src" / "atlas_explainer").is_dir() for c in candidates):
        pytest.skip("atlas-explainer checkout not present (git submodule update --init)")
    done = subprocess.run([sys.executable, str(script), "--check"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
