"""Card storage is committed and untrusted: no card command may read, write or delete through a symlink/junction."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from code_atlas.semantic import store
from code_atlas.semantic.explainers import apply_card, cards_dir, confirm_card, delete_card

from test_explainers import MEDIA_PAGE, _baked_anim, draft
from test_tours import setup


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or elevation on Windows")


def _junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        pytest.skip("junctions are a Windows feature")
    link.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"cannot create a junction: {r.stderr or r.stdout}")


def _link_variants():
    return [_symlink] if os.name != "nt" else [_symlink, _junction]


def _outside(tmp_path, text="KEEP"):
    d = tmp_path.parent / f"{tmp_path.name}-outside"
    d.mkdir(exist_ok=True)
    (d / "keep.txt").write_text(text, "utf-8")
    return d


@pytest.mark.parametrize("make_link", _link_variants())
def test_apply_refuses_a_linked_card_directory(tmp_path, make_link):
    repo = setup(tmp_path)
    assert apply_card(repo, draft(tmp_path), run="c1")["ok"]
    stored = cards_dir(repo) / "user-flow"
    outside = _outside(tmp_path)
    shutil.rmtree(stored)
    make_link(stored, outside)
    r = apply_card(repo, draft(tmp_path), run="c2")
    assert not r["ok"] and "symlink or junction" in r["errors"][0]
    assert (outside / "keep.txt").read_text("utf-8") == "KEEP"
    assert not (outside / "card.yaml").exists()


@pytest.mark.parametrize("make_link", _link_variants())
def test_apply_refuses_a_linked_media_directory_and_deletes_nothing(tmp_path, make_link):
    repo = setup(tmp_path)
    d = draft(tmp_path, page=MEDIA_PAGE)
    _baked_anim(d)
    assert apply_card(repo, d, run="c1")["ok"]
    media = cards_dir(repo) / "user-flow" / "media"
    outside = _outside(tmp_path)
    shutil.rmtree(media)
    make_link(media, outside)
    r = apply_card(repo, d, run="c2")
    assert not r["ok"] and "symlink or junction" in r["errors"][0]
    assert (outside / "keep.txt").read_text("utf-8") == "KEEP"
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]  # no manifest file written, none deleted


@pytest.mark.parametrize("make_link", _link_variants())
def test_confirm_refuses_a_linked_card_directory(tmp_path, make_link):
    repo = setup(tmp_path)
    assert apply_card(repo, draft(tmp_path), run="c1")["ok"]
    stored = cards_dir(repo) / "user-flow"
    outside = _outside(tmp_path)
    (outside / "card.yaml").write_text("id: user-flow\n", "utf-8")
    (outside / "index.html").write_text("OUTSIDE", "utf-8")
    shutil.rmtree(stored)
    make_link(stored, outside)
    r = confirm_card(repo, "user-flow")
    assert not r["ok"] and "symlink or junction" in r["errors"][0]
    assert (outside / "index.html").read_text("utf-8") == "OUTSIDE"  # the outside card was never read or rewritten


@pytest.mark.parametrize("make_link", _link_variants())
def test_delete_refuses_a_linked_media_directory(tmp_path, make_link):
    repo = setup(tmp_path)
    d = draft(tmp_path, page=MEDIA_PAGE)
    _baked_anim(d)
    assert apply_card(repo, d, run="c1")["ok"]
    stored = cards_dir(repo) / "user-flow"
    media = stored / "media"
    outside = _outside(tmp_path)
    shutil.rmtree(media)
    make_link(media, outside)
    r = delete_card(repo, "user-flow")
    assert not r["ok"] and "symlink or junction" in r["errors"][0]
    assert (outside / "keep.txt").read_text("utf-8") == "KEEP"
    assert (stored / "card.yaml").exists()


def test_confirm_refuses_a_linked_page_file(tmp_path):
    repo = setup(tmp_path)
    assert apply_card(repo, draft(tmp_path), run="c1")["ok"]
    stored = cards_dir(repo) / "user-flow"
    outside = _outside(tmp_path)
    (stored / "index.html").unlink()
    try:
        (stored / "index.html").symlink_to(outside / "keep.txt")
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or elevation on Windows")
    r = confirm_card(repo, "user-flow")
    assert not r["ok"] and "symlink or junction" in r["errors"][0]


@pytest.mark.parametrize("make_link", _link_variants())
def test_snapshot_does_not_follow_a_linked_media_directory(tmp_path, make_link):
    repo = setup(tmp_path)
    d = draft(tmp_path, page=MEDIA_PAGE)
    _baked_anim(d)
    assert apply_card(repo, d, run="c1")["ok"]
    media = cards_dir(repo) / "user-flow" / "media"
    outside = _outside(tmp_path, "NOT-A-REAL-TOKEN")
    shutil.rmtree(media)
    make_link(media, outside)
    snap = store.snapshot(repo)
    assert "explainers/user-flow/card.yaml" in snap  # the card's own files are still snapshotted
    assert not any(name.startswith("explainers/user-flow/media/") for name in snap)
    assert "NOT-A-REAL-TOKEN" not in json.dumps(snap)


@pytest.mark.parametrize("make_link", _link_variants())
def test_snapshot_ignores_a_linked_explainers_directory(tmp_path, make_link):
    repo = setup(tmp_path)
    exp = cards_dir(repo)
    outside = _outside(tmp_path, "NOT-A-REAL-TOKEN")
    if exp.exists():
        shutil.rmtree(exp)
    make_link(exp, outside)
    assert not any(name.startswith("explainers/") for name in store.snapshot(repo))
