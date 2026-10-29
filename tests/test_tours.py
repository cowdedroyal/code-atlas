import json

import yaml

from code_atlas.graphdata import load_index
from code_atlas.scan import scan
from code_atlas.semantic import store
from code_atlas.semantic.agenda import agenda
from code_atlas.semantic.changeset import apply, validate
from code_atlas.semantic.explainers import apply_card
from code_atlas.semantic.tours import PathFinder, tour_status
from code_atlas.site import build_data

from test_scan import REPO, write
from test_semantic import GOOD

CARD_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><link rel="stylesheet" href="atlas-kit:1.css"></head>
<body><h1>One step</h1><p>What happens here, one frame at a time.</p>
<script src="atlas-kit:1.js"></script></body></html>
"""


def card_draft(tmp_path, cid="step-card", anchors=("app/service.py#make_user",)):
    d = tmp_path.parent / f"{tmp_path.name}-cards" / cid
    d.mkdir(parents=True, exist_ok=True)
    manifest = {"kit": 1, "id": cid, "title": "One step", "questions": ["What happens at this step?"],
                "summary": "A single step of the tour.", "anchors": list(anchors), "tags": ["tour"], "language": "en"}
    (d / "card.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True), "utf-8")
    (d / "index.html").write_text(CARD_PAGE, "utf-8")
    return d


TOUR = {
    "id": "make-a-user",
    "title": "Creating a user",
    "summary": "From the command line to the saved user.",
    "steps": [
        {"at": "main.py", "text": "The script entry point calls the service."},
        {"at": "app/service.py#make_user", "text": "Builds the user and logs the name."},
        {"at": "app/models.py#User.greet", "text": "Greeting saves the user first."},
        {"at": "app/models.py#Base.save", "text": "Saving validates."},
    ],
}


def setup(tmp_path):
    write(tmp_path, REPO)
    scan(tmp_path)
    assert apply(tmp_path, GOOD, run="r1")["ok"]
    return tmp_path


def test_path_finder(tmp_path):
    repo = setup(tmp_path)
    pf = PathFinder(load_index(repo))
    assert pf.path("main.py", "app/models.py#Base.save") is not None  # main → make_user → User → greet → save
    assert pf.path("app/service.py#make_user", "app/models.py#User.greet")[-1] == "app/models.py#User"  # via the class
    assert pf.path("main.py", "web/src/api.ts#post") is None


def test_valid_tour_applies_with_anchors(tmp_path):
    repo = setup(tmp_path)
    r = apply(repo, {"tours": [TOUR]}, run="t1")
    assert r["ok"], r["errors"]
    t = store.load(repo).tours["make-a-user"]
    assert all(s.get("anchor") for s in t["steps"])
    assert (repo / ".codemap" / "tours" / "make-a-user.yaml").exists()


def test_unconnected_steps_are_rejected(tmp_path):
    repo = setup(tmp_path)
    bad = {**TOUR, "steps": [TOUR["steps"][0], {"at": "web/src/api.ts#post", "text": "Jumps to the web."}]}
    report, _, _ = validate(repo, {"tours": [bad]})
    assert not report["ok"]
    assert any("no call path to web/src/api.ts#post from any earlier step" in e for e in report["errors"])
    # unknown step, missing text, too few steps
    report, _, _ = validate(repo, {"tours": [{"id": "x", "title": "t", "steps": [{"at": "nope.py"}]}]})
    assert any("2–20 steps" in e for e in report["errors"])


def test_via_relation_crosses_invisible_links(tmp_path):
    repo = setup(tmp_path)
    rel_id = next(iter(store.load(repo).relations))  # services → domain
    tour = {"id": "log-then-validate", "title": "Across the boundary", "steps": [
        {"at": "app/util.py#log", "text": "Logs."},
        {"at": "app/models.py#Base.validate", "text": "Validated on the other side.", "via_relation": rel_id},
    ]}
    report, _, _ = validate(repo, {"tours": [tour]})
    assert report["ok"], report["errors"]
    wrong = {**tour, "steps": [tour["steps"][1], {**tour["steps"][0], "via_relation": rel_id}]}  # reversed direction
    report, _, _ = validate(repo, {"tours": [wrong]})
    assert any("does not connect" in e for e in report["errors"])


def test_tour_goes_stale_and_shows_in_agenda(tmp_path):
    repo = setup(tmp_path)
    apply(repo, {"tours": [TOUR]}, run="t1")
    models = (repo / "app/models.py").read_text(encoding="utf-8").replace("return self.validate()", "return bool(self.validate())")
    (repo / "app/models.py").write_text(models, encoding="utf-8")
    scan(repo)
    st = tour_status(load_index(repo), store.load(repo))["make-a-user"]
    assert st["state"] == "stale" and st["steps"][3] == "stale"
    a = agenda(repo, limit=50, types=["refresh"])
    assert any(t["target"] == "tour:make-a-user" for t in a["tasks"])


def test_step_may_return_to_an_earlier_orchestrator(tmp_path):
    repo = setup(tmp_path)
    # make_user calls User() and then util.log: log is not reachable from User.greet, but it is from make_user
    tour = {"id": "fan-out", "title": "Fan-out", "steps": [
        {"at": "app/service.py#make_user", "text": "Orchestrates."},
        {"at": "app/models.py#User.greet", "text": "First call."},
        {"at": "app/util.py#log", "text": "Second call, back in make_user."},
    ]}
    report, _, _ = validate(repo, {"tours": [tour]})
    assert report["ok"], report["errors"]
    apply(repo, {"tours": [tour]}, run="t1")
    steps = build_data(repo)["graph"]["tours"][0]["steps"]
    assert steps[2]["from"] == 0  # the leg is drawn from make_user, not from greet


def test_step_can_carry_an_explainer_card(tmp_path):
    repo = setup(tmp_path)
    assert apply_card(repo, card_draft(tmp_path), run="c1")["ok"]
    tour = {**TOUR, "steps": [dict(s) for s in TOUR["steps"]]}
    tour["steps"][1]["card"] = "step-card"
    report, _, _ = validate(repo, {"tours": [tour]})
    assert report["ok"], report["errors"]
    assert apply(repo, {"tours": [tour]}, run="t1")["ok"]
    assert store.load(repo).tours["make-a-user"]["steps"][1]["card"] == "step-card"
    steps = build_data(repo)["graph"]["tours"][0]["steps"]
    assert steps[1]["card"] == "step-card"
    assert "card" not in steps[0]


def test_adding_a_card_to_an_unchanged_step_is_applied(tmp_path):
    repo = setup(tmp_path)
    assert apply(repo, {"tours": [TOUR]}, run="t1")["ok"]
    assert apply_card(repo, card_draft(tmp_path), run="c1")["ok"]
    tour = {**TOUR, "steps": [dict(s) for s in TOUR["steps"]]}
    tour["steps"][1]["card"] = "step-card"
    assert apply(repo, {"tours": [tour]}, run="t2")["ok"]
    assert store.load(repo).tours["make-a-user"]["steps"][1]["card"] == "step-card"


def test_step_card_must_exist_and_cover_the_step(tmp_path):
    repo = setup(tmp_path)
    tour = {**TOUR, "steps": [dict(s) for s in TOUR["steps"]]}
    tour["steps"][1]["card"] = "nope"
    report, _, _ = validate(repo, {"tours": [tour]})
    assert any("card 'nope' does not exist" in e for e in report["errors"]), report["errors"]
    assert apply_card(repo, card_draft(tmp_path, cid="elsewhere", anchors=("app/models.py#User.greet",)), run="c1")["ok"]
    tour["steps"][1]["card"] = "elsewhere"
    report, _, _ = validate(repo, {"tours": [tour]})
    assert any("does not explain this step" in e for e in report["errors"]), report["errors"]


def test_step_card_may_be_anchored_on_the_file(tmp_path):
    repo = setup(tmp_path)
    assert apply_card(repo, card_draft(tmp_path, anchors=("app/service.py",)), run="c1")["ok"]
    tour = {**TOUR, "steps": [dict(s) for s in TOUR["steps"]]}
    tour["steps"][1]["card"] = "step-card"
    report, _, _ = validate(repo, {"tours": [tour]})
    assert report["ok"], report["errors"]


def test_missing_step_card_marks_the_tour_broken(tmp_path):
    repo = setup(tmp_path)
    assert apply_card(repo, card_draft(tmp_path), run="c1")["ok"]
    tour = {**TOUR, "steps": [dict(s) for s in TOUR["steps"]]}
    tour["steps"][1]["card"] = "step-card"
    assert apply(repo, {"tours": [tour]}, run="t1")["ok"]
    from code_atlas.semantic.explainers import delete_card
    assert delete_card(repo, "step-card", run="c2")["ok"]
    st = tour_status(load_index(repo), store.load(repo), {})
    assert st["make-a-user"]["state"] == "broken"
    assert any("does not exist" in p for p in st["make-a-user"]["problems"])


def test_coarse_steps_warn_but_do_not_block(tmp_path):
    repo = setup(tmp_path)
    tour = {**TOUR, "steps": [
        {"at": "app/service.py#make_user", "text": "Builds the user; logs the name."},
        {"at": "app/models.py#User.greet", "text": "Greets the user."},
        {"at": "app/models.py#Base.save", "text": "Saves the record."},
    ]}
    report, _, _ = validate(repo, {"tours": [tour]})
    assert report["ok"], report["errors"]
    assert any("one action" in w for w in report["warnings"]), report["warnings"]
    tour["steps"] = [{"at": "app/service.py#make_user", "text": "Builds the user; logs the name."},
                       {"at": "app/util.py#log", "text": "Logs the name."},
                       {"at": "app/models.py", "text": "The model file."}]
    report, _, _ = validate(repo, {"tours": [tour]})
    assert report["ok"], report["errors"]
    assert any("stops at a file" in w for w in report["warnings"]), report["warnings"]


def test_cli_lists_tours_with_their_cards(tmp_path):
    from typer.testing import CliRunner
    from code_atlas.cli import app
    repo = setup(tmp_path)
    assert apply_card(repo, card_draft(tmp_path), run="c1")["ok"]
    tour = {**TOUR, "steps": [dict(s) for s in TOUR["steps"]]}
    tour["steps"][1]["card"] = "step-card"
    assert apply(repo, {"tours": [tour]}, run="t1")["ok"]
    r = CliRunner().invoke(app, ["tours", "-r", str(repo)])
    assert r.exit_code == 0, r.stdout
    data = json.loads(r.stdout)
    step = data["tours"][0]["steps"][1]
    assert step["card"] == "step-card" and step["card_ok"] is True
    assert "card" not in data["tours"][0]["steps"][0]


def test_site_exports_tours_with_hops(tmp_path):
    repo = setup(tmp_path)
    apply(repo, {"tours": [TOUR]}, run="t1")
    tours = build_data(repo)["graph"]["tours"]
    assert tours[0]["id"] == "make-a-user" and len(tours[0]["steps"]) == 4
    assert all(len(s["box"]) == 4 for s in tours[0]["steps"])
    assert tours[0]["steps"][2]["hops"] == [] or all(len(h) == 2 for h in tours[0]["steps"][2]["hops"])
