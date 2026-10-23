import subprocess
from code_atlas.scan import scan
from code_atlas.index import connect


def test_git_rename_tracks_a_moved_symbol_even_when_its_body_changes(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True)
    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    source = "def moved():\n    return 1\n" + "# context\n" * 30
    (tmp_path / "old.py").write_text(source)
    git("add", "old.py")
    git("commit", "-m", "base")
    scan(tmp_path)
    git("mv", "old.py", "new.py")
    (tmp_path / "new.py").write_text(source.replace("return 1", "return 2"))
    scan(tmp_path)
    db = connect(tmp_path)
    assert db.execute("select new from aliases where old='old.py#moved'").fetchone()[0] == "new.py#moved"
    db.close()
