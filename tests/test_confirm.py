from code_atlas.scan import scan
from code_atlas.semantic import store
from code_atlas.semantic.agenda import staleness
from code_atlas.semantic.changeset import apply, revert, validate
from code_atlas.graphdata import load_index
from test_tours import setup, TOUR


def test_confirm_reanchors_without_text_changes_is_idempotent_and_revertible(tmp_path):
    repo = setup(tmp_path)
    assert apply(repo, {"tours": [TOUR]}, run="tour")["ok"]
    before = store.load(repo)
    p = repo / "app/models.py"
    p.write_text(p.read_text("utf8").replace("return self.save()", "return self.save() or None"), "utf8")
    p = repo / "app/service.py"
    p.write_text(p.read_text("utf8").replace("return u.greet()", "return [u.greet()]"), "utf8")
    scan(repo)
    rid = next(iter(before.relations))
    cs = {"notes": [{"op": "confirm", "target": "app/models.py#User"}],
          "relations": [{"op": "confirm", "id": rid}],
          "tours": [{"op": "confirm", "id": TOUR["id"]}]}
    assert staleness(repo, load_index(repo), store.load(repo))
    assert validate(repo, cs)[0]["ok"]
    assert store.load(repo).notes == before.notes
    assert apply(repo, cs, run="confirm")["ok"]
    after = store.load(repo)
    assert after.notes["app/models.py#User"]["text"] == before.notes["app/models.py#User"]["text"]
    assert not staleness(repo, load_index(repo), after)
    assert all(s["anchor"] == load_index(repo).anchor(s["at"]) for s in after.tours[TOUR["id"]]["steps"])
    assert apply(repo, cs, run="again")["ok"]
    assert store.load(repo).notes == after.notes
    assert revert(repo, "again")["ok"]
    assert revert(repo, "confirm")["ok"]
    assert staleness(repo, load_index(repo), store.load(repo))


def test_confirm_rejects_missing_targets_content_edits_and_broken_tours(tmp_path):
    repo = setup(tmp_path)
    cs = {"notes": [{"op": "confirm", "target": "no.py", "text": "x"}],
          "relations": [{"op": "confirm", "id": "none"}], "tours": [{"op": "confirm", "id": "none"}]}
    assert not validate(repo, cs)[0]["ok"]
    assert apply(repo, {"tours": [TOUR]}, run="tour")["ok"]
    p = repo / "main.py"
    p.write_text("print('disconnected')\n", "utf8")
    scan(repo)
    report = validate(repo, {"tours": [{"op": "confirm", "id": TOUR["id"]}]})[0]
    assert not report["ok"]
    assert any("no call path" in e for e in report["errors"])
