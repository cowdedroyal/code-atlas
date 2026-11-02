"""Regressions for review findings that need a process or two repos' helpers to reproduce."""

import json
import socket
import subprocess
import sys

import pytest

from code_atlas import site
from code_atlas.scan import scan
from code_atlas.semantic import store
from code_atlas.semantic.changeset import apply
from code_atlas.semantic.explainers import apply_card, load_cards
from code_atlas.site import build_data, build_site, linted_cards

from test_scan import REPO, write
from test_site import _link_dir
from test_tours import TOUR, card_draft


@pytest.fixture
def web(tmp_path, monkeypatch):
    """A stand-in viewer bundle: src/code_atlas/web is a build artifact, absent from a fresh checkout and from CI."""
    d = tmp_path / "web"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html>viewer", encoding="utf-8")
    (d / "assets" / "app.js").write_text("", encoding="utf-8")
    monkeypatch.setattr(site, "WEB_DIR", d)
    return d


def test_serve_fails_clearly_when_the_port_is_taken(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        p = subprocess.run([sys.executable, "-m", "code_atlas.cli", "serve", "-r", str(tmp_path),
                            "--no-scan", "--no-watch", "--port", str(port)],
                           capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(tmp_path))
    finally:
        sock.close()
    assert p.returncode == 1, p.stderr
    out = json.loads(p.stdout)
    assert out["ok"] is False and "port in use" in out["errors"][0]


def test_broken_card_is_not_offered_on_tour_steps(tmp_path):
    write(tmp_path, REPO)
    scan(tmp_path)
    assert apply_card(tmp_path, card_draft(tmp_path), run="c1")["ok"]
    tour = {**TOUR, "steps": [dict(s) for s in TOUR["steps"]]}
    tour["steps"][1]["card"] = "step-card"
    assert apply(tmp_path, {"tours": [tour]}, run="t1")["ok"]
    p = tmp_path / ".codemap" / "explainers" / "step-card" / "index.html"
    p.write_text('<script>fetch("https://x.test")</script>' + p.read_text(encoding="utf-8"), encoding="utf-8")
    data = build_data(tmp_path)
    assert "step-card" in data["skipped_cards"]
    assert "step-card" not in linted_cards(tmp_path, load_cards(tmp_path))
    t = next(t for t in data["graph"]["tours"] if t["id"] == "make-a-user")
    assert all("card" not in s for s in t["steps"])
    assert t["state"] == "broken"
    assert store.load(tmp_path).tours["make-a-user"]["steps"][1]["card"] == "step-card"  # only the site hides it


def _serve(args, cwd, timeout=120):
    return subprocess.run([sys.executable, "-m", "code_atlas.cli", *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout, cwd=str(cwd))


def test_serve_rebuilds_over_its_own_partial_cache(tmp_path):
    import time
    write(tmp_path, REPO)
    scan(tmp_path)
    out = tmp_path / ".codemap" / "cache" / "site"
    (out / "assets").mkdir(parents=True)
    (out / "index.html").write_text("partial", encoding="utf-8")
    (out / "assets" / "old.js").write_text("x", encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "code_atlas.cli", "serve", "-r", str(tmp_path),
                          "--no-scan", "--no-watch", "--port", "0"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", cwd=str(tmp_path))
    try:
        deadline = time.time() + 120
        while time.time() < deadline and p.poll() is None and not (out / "data" / "graph.json").exists():
            time.sleep(0.1)
        assert p.poll() is None, "serve exited on its own partial cache"
        assert (out / "data" / "graph.json").exists()
        if site.WEB_DIR.exists():  # serve runs in its own process, so it can only replace a viewer that was built
            assert (out / "index.html").read_text(encoding="utf-8") != "partial"
    finally:
        p.terminate()
        try:
            p.wait(timeout=60)
        except subprocess.TimeoutExpired:  # pragma: no cover - a hung process must not break the suite
            p.kill()
            p.wait()


def test_build_refuses_to_write_through_a_link_in_the_output(tmp_path, web):
    import shutil
    write(tmp_path, REPO)
    scan(tmp_path)
    out = tmp_path / "site"
    assert build_site(tmp_path, out, refresh_index=False)["ok"]
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("keep", encoding="utf-8")
    shutil.rmtree(out / "assets")
    _link_dir(out / "assets", victim)
    report = build_site(tmp_path, out, refresh_index=False)
    assert not report["ok"] and "symlink or junction" in report["errors"][0]
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert {p.name for p in victim.iterdir()} == {"keep.txt"}


def test_manifest_entries_that_cannot_be_paths_are_ignored(tmp_path, web):
    write(tmp_path, REPO)
    scan(tmp_path)
    out = tmp_path / "site"
    assert build_site(tmp_path, out, refresh_index=False)["ok"]
    (out / ".codeatlas-site.json").write_text(
        json.dumps({"version": 1, "entries": ["data", "a\x00b", "x/y", ".."]}), encoding="utf-8")
    report = build_site(tmp_path, out, refresh_index=False)
    assert report["ok"], report
    assert (out / "data" / "graph.json").exists() and (out / "index.html").exists()


def test_serve_port_out_of_range_is_a_json_error(tmp_path):
    p = _serve(["serve", "-r", str(tmp_path), "--no-scan", "--no-watch", "--port", "70000"], tmp_path)
    assert p.returncode == 1, p.stderr
    assert "Traceback" not in p.stderr
    out = json.loads(p.stdout)
    assert out["ok"] is False and "0 and 65535" in out["errors"][0]


def test_build_reports_a_stale_index_as_json(tmp_path):
    write(tmp_path, REPO)
    scan(tmp_path)
    p = tmp_path / "app" / "service.py"
    p.write_text(p.read_text(encoding="utf-8") + "\n# changed after the scan\n", encoding="utf-8")
    report = build_site(tmp_path, tmp_path / "site", refresh_index=False)
    assert not report["ok"] and "Source changed since scan" in report["errors"][0]


def test_stale_nodes_use_canonical_ids(tmp_path):
    from code_atlas.site import _explainers
    write(tmp_path, REPO)
    scan(tmp_path)
    assert apply_card(tmp_path, card_draft(tmp_path), run="c1")["ok"]

    class Idx:
        def canonical(self, a):
            return "app/service.py#renamed" if a == "app/service.py#make_user" else a

    state = {"step-card": {"state": "stale", "nodes": ["app/service.py#make_user", "region:services"]}}
    listed, _, _ = _explainers(Idx(), tmp_path, load_cards(tmp_path), state)
    assert listed[0]["anchors"] == ["app/service.py#renamed"]
    assert listed[0]["stale_nodes"] == ["app/service.py#renamed", "region:services"]


def test_packed_cards_are_published_as_json_not_html(tmp_path):
    write(tmp_path, REPO)
    scan(tmp_path)
    assert apply_card(tmp_path, card_draft(tmp_path), run="c1")["ok"]
    out = tmp_path / "site"
    assert build_site(tmp_path, out, refresh_index=False)["ok"]
    page = json.loads((out / "data" / "explainers" / "step-card.json").read_text(encoding="utf-8"))
    assert isinstance(page, str) and "Content-Security-Policy" in page
    assert not (out / "data" / "explainers" / "step-card.html").exists()


def test_card_bake_missing_directory_is_json(tmp_path):
    from typer.testing import CliRunner
    from code_atlas.cli import app
    r = CliRunner().invoke(app, ["card", "bake", str(tmp_path / "does-not-exist")])
    assert r.exit_code == 1
    report = json.loads(r.output)
    assert "no card directory" in report["errors"][0]


def test_card_bake_unknown_only_name_is_an_error(tmp_path):
    from typer.testing import CliRunner
    from code_atlas.cli import app
    from test_explainers import SCENE_YAML
    card = tmp_path / "scene-card"
    scenes = card / "media" / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "flow.yaml").write_text(SCENE_YAML, encoding="utf-8")
    r = CliRunner().invoke(app, ["card", "bake", str(card), "--only", "flow", "--only", "typo"])
    assert r.exit_code == 1
    report = json.loads(r.output)
    assert "typo" in report["errors"][0]
    assert (card / "media" / "flow.json").exists()


def test_card_bake_without_the_media_extra_is_json(tmp_path):
    import os
    card = tmp_path / "anim-card"
    frames = card / "media" / "src" / "n" / "frames"
    frames.mkdir(parents=True)
    (frames / "000.png").write_bytes(b"not really a png")
    stub = tmp_path / "stub" / "PIL"
    stub.mkdir(parents=True)
    (stub / "__init__.py").write_text("raise ImportError('no pillow here')", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(stub.parent), "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, "-m", "code_atlas.cli", "card", "bake", str(card)],
                       capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(tmp_path), env=env)
    assert p.returncode == 1, p.stderr
    report = json.loads(p.stdout)
    assert "media extra" in report["errors"][0]


def test_card_bake_help_names_the_installed_extra():
    from typer.testing import CliRunner
    from code_atlas.cli import app
    r = CliRunner().invoke(app, ["card", "bake", "--help"])
    assert r.exit_code == 0
    assert "repo-code-atlas[media]" in r.output
