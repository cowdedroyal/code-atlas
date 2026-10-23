import json

from code_atlas.scan import scan
from code_atlas.semantic import store
from code_atlas.semantic.agenda import agenda
from code_atlas.semantic.changeset import apply, revert, validate
from code_atlas.semantic.context import context, outline, semantic_status
from code_atlas.site import build_data

from test_scan import REPO, write

GOOD = {
    "intent": "regionize",
    "regions": [
        {"id": "domain", "name": "User domain", "role": "core", "responsibility": "Rules about users and how they are saved."},
        {"id": "services", "name": "Services", "role": "orchestration", "responsibility": "Turns requests into domain calls."},
        {"id": "web", "name": "Web UI", "role": "presentation", "responsibility": "What the user sees in the browser."},
    ],
    "assign": [
        {"match": "app/", "region": "services"},
        {"match": "app/models.py", "region": "domain"},
        {"match": "web/", "region": "web"},
    ],
    "notes": [
        {"target": "region:domain", "text": "Users and their persistence rules."},
        {"target": "app/models.py#User", "text": "A user; greeting saves it first."},
    ],
    "relations": [
        {"src": "region:services", "dst": "region:domain", "family": "control", "label": "creates and greets users",
         "rationale": "make_user instantiates User and calls greet", "evidence": ["app/service.py#make_user"]},
    ],
}


def setup(tmp_path):
    write(tmp_path, REPO)
    scan(tmp_path)
    return tmp_path


def test_validate_reports_every_problem(tmp_path):
    repo = setup(tmp_path)
    bad = {
        "regions": [{"id": "Bad Slug", "name": "x", "role": "core", "responsibility": "x"},
                    {"id": "ok", "name": "x", "role": "wizard", "responsibility": "x" * 500, "parent": "nope"}],
        "assign": [{"match": "nowhere/**", "region": "ok"}, {"match": "app/", "region": "missing"}],
        "notes": [{"target": "app/none.py#X", "text": "hi"}],
        "relations": [{"src": "region:ok", "dst": "region:ok", "family": "magic", "label": "l", "rationale": "r",
                       "evidence": ["app/service.py#nothing"]}],
    }
    report, _, _ = validate(repo, bad)
    text = "\n".join(report["errors"])
    assert not report["ok"]
    for needle in ["lowercase slug", "role 'wizard'", "limit 140", "parent 'nope'", "matches no mapped file",
                   "region 'missing' does not exist", "note app/none.py#X", "two different ends", "family 'magic'",
                   "evidence 'app/service.py#nothing'"]:
        assert needle in text, needle
    assert not (repo / ".codemap" / "regions.yaml").exists()  # validate writes nothing


def test_apply_and_effective_regions(tmp_path):
    repo = setup(tmp_path)
    r = apply(repo, GOOD, run="r1")
    assert r["ok"], r
    assert r["state"]["unassigned"] > 0  # main.py, go/, README… are left over
    assert any("unassigned" in w for w in r["warnings"])
    sem = store.load(repo)
    assert [x["id"] for x in sem.regions] == ["domain", "services", "web"]
    assert sem.notes["app/models.py#User"]["anchor"]  # written against a content hash
    data = build_data(repo)
    regions = {x["id"]: x for x in data["graph"]["regions"]}
    files = {f["id"]: f for f in data["graph"]["files"]}
    assert data["graph"]["regions"][files["app/models.py"]["region"]]["id"] == "region:domain"  # later rule wins
    assert data["graph"]["regions"][files["app/service.py"]["region"]]["id"] == "region:services"
    assert regions["region:domain"]["note"].startswith("Users")
    assert "region:_unassigned" in regions
    assert data["graph"]["relations"][0]["label"] == "creates and greets users"
    st = semantic_status(repo)
    assert st["regions"] == 3 and st["notes"] == 2 and st["relations"] == 1 and st["unassigned_files"] > 0


def test_apply_is_idempotent(tmp_path):
    repo = setup(tmp_path)
    apply(repo, GOOD, run="r1")
    first = {k: v for k, v in store.snapshot(repo).items() if k != "log.jsonl"}
    apply(repo, GOOD, run="r2")
    assert {k: v for k, v in store.snapshot(repo).items() if k != "log.jsonl"} == first


def test_agenda_flow(tmp_path):
    repo = setup(tmp_path)
    a = agenda(repo, limit=50)
    assert a["tasks"][0]["type"] == "regionize"
    apply(repo, GOOD, run="r1")
    a = agenda(repo, limit=50)
    types = {t["type"] for t in a["tasks"]}
    assert "assign" in types and "annotate-region" in types
    assert not any(t["type"] == "annotate-region" and t["target"] == "region:domain" for t in a["tasks"])


def test_staleness_and_rename(tmp_path):
    repo = setup(tmp_path)
    apply(repo, GOOD, run="r1")
    # edit the evidence of the relation and the body of the noted class
    svc = (repo / "app/service.py").read_text(encoding="utf-8").replace("return u.greet()", "return [u.greet()]")
    (repo / "app/service.py").write_text(svc, encoding="utf-8")
    models = (repo / "app/models.py").read_text(encoding="utf-8").replace("return self.save()", "return self.save() or None")
    (repo / "app/models.py").write_text(models, encoding="utf-8")
    scan(repo)
    a = agenda(repo, limit=100, types=["refresh"])
    targets = {t["target"] for t in a["tasks"]}
    assert "app/models.py#User" in targets
    assert any(t.startswith("rel-") for t in targets)
    # a rename keeps the note attached through the alias
    (repo / "app/models.py").unlink()
    (repo / "app/entities.py").write_text(models, encoding="utf-8")
    scan(repo)
    report, _, _ = validate(repo, {})
    assert not any("note app/models.py#User" in e for e in report["errors"])


def test_revert(tmp_path):
    repo = setup(tmp_path)
    apply(repo, GOOD, run="r1")
    apply(repo, {"notes": [{"target": "region:web", "text": "The React app."}]}, run="r2")
    assert not revert(repo, "r1")["ok"]  # r2 came after
    assert revert(repo, "r2")["ok"]
    assert "region:web" not in store.load(repo).notes
    assert revert(repo, "r1")["ok"]
    assert store.load(repo).regions == []


def test_outline_and_context(tmp_path):
    repo = setup(tmp_path)
    o = outline(repo)
    assert any(d["dir"] == "app" for d in o["dirs"])
    assert "main.py" in o["entry_points"]
    apply(repo, GOOD, run="r1")
    c = context(repo, "region:services")
    assert c["ok"] and c["files"] >= 3
    assert any(d["on"] == "region:domain" for d in c["depends_on"])
    assert json.dumps(c)  # serialisable
