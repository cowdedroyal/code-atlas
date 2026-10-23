from copy import deepcopy

from code_atlas.module_scope import declaration_folds, execution_symbols
from code_atlas.scan import scan
from code_atlas.site import build_data
from code_atlas.index import connect
from test_scan import write


def test_module_scopes_preserve_original_edges_and_ignore_imports_and_doc_links():
    files = {p: {"kind": "source", "tier": "1", "lines": 20} for p in ("a.js", "b.js", "only_import.js")}
    files["README.md"] = {"kind": "docs", "tier": "outline", "lines": 10}
    symbols = {"b.js#target": {"id": "b.js#target"}, "a.js#@module": {"id": "a.js#@module"}}
    edges = [
        {"src": "a.js", "dst": "b.js#target", "type": "calls", "via": "import", "conf": .9, "count": 2},
        {"src": "b.js#target", "dst": "a.js", "type": "references", "via": "local", "conf": .8, "count": 1},
        {"src": "only_import.js", "dst": "b.js", "type": "imports", "via": "import"},
        {"src": "README.md", "dst": "b.js", "type": "references", "via": "link"},
    ]
    original = deepcopy((files, symbols, edges))
    syms, projected, scopes = execution_symbols(files, symbols, edges)
    assert (files, symbols, edges) == original
    assert scopes == {"a.js": "a.js#@module~2"}
    assert len(syms) == 3
    assert projected[0] == {**edges[0], "src": scopes["a.js"]}
    assert projected[1] == {**edges[1], "dst": scopes["a.js"]}
    assert projected[2:] == edges[2:]


def test_callbacks_and_python_top_level_calls_have_symbol_endpoints_across_chunks(tmp_path, monkeypatch):
    write(tmp_path, {
        "lib/font.ts": "export function mapFontRenderer() { return 42; }\n",
        "tests/font.test.mjs": "import { mapFontRenderer } from '../lib/font.ts';\nimport test from 'node:test';\ntest('font', () => { mapFontRenderer(); });\n",
        "py/font.py": "def render():\n    return 42\n",
        "py/main.py": "from font import render\nrender()\n",
        "imports_only.ts": "import { mapFontRenderer } from './lib/font.ts';\n",
    })
    scan(tmp_path)
    # Separate the two endpoints to verify storage and chunk loading from either side.
    monkeypatch.setattr("code_atlas.site.CHUNK_SYMBOLS", 1)
    data = build_data(tmp_path)
    graph = data["graph"]
    files = {f["id"]: f for f in graph["files"]}
    test_scope = files["tests/font.test.mjs"]["module_scope"]
    py_scope = files["py/main.py"]["module_scope"]
    assert "module_scope" not in files["imports_only.ts"]
    assert files["tests/font.test.mjs"]["chunk"] != files["lib/font.ts"]["chunk"]
    syms = {s["id"]: s for c, _ in data["chunks"] for s in c["symbols"]}
    assert syms[test_scope]["kind"] == "module_scope"
    assert syms[test_scope]["parent"] is None
    assert (syms[test_scope]["start"], syms[test_scope]["end"]) == (1, 4)
    assert test_scope in {row[3] for row in data["search"]}
    for path in ("tests/font.test.mjs", "lib/font.ts"):
        c = data["chunks"][files[path]["chunk"]][0]
        assert any(e[:3] == [test_scope, "lib/font.ts#mapFontRenderer", "calls"] for e in c["edges"])
    all_edges = [e for c, _ in data["chunks"] for e in c["edges"]]
    assert any(e[:3] == [py_scope, "py/font.py#render", "calls"] for e in all_edges)
    # L1 remains aggregated onto files; the underlying scanner ownership is unchanged.
    test_i = graph["files"].index(files["tests/font.test.mjs"])
    font_i = graph["files"].index(files["lib/font.ts"])
    assert any(e[0:2] == [test_i, font_i] and "calls" in e[3] for e in graph["file_edges"])
    db = connect(tmp_path)
    assert db.execute("select 1 from edges where src=? and dst=? and type='calls'",
                      ("tests/font.test.mjs", "lib/font.ts#mapFontRenderer")).fetchone()
    assert not db.execute("select 1 from symbols where kind='module_scope'").fetchone()
    db.close()


def test_python_folds_preserve_decorators_signatures_and_top_level_calls():
    from code_atlas.extract.python import extract_python
    text = """app = create_app()
@app.command()
def run(flag=option()):
    print(flag)
    work()

if __name__ == "__main__":
    app()
"""
    facts = extract_python(text)
    symbols = [{"id": f"cli.py#{s['q']}", "name": s["n"], "kind": s["k"], "start": s["s"], "end": s["e"]} for s in facts["symbols"]]
    folds = declaration_folds(text, "python", symbols)
    assert folds == [{"from": 4, "to": 5, "name": "run", "id": "cli.py#run"}]


def test_ts_folds_preserve_arrow_initializer_headers_and_anonymous_callbacks():
    from code_atlas.extract.ecmascript import extract_ecmascript
    text = """const render = (x = getDefault()) => {
  return x;
};
test('anonymous', () => {
  render();
});
class Example {
  method() {
    render();
  }
}
"""
    facts = extract_ecmascript(text, "typescript")
    symbols = [{"id": f"test.ts#{s['q']}", "name": s["n"], "kind": s["k"], "start": s["s"], "end": s["e"]} for s in facts["symbols"]]
    folds = declaration_folds(text, "typescript", symbols)
    assert [(f["from"], f["to"], f["name"]) for f in folds] == [(2, 2, "render"), (8, 10, "Example")]
    assert not any(f["from"] <= 5 <= f["to"] for f in folds)


def test_sfc_folds_shift_script_ranges_and_preserve_template():
    from code_atlas.extract.ecmascript import extract_sfc
    text = '<template><div/></template>\n<script lang="ts">\nexport function work() {\n  console.log("hi");\n}\nwork();\n</script>\n'
    facts = extract_sfc(text)
    symbols = [{"id": f"App.vue#{s['q']}", "name": s["n"], "kind": s["k"], "start": s["s"], "end": s["e"]} for s in facts["symbols"]]
    folds = declaration_folds(text, "vue", symbols)
    assert [(f["from"], f["to"]) for f in folds] == [(4, 4)]
