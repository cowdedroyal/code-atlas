from code_atlas.scan import scan
from code_atlas.site import build_site, build_data
from test_scan import write


def test_added_file_preserves_existing_sibling_positions(tmp_path):
    write(tmp_path, {f"app/f{i}.py": f"def function{i}():\n    return {i}\n" for i in range(6)})
    scan(tmp_path)
    first = build_data(tmp_path, stable_layout=True)
    write(tmp_path, {"app/added.py": "def added():\n    return 1\n"})
    scan(tmp_path)
    second = build_data(tmp_path, stable_layout=True)
    old = {f["id"]: f["box"] for f in first["graph"]["files"]}
    now = {f["id"]: f["box"] for f in second["graph"]["files"]}
    assert all(now[p] == box for p, box in old.items())
    from test_site import overlap
    boxes = list(now.values())
    assert all(not overlap(a,b) for i,a in enumerate(boxes) for b in boxes[i+1:])
