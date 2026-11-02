"""Regressions for review findings that need a process or two repos' helpers to reproduce."""

import json
import socket
import subprocess
import sys

from code_atlas.scan import scan
from code_atlas.semantic import store
from code_atlas.semantic.changeset import apply
from code_atlas.semantic.explainers import apply_card, load_cards
from code_atlas.site import build_data, linted_cards

from test_scan import REPO, write
from test_tours import TOUR, card_draft


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
