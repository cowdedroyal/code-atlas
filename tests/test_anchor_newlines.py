from pathlib import Path
from code_atlas.scan import scan
from code_atlas.graphdata import load_index
from code_atlas.semantic import store
from code_atlas.semantic.agenda import staleness
from code_atlas.semantic.changeset import apply


def test_file_anchors_ignore_checkout_line_endings_but_detect_code_changes(tmp_path):
    p = tmp_path / "example.py"
    source = b'def example():\n    return 1\n'
    p.write_bytes(source.replace(b'\n', b'\r\n'))
    scan(tmp_path)
    cs = {"notes": [{"target": "example.py", "text": "Returns one."}],
          "relations": [{"src": "example.py", "dst": "example.py#example", "family": "control", "label": "defines",
                         "rationale": "The module defines example.", "evidence": ["example.py"]}]}
    assert apply(tmp_path, cs, run="note")["ok"]
    before = load_index(tmp_path)
    p.write_bytes(source)
    scan(tmp_path)
    after = load_index(tmp_path)
    assert before.mapped["example.py"]["hash"] != after.mapped["example.py"]["hash"]
    assert before.anchor("example.py") == after.anchor("example.py")
    assert not staleness(tmp_path, after, store.load(tmp_path))
    p.write_bytes(source.replace(b'return 1', b'return 2'))
    scan(tmp_path)
    assert staleness(tmp_path, load_index(tmp_path), store.load(tmp_path))


def test_old_index_adds_anchor_column_without_discarding_cached_facts(tmp_path):
    import sqlite3
    root = tmp_path / ".codemap" / "cache"
    root.mkdir(parents=True)
    db = sqlite3.connect(root / "index.sqlite")
    db.execute('create table files (path text primary key, kind text, lang text, tier text, size integer, lines integer, hash text, entry integer, doc text, mtime integer)')
    db.close()
    (tmp_path / "main.py").write_bytes(b'print("ok")\r\n')
    scan(tmp_path)
    idx = load_index(tmp_path)
    assert idx.mapped["main.py"]["anchor_hash"]
