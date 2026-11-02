"""Cards are untrusted input: damaged manifests, stray files, symlinks and reverts must not break the commands."""

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from code_atlas.cli import app
from code_atlas.explainer_kit.media import bake_card
from code_atlas.semantic import store
from code_atlas.semantic.agenda import agenda
from code_atlas.semantic.changeset import apply as apply_changeset
from code_atlas.semantic.changeset import revert, validate
from code_atlas.semantic.context import semantic_status
from code_atlas.semantic.explainers import (apply_card, cards_dir, delete_card, load_cards, search_cards,
                                            validate_card)
from code_atlas.site import build_data

from test_explainers import MEDIA_PAGE, PAGE, SCENE_PAGE, SCENE_YAML, WEBP, _baked_anim, draft
from test_tours import setup

NOTE = {"intent": "note", "notes": [{"target": "app/models.py#User", "text": "A user; greeting saves it first."}]}

CARD = {"kit": 1, "id": "user-flow", "title": "How a user is made", "questions": ["Why does greet save first?"],
        "summary": "Follows one user to the saved record.", "anchors": ["app/models.py#User"], "tags": ["users"],
        "language": "en"}


def _scene_draft(tmp_path):
    d = draft(tmp_path, page=SCENE_PAGE)
    scenes = d / "media" / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "flow.yaml").write_text(SCENE_YAML, "utf-8")
    assert bake_card(d)["media"]["flow"]["kind"] == "scene"
    return d


def _anim(card: Path, name: str) -> None:
    media = card / "media"
    media.mkdir(exist_ok=True)
    (media / f"{name}.json").write_text(json.dumps({
        "kind": "anim", "id": name, "sheet": f"{name}.webp", "width": 8, "height": 8, "columns": 1,
        "frames": 1, "fps": 4, "ms": 250, "captions": [], "sources": {}}), "utf-8")
    (media / f"{name}.webp").write_bytes(WEBP)


def test_revert_covers_nested_scene_sources(tmp_path):
    repo = setup(tmp_path)
    d = _scene_draft(tmp_path)
    assert apply_card(repo, d, run="a1")["ok"]
    assert "explainers/user-flow/media/scenes/flow.yaml" in store.snapshot(repo)
    assert revert(repo, "a1")["ok"]
    assert not (cards_dir(repo) / "user-flow").exists()  # nor its now-empty media/scenes directory

    assert apply_card(repo, d, run="a2")["ok"]
    assert delete_card(repo, "user-flow", run="d1")["ok"]
    assert revert(repo, "d1")["ok"]
    assert (cards_dir(repo) / "user-flow/media/scenes/flow.yaml").read_text("utf-8") == SCENE_YAML

    (d / "media/scenes/flow.yaml").write_text(SCENE_YAML.replace("greet", "hello"), "utf-8")
    bake_card(d)
    assert apply_card(repo, d, run="a3")["ok"]
    assert revert(repo, "a3")["ok"]
    assert (cards_dir(repo) / "user-flow/media/scenes/flow.yaml").read_text("utf-8") == SCENE_YAML
    assert "user-flow" in build_data(repo)["cards"]  # the reverted manifest matches the reverted source


def test_damaged_stored_cards_are_skipped_not_fatal(tmp_path):
    repo = setup(tmp_path)
    assert apply_card(repo, draft(tmp_path), run="c1")["ok"]
    p = cards_dir(repo) / "user-flow" / "card.yaml"
    damaged = [
        ("anchors-null", yaml.safe_dump({**CARD, "anchors": None}).encode()),
        ("anchors-int", yaml.safe_dump({**CARD, "anchors": [42]}).encode()),
        ("hashes-list", yaml.safe_dump({**CARD, "anchor_hashes": ["x"]}).encode()),
        ("questions-int", yaml.safe_dump({**CARD, "questions": 3}).encode()),
        ("not-utf8", yaml.safe_dump({**CARD, "tags": None, "title": "中"}, allow_unicode=True).encode("utf-16")),
    ]
    for name, data in damaged:
        p.write_bytes(data)
        snap = store.snapshot(repo)["explainers/user-flow/card.yaml"]
        assert isinstance(snap, dict) is (name == "not-utf8")
        assert load_cards(repo) == {}
        assert not [t for t in agenda(repo)["tasks"] if t["target"].startswith("card:")]
        assert semantic_status(repo)["explainers"] == 0
        assert search_cards(repo)["total"] == 0
        assert build_data(repo)["graph"]["explainers"] == []
        assert validate(repo, NOTE)[0]["ok"]  # an unrelated changeset still validates
    assert apply_changeset(repo, NOTE, run="r2")["ok"]  # and an unrelated apply still succeeds


