from code_atlas.scan import scan
from code_atlas.site import build_data
from code_atlas.site import build_site
import pytest

from test_scan import REPO, write


def inside(c, p):
    return c[0] >= p[0] - 0.01 and c[1] >= p[1] - 0.01 and c[0] + c[2] <= p[0] + p[2] + 0.01 and c[1] + c[3] <= p[1] + p[3] + 0.01


def overlap(a, b):
    return a[0] < b[0] + b[2] and a[0] + a[2] > b[0] and a[1] < b[1] + b[3] and a[1] + a[3] > b[1]


def test_layout_nests_without_overlap(tmp_path):
    write(tmp_path, REPO)
    scan(tmp_path)
    data = build_data(tmp_path)
    g = data["graph"]
    regions, files = g["regions"], g["files"]
    assert files and all(f["kind"] != "vendored" for f in files)
    siblings: dict = {}
    for r in regions:
        if r["parent"] is not None:
            assert inside(r["box"], regions[r["parent"]]["box"])
            siblings.setdefault(("r", r["parent"]), []).append(r["box"])
    for f in files:
        assert inside(f["box"], regions[f["region"]]["box"])
        siblings.setdefault(("r", f["region"]), []).append(f["box"])
    syms = [s for chunk, _ in data["chunks"] for s in chunk["symbols"]]
    by_id = {s["id"]: s for s in syms}
    for s in syms:
        parent = by_id[s["parent"]]["box"] if s["parent"] else files[s["file"]]["box"]
        assert inside(s["box"], parent)
        siblings.setdefault(("s", s["parent"] or s["file"]), []).append(s["box"])
    for boxes in siblings.values():
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                assert not overlap(a, b)


def test_site_has_search_index_and_source(tmp_path):
    write(tmp_path, REPO)
    scan(tmp_path)
    data = build_data(tmp_path)
    names = {row[0] for row in data["search"]}
    assert {"User", "make_user", "Client"} <= names
    code = {p for _, c in data["chunks"] for p in c}
    assert "app/service.py" in code
    assert all(not c for _, c in build_data(tmp_path, include_source=False)["chunks"])


def test_build_refreshes_symbol_spans_with_source(tmp_path):
    path = tmp_path / "Panel.tsx"
    path.write_text('interface Props { title: string }\nexport class Panel { render() { return <div/>; } }\n', encoding="utf-8")
    scan(tmp_path)
    current = '// added header\n\ninterface Props {\n  title: string\n}\nexport class Panel {\n  render() { return <div/>; }\n}\n'
    path.write_text(current, encoding="utf-8")
    with pytest.raises(ValueError, match="Source changed since scan: Panel.tsx"):
        build_data(tmp_path)
    out = tmp_path / ".codemap" / "cache" / "site"
    build_site(tmp_path, out)
    data = build_data(tmp_path)
    symbols = {s["name"]: s for c, _ in data["chunks"] for s in c["symbols"]}
    code = next(c["Panel.tsx"] for _, c in data["chunks"] if "Panel.tsx" in c)
    assert code.replace("\r\n", "\n") == current
    assert (symbols["Props"]["start"], symbols["Props"]["end"]) == (3, 5)
    assert (symbols["Panel"]["start"], symbols["Panel"]["end"]) == (6, 8)
    assert (symbols["render"]["start"], symbols["render"]["end"]) == (7, 7)
    lines = code.splitlines()
    assert "\n".join(lines[2:5]) == "interface Props {\n  title: string\n}"
