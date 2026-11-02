"""Writers of `.codemap` are serialized, saves are atomic, and run snapshots reference media by hash."""

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from code_atlas.semantic import store
from code_atlas.semantic.changeset import apply, revert
from code_atlas.semantic.explainers import apply_card, cards_dir
from test_explainers import MEDIA_PAGE, WEBP, draft
from test_explainer_robustness import _anim
from test_tours import setup

WORKER = '''\
import json, sys, time
from pathlib import Path
from code_atlas.semantic.changeset import apply
time.sleep(float(sys.argv[2]))
r = apply(Path(sys.argv[1]), {"notes": [{"target": sys.argv[3], "text": "note " + sys.argv[4]}]}, run=sys.argv[4])
print(json.dumps(r))
'''


def _spawn(script: Path, *args):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.Popen([sys.executable, str(script), *map(str, args)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)


def test_the_write_lock_is_reentrant_within_a_thread(tmp_path):
    repo = setup(tmp_path)
    with store.write_lock(repo):
        with store.write_lock(repo):  # a nested writer must not deadlock against itself
            pass


def test_two_processes_applying_at_once_keep_both_notes(tmp_path):
    repo = setup(tmp_path)
    script = tmp_path.parent / f"{tmp_path.name}-apply.py"
    script.write_text(WORKER, "utf-8")
    procs = [_spawn(script, repo, 0.5, "app/service.py", "p1"),
             _spawn(script, repo, 0.5, "main.py", "p2")]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=90)
        assert p.returncode == 0, err
        results.append(json.loads(out))
    assert all(r["ok"] for r in results), results  # the lock makes them take turns instead of losing an update
    sem = store.load(repo)
    assert sem.notes["app/service.py"]["text"] == "note p1"
    assert sem.notes["main.py"]["text"] == "note p2"
    assert {"p1", "p2"} <= {e.get("run") for e in store.read_log(repo)}


HOLDER = '''\
import sys, time
from pathlib import Path
from code_atlas.semantic import store
with store.write_lock(Path(sys.argv[1])):
    print("locked", flush=True)
    time.sleep(float(sys.argv[2]))
'''


def test_a_busy_lock_fails_with_the_normal_error_report(tmp_path, monkeypatch):
    repo = setup(tmp_path)
    script = tmp_path.parent / f"{tmp_path.name}-hold.py"
    script.write_text(HOLDER, "utf-8")
    holder = _spawn(script, repo, 3)
    assert holder.stdout.readline().strip() == "locked"
    monkeypatch.setattr(store, "LOCK_TIMEOUT", 0.2)
    r = apply(repo, {"notes": [{"target": "main.py", "text": "x"}]}, run="late")
    assert not r["ok"] and r["errors"] == ["another codeatlas write is in progress"]
    holder.communicate(timeout=30)
    assert not (repo / ".codemap/cache/runs/late.json").exists()


def test_a_failed_save_restores_the_previous_layer(tmp_path, monkeypatch):
    repo = setup(tmp_path)
    before = store.snapshot(repo)
    original = store.write_atomic
    fired = []

    def failing(path, data, newline=None):
        if path.name == "relations.jsonl" and not fired:
            fired.append(path)
            raise PermissionError(13, "Permission denied", str(path))
        return original(path, data, newline)

    monkeypatch.setattr(store, "write_atomic", failing)
    r = apply(repo, {"regions": [{"id": "tools", "name": "Tools", "role": "tooling", "responsibility": "Helpers."}],
                     "assign": [{"match": "go/", "region": "tools"}],
                     "notes": [{"target": "region:tools", "text": "Small tools."}]}, run="broken")
    assert not r["ok"] and "could not write" in r["errors"][0], r
    assert fired
    assert store.snapshot(repo) == before  # no half-written layer left behind
    assert "broken" not in {e.get("run") for e in store.read_log(repo)}


def test_binary_media_is_stored_once_as_a_blob(tmp_path):
    repo = setup(tmp_path)
    d = draft(tmp_path, page=MEDIA_PAGE)
    _anim(d, "flow")
    assert apply_card(repo, d, run="c1")["ok"]
    runs = repo / ".codemap/cache/runs"
    ref = json.loads((runs / "c1.json").read_text("utf-8"))["after"]["explainers/user-flow/media/flow.webp"]
    assert re.fullmatch(r"[0-9a-f]{64}", ref["blob"])
    assert (runs / "blobs" / ref["blob"]).read_bytes() == WEBP
    other = draft(tmp_path, "other", anchors=("region:web",), page=MEDIA_PAGE)
    _anim(other, "flow")
    assert apply_card(repo, other, run="c2")["ok"]
    assert sorted(p.name for p in (runs / "blobs").iterdir()) == [ref["blob"]]  # same bytes, one copy
    assert revert(repo, "c2")["ok"] and revert(repo, "c1")["ok"]
    assert not (cards_dir(repo) / "user-flow").exists()


def test_revert_accepts_a_run_written_with_base64_envelopes(tmp_path):
    repo = setup(tmp_path)
    d = draft(tmp_path, page=MEDIA_PAGE)
    _anim(d, "flow")
    assert apply_card(repo, d, run="c1")["ok"]
    f = repo / ".codemap/cache/runs/c1.json"
    snap = json.loads(f.read_text("utf-8"))
    blobs = repo / ".codemap/cache/runs/blobs"
    for name, value in snap["after"].items():
        if isinstance(value, dict) and "blob" in value:
            snap["after"][name] = {"encoding": "base64",
                                   "data": base64.b64encode((blobs / value["blob"]).read_bytes()).decode("ascii")}
    f.write_text(json.dumps(snap), "utf-8")
    assert revert(repo, "c1")["ok"]
    assert not (cards_dir(repo) / "user-flow").exists()


def test_restore_refuses_paths_and_blobs_outside_the_snapshot(tmp_path):
    repo = setup(tmp_path)
    before = store.snapshot(repo)
    with pytest.raises(ValueError):
        store.restore(repo, {"../escaped.txt": "x"})
    with pytest.raises(ValueError):
        store.restore(repo, {"notes/_root.jsonl": {"blob": "../../../evil"}})
    assert not (repo / "escaped.txt").exists()
    assert store.snapshot(repo) == before  # rejected before deleting or writing anything
