import sqlite3
import textwrap
from pathlib import Path

import pytest

from code_atlas.scan import scan


def write(root: Path, files: dict[str, str]):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


REPO = {
    "app/__init__.py": "",
    "app/models.py": '''
        class Base:
            def save(self):
                return self.validate()

            def validate(self):
                return True


        class User(Base):
            """A user."""
            name: str = ""

            def greet(self):
                return self.save()
    ''',
    "app/service.py": '''
        from .models import User
        from . import util


        def make_user(name):
            u = User()
            util.log(name)
            return u.greet()
    ''',
    "app/util.py": '''
        import os


        def log(msg):
            print(os.path.basename(msg))
    ''',
    "main.py": '''
        from app.service import make_user

        if __name__ == "__main__":
            make_user("x")
    ''',
    "tests/test_service.py": '''
        from app.service import make_user


        def test_make_user():
            make_user("a")
    ''',
    "web/src/api.ts": '''
        export function post(url: string) { return fetch(url); }
        export class Client { get() { return post("/x"); } }
    ''',
    "web/src/index.ts": '''
        export * from "./api";
    ''',
    "web/src/App.tsx": '''
        import { Client } from "./index";
        import Button from "@/ui/Button";
        export const App = () => { const c = new Client(); return <Button onClick={() => c.get()} />; };
    ''',
    "web/src/ui/Button.tsx": '''
        export default function Button(props: any) { return <button />; }
    ''',
    "web/tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}',
    "web/node_modules/react/index.js": "module.exports = {};",
    "go/util.go": '''
        package main

        func Helper() int { return 1 }
    ''',
    "go/main.go": '''
        package main

        type S struct{}

        func (s *S) Run() { Helper() }
        func main() { s := S{}; s.Run() }
    ''',
    "README.md": "# Demo\n\nSee [service](app/service.py).\n\n## Usage\n",
    "assets/logo.png": "\0PNG",
}


@pytest.fixture()
def repo(tmp_path):
    write(tmp_path, REPO)
    return tmp_path


def db(repo):
    c = sqlite3.connect(repo / ".codemap" / "cache" / "index.sqlite")
    c.row_factory = sqlite3.Row
    return c


def edges(repo, type_=None):
    q = "select src, dst, type from edges" + (" where type = ?" if type_ else "")
    return {(r["src"], r["dst"], r["type"]) for r in db(repo).execute(q, (type_,) if type_ else ())}


def test_classification_and_symbols(repo):
    scan(repo)
    c = db(repo)
    kinds = dict(c.execute("select path, kind from files").fetchall())
    assert kinds["web/node_modules/react/index.js"] == "vendored"
    assert kinds["assets/logo.png"] == "binary"
    assert kinds["tests/test_service.py"] == "test"
    syms = {r["id"]: r for r in c.execute("select * from symbols")}
    assert syms["app/models.py#User"]["kind"] == "class"
    assert syms["app/models.py#User"]["doc"] == "A user."
    assert syms["app/models.py#User.greet"]["kind"] == "method"
    assert syms["app/models.py#User.name"]["kind"] == "field"
    assert syms["go/main.go#S.Run"]["parent"] == "go/main.go#S"
    assert "README.md#Demo/Usage" in syms
    assert c.execute("select entry from files where path = 'main.py'").fetchone()[0] == 1


def test_python_edges(repo):
    scan(repo)
    e = edges(repo)
    assert ("app/service.py#make_user", "app/models.py#User", "instantiates") in e
    assert ("app/service.py#make_user", "app/util.py#log", "calls") in e
    assert ("app/models.py#User", "app/models.py#Base", "inherits") in e
    # self.save() climbs to the base class; self.validate() stays inside it
    assert ("app/models.py#User.greet", "app/models.py#Base.save", "calls") in e
    assert ("app/models.py#Base.save", "app/models.py#Base.validate", "calls") in e
    assert ("main.py", "app/service.py#make_user", "calls") in e
    assert ("tests/test_service.py", "app/service.py", "tests") in e
    # builtins and stdlib never become edges
    assert not any(d.endswith("#print") for _, d, _ in e)


def test_js_edges_follow_reexports_and_paths(repo):
    scan(repo)
    e = edges(repo)
    assert ("web/src/App.tsx#App", "web/src/api.ts#Client", "instantiates") in e
    assert ("web/src/App.tsx#App", "web/src/ui/Button.tsx#Button", "calls") in e  # JSX via tsconfig paths
    assert ("web/src/api.ts#Client.get", "web/src/api.ts#post", "calls") in e
    assert ("web/src/App.tsx", "web/src/index.ts", "imports") in e


def test_go_same_package(repo):
    scan(repo)
    e = edges(repo)
    assert ("go/main.go#S.Run", "go/util.go#Helper", "calls") in e


def test_doc_links(repo):
    scan(repo)
    assert ("README.md", "app/service.py", "references") in edges(repo)


def test_incremental_equals_full(repo):
    scan(repo)
    (repo / "app/util.py").write_text("import os\n\n\ndef log(msg, level=0):\n    print(msg)\n", encoding="utf-8")
    rep = scan(repo)
    assert rep.parsed == 1
    inc = edges(repo)
    inc_syms = {tuple(r) for r in db(repo).execute("select * from symbols")}
    scan(repo, full=True)
    assert edges(repo) == inc
    assert {tuple(r) for r in db(repo).execute("select * from symbols")} == inc_syms


def test_rename_is_tracked(repo):
    scan(repo)
    src = (repo / "app/util.py").read_text(encoding="utf-8")
    (repo / "app/util.py").unlink()
    (repo / "app/logging_util.py").write_text(src, encoding="utf-8")
    rep = scan(repo)
    assert rep.renamed >= 1
    row = db(repo).execute("select new from aliases where old = 'app/util.py#log'").fetchone()
    assert row and row[0] == "app/logging_util.py#log"


def test_sys_path_roots_and_ambiguity(tmp_path):
    write(tmp_path, {
        "run.py": '''
            import sys
            from pathlib import Path
            ROOT = Path(__file__).resolve().parent
            sys.path.insert(0, str(ROOT / "services" / "imaging"))
            import server


            def main():
                server.start()
        ''',
        "other.py": "import server\n",
        "services/imaging/server.py": "def start():\n    pass\n",
        "services/audio/server.py": "def start():\n    pass\n",
        "services/imaging/progress.py": "import server\n",
    })
    scan(tmp_path)
    e = edges(tmp_path)
    assert ("run.py#main", "services/imaging/server.py#start", "calls") in e
    assert ("services/imaging/progress.py", "services/imaging/server.py", "imports") in e  # script dir on sys.path
    assert not any(s == "other.py" for s, _, _ in e)  # two equally foreign server.py: no guess


def test_js_default_export_with_several_exports(tmp_path):
    write(tmp_path, {
        "src/View.tsx": "export function helper() { return 1; }\nexport default function View() { return helper(); }\n",
        "src/App.tsx": "import View from './View';\nexport function App() { return <View />; }\n",
    })
    scan(tmp_path)
    assert ("src/App.tsx#App", "src/View.tsx#View", "calls") in edges(tmp_path)