def test_card_validate_reports_bad_drafts_instead_of_raising(tmp_path):
    repo = setup(tmp_path)
    cases = {"bad-yaml": b"id: [unclosed\n", "list": b"- kit: 1\n", "scalar": b"just a string\n",
             "not-utf8": "title: 中文\n".encode("utf-16")}
    for name, data in cases.items():
        d = tmp_path.parent / f"{tmp_path.name}-bad" / name
        d.mkdir(parents=True)
        (d / "card.yaml").write_bytes(data)
        (d / "index.html").write_text(PAGE, "utf-8")
        r = validate_card(repo, d)
        assert not r["ok"] and r["errors"]
        assert not apply_card(repo, d)["ok"] and not (cards_dir(repo) / name).exists()
        out = CliRunner().invoke(app, ["card", "validate", str(d), "-r", str(repo)])
        assert out.exit_code == 1
        assert json.loads(out.output)["ok"] is False  # a JSON report, not a traceback
    d = tmp_path.parent / f"{tmp_path.name}-bad" / "gbk-page"
    d.mkdir(parents=True)
    (d / "card.yaml").write_text(yaml.safe_dump({**CARD, "id": "gbk-page"}), "utf-8")
    (d / "index.html").write_bytes("中文".encode("utf-16"))
    r = validate_card(repo, d)
    assert not r["ok"] and any("index.html" in e for e in r["errors"])


def test_stray_binary_file_in_a_stored_card_does_not_block_writes(tmp_path):
    repo = setup(tmp_path)
    assert apply_card(repo, draft(tmp_path), run="c1")["ok"]
    (cards_dir(repo) / "user-flow" / ".DS_Store").write_bytes(b"\xff\x00\x01junk")
    snap = store.snapshot(repo)["explainers/user-flow/.DS_Store"]
    assert snap.get("blob")  # binary assets are referenced by content hash, not copied into every run
    assert apply_card(repo, draft(tmp_path, "other", anchors=("region:web",)), run="c2")["ok"]
    assert revert(repo, "c2")["ok"]
    assert delete_card(repo, "user-flow", run="d1")["ok"]
    assert revert(repo, "d1")["ok"]


def test_apply_does_not_copy_symlinked_scene_sources(tmp_path):
    repo = setup(tmp_path)
    secret = tmp_path.parent / f"{tmp_path.name}-secret.txt"
    secret.write_text("NOT-A-REAL-TOKEN", "utf-8")
    d = _scene_draft(tmp_path)
    try:
        (d / "media/scenes/leak.yaml").symlink_to(secret)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or elevation on Windows")
    assert apply_card(repo, d, run="a1")["ok"]
    stored = cards_dir(repo) / "user-flow"
    assert sorted(p.name for p in (stored / "media/scenes").iterdir()) == ["flow.yaml"]
    assert not any("NOT-A-REAL-TOKEN" in p.read_text("utf-8", "ignore") for p in stored.rglob("*") if p.is_file())


def test_apply_ignores_a_symlinked_media_scenes_directory(tmp_path):
    repo = setup(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "hosts.yml").write_text("oauth_token: NOT-A-REAL-TOKEN", "utf-8")
    d = draft(tmp_path, page=MEDIA_PAGE)
    _baked_anim(d)
    try:
        (d / "media/scenes").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or elevation on Windows")
    assert apply_card(repo, d, run="a1")["ok"]
    assert sorted(p.name for p in (cards_dir(repo) / "user-flow/media").iterdir()) == ["flow.json", "flow.webp"]


def test_empty_tags_is_still_unchanged_when_nothing_else_changed(tmp_path):
    repo = setup(tmp_path)
    d = draft(tmp_path, tags=[])
    assert apply_card(repo, d, run="c1")["ok"]
    runs = len(store.read_log(repo))
    again = apply_card(repo, d, run="c2")
    assert again["unchanged"] and again["run"] == "c1"
    assert len(store.read_log(repo)) == runs


def test_case_only_media_rename_keeps_the_new_files(tmp_path):
    repo = setup(tmp_path)
    upper = draft(tmp_path, page=MEDIA_PAGE.replace("flow", "Flow"))
    _anim(upper, "Flow")
    assert apply_card(repo, upper, run="a1")["ok"]
    lower = draft(tmp_path / "lower", page=MEDIA_PAGE)
    _anim(lower, "flow")
    assert apply_card(repo, lower, run="a2")["ok"]
    assert sorted(p.name for p in (cards_dir(repo) / "user-flow/media").iterdir()) == ["flow.json", "flow.webp"]
    assert "user-flow" in build_data(repo)["cards"]
