"""Regression tests: one bad file never aborts a scan, and scan order never changes resolution."""

from test_scan import db, edges, write

from code_atlas.scan import scan


def sfc_ids(tmp_path):
    return [r[0] for r in db(tmp_path).execute(
        "select id from symbols where path = 'src/Widget.vue' order by rowid")]


def test_sfc_duplicate_symbols_are_deterministic(tmp_path):
    write(tmp_path, {
        "src/Widget.vue": '''
            <script>
            function load() { return 1; }
            </script>
            <script setup>
            function load() { return 2; }
            </script>
            <template><div /></template>
        ''',
    })
    rep = scan(tmp_path)
    assert not rep.failed, rep.failed
    assert sfc_ids(tmp_path) == ["src/Widget.vue#load", "src/Widget.vue#load~2"]
    scan(tmp_path, full=True)
    assert sfc_ids(tmp_path) == ["src/Widget.vue#load", "src/Widget.vue#load~2"]


def test_sfc_script_inside_html_comment_is_ignored(tmp_path):
    write(tmp_path, {
        "src/Widget.vue": '''
            <!-- previous implementation
            <script>
            function load() { return 1; }
            </script>
            -->
            <script setup>
            function refresh() { return 2; }
            </script>
        ''',
    })
    rep = scan(tmp_path)
    assert not rep.failed, rep.failed
    assert [r[0] for r in db(tmp_path).execute(
        "select name from symbols where path = 'src/Widget.vue'")] == ["refresh"]


def test_duplicate_symbol_ids_do_not_abort_scan(tmp_path, monkeypatch):
    import code_atlas.scan as scan_mod
    real = scan_mod.extract

    def duplicating(path, lang, tier, text):
        fx = real(path, lang, tier, text)
        if path == "a.py":
            fx["symbols"].extend([dict(fx["symbols"][0]), dict(fx["symbols"][0])])
        return fx

    monkeypatch.setattr(scan_mod, "extract", duplicating)
    write(tmp_path, {"a.py": "def dup():\n    pass\n", "b.py": "def b():\n    pass\n"})
    rep = scan(tmp_path)
    assert not rep.failed, rep.failed
    assert db(tmp_path).execute("select count(*) from symbols where path = 'a.py'").fetchone()[0] == 1
    assert db(tmp_path).execute("select count(*) from symbols where path = 'b.py'").fetchone()[0] == 1


def test_incremental_edges_match_full_with_tied_candidates(tmp_path):
    write(tmp_path, {
        "go.mod": "module example.com/m\n\ngo 1.21\n",
        "main.go": "package main\n\nfunc main() { openFile() }\n",
        "util_linux.go": "package main\n\nfunc openFile() {}\n",
        "util_windows.go": "package main\n\nfunc openFile() {}\n",
    })
    scan(tmp_path)
    first = edges(tmp_path)
    assert ("main.go#main", "util_linux.go#openFile", "calls") in first  # the sorted-first candidate
    (tmp_path / "util_linux.go").write_text("package main\n\nfunc openFile() { println(1) }\n", encoding="utf-8")
    rep = scan(tmp_path)
    assert rep.parsed == 1
    incremental = edges(tmp_path)
    scan(tmp_path, full=True)
    assert incremental == first
    assert edges(tmp_path) == incremental
