from code_atlas.scan import scan
from code_atlas.index import connect
from test_scan import write


def test_unknown_receiver_is_not_linked_to_an_unrelated_unique_method(tmp_path):
    write(tmp_path, {"go.mod": "module example\n", "main.go": "package main\nfunc main() { unknown.Logf(\"hi\") }\n",
                     "other/other.go": "package other\ntype Logger struct{}\nfunc (l *Logger) Logf(s string) {}\n"})
    scan(tmp_path)
    db = connect(tmp_path)
    assert not db.execute("select 1 from edges where src='main.go#main' and dst like '%Logf' and type='calls'").fetchone()
    db.close()
