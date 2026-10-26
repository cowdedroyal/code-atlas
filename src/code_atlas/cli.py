"""Command line: every command prints JSON so agents can parse it; humans can pipe through `jq`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Code Atlas — a zoomable map of a code repository.")

RepoOpt = typer.Option(Path("."), "--repo", "-r", help="Repository root (default: current directory).")


def emit(obj) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


@app.command()
def scan(repo: Path = RepoOpt,
         full: bool = typer.Option(False, help="Ignore the parse cache and re-parse every file."),
         workers: int = typer.Option(None, help="Parser processes (default: CPU count)."),
         scip: list[Path] = typer.Option(None, "--scip", help="SCIP index file(s) with precise references; "
                                         "remembered for later scans.")):
    """Scan the repository and (re)build the structure index."""
    from .scan import scan as run_scan
    rep = run_scan(repo, full=full, workers=workers, scip=scip or None)
    emit({"ok": True, **rep.__dict__, "failed": rep.failed[:20], "failed_count": len(rep.failed)})


@app.command()
def status(repo: Path = RepoOpt):
    """Size of the map, language coverage and how much of the code resolved into edges."""
    from .query import status as q
    from .semantic.context import semantic_status
    out = q(repo.resolve())
    out["semantic"] = semantic_status(repo.resolve())
    emit(out)


@app.command()
def show(target: str, repo: Path = RepoOpt,
         code: bool = typer.Option(False, help="Include the source text."),
         limit: int = typer.Option(40, help="Max neighbours per direction.")):
    """Details of a file or symbol (id `path` or `path#Qual.name`, or a bare name to search)."""
    from .query import show as q
    emit(q(repo.resolve(), target, code=code, limit=limit))


@app.command()
def path(src: str, dst: str, repo: Path = RepoOpt,
         hops: int = typer.Option(6, help="Maximum number of hops.")):
    """The shortest call path from one file/symbol to another (what a tour step needs)."""
    from .graphdata import load_index
    from .semantic.tours import PathFinder
    idx = load_index(repo.resolve())
    a, b = idx.canonical(src), idx.canonical(dst)
    missing = [x for x in (a, b) if not idx.exists(x)]
    if missing:
        emit({"ok": False, "error": f"not in the index: {', '.join(missing)}"})
        raise typer.Exit(1)
    chain = PathFinder(idx).path(a, b, hops)
    emit({"ok": chain is not None, "path": chain,
          **({} if chain else {"hint": "no structural path; if they are linked over HTTP, a queue, files…, record a "
                                        "relation and use it as the step's via_relation"})})


def _read_changeset(file: Path | None) -> dict:
    text = file.read_text("utf-8") if file and str(file) != "-" else sys.stdin.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        emit({"ok": False, "errors": [f"changeset is not valid JSON: {e}"]})
        raise typer.Exit(1)


@app.command()
def outline(repo: Path = RepoOpt, depth: int = typer.Option(3, help="Directory depth to show.")):
    """The whole repository at a glance: directories, entry points, important files, externals."""
    from .semantic.context import outline as q
    emit(q(repo.resolve(), depth))


@app.command()
def context(target: str = typer.Argument(".", help="region:<id>, a directory, or a file."),
            repo: Path = RepoOpt, limit: int = typer.Option(40, help="Max member files listed.")):
    """Members, docstrings, main symbols and dependencies of a region, directory or file."""
    from .semantic.context import context as q
    emit(q(repo.resolve(), target, limit))


@app.command()
def agenda(repo: Path = RepoOpt, limit: int = typer.Option(20), type: list[str] = typer.Option(None, "--type",
           help="Only these task types (regionize, assign, refresh, link, annotate…).")):
    """What the semantic layer needs next, most important first."""
    from .semantic.agenda import agenda as q
    emit(q(repo.resolve(), limit, type))


@app.command()
def validate(file: Path = typer.Option(None, "--file", "-f", help="Changeset JSON (default: stdin)."), repo: Path = RepoOpt):
    """Dry-run a changeset: report every error and warning, change nothing."""
    from .semantic.changeset import validate as v
    report, _, _ = v(repo.resolve(), _read_changeset(file))
    emit(report)
    raise typer.Exit(0 if report["ok"] else 1)


@app.command()
def apply(file: Path = typer.Option(None, "--file", "-f", help="Changeset JSON (default: stdin)."), repo: Path = RepoOpt,
          run: str = typer.Option(None, help="Run id (default: timestamp).")):
    """Validate and write a changeset into .codemap/."""
    from .semantic.changeset import apply as a
    report = a(repo.resolve(), _read_changeset(file), run)
    emit(report)
    raise typer.Exit(0 if report["ok"] else 1)


@app.command()
def log(repo: Path = RepoOpt, limit: int = typer.Option(20)):
    """Applied changesets, newest first."""
    from .semantic.store import read_log
    emit(list(reversed(read_log(repo.resolve())))[:limit])


@app.command()
def revert(run: str, repo: Path = RepoOpt):
    """Undo the most recent run (only if nothing was applied after it)."""
    from .semantic.changeset import revert as r
    emit(r(repo.resolve(), run))


card_app = typer.Typer(help="Explainer cards: visual answers to a reader's questions, hung on the code they explain.")
app.add_typer(card_app, name="card")


@card_app.command("validate")
def card_validate(path: Path = typer.Argument(..., help="Card directory (card.yaml + index.html)."), repo: Path = RepoOpt):
    """Dry-run a card: lint its files and check every anchor against the index."""
    from .semantic.explainers import validate_card
    report = validate_card(repo.resolve(), path.resolve())
    emit(report)
    raise typer.Exit(0 if report["ok"] else 1)


@card_app.command("apply")
def card_apply(path: Path = typer.Argument(..., help="Card directory (card.yaml + index.html)."), repo: Path = RepoOpt,
               run: str = typer.Option(None, help="Run id (default: timestamp).")):
    """Validate a card and write it into .codemap/explainers/ (same id replaces; identical content changes nothing)."""
    from .semantic.explainers import apply_card
    report = apply_card(repo.resolve(), path.resolve(), run)
    emit(report)
    raise typer.Exit(0 if report["ok"] else 1)


@card_app.command("search")
def card_search(query: str = typer.Argument("", help="Words from the title, questions, summary or tags; empty lists all."),
                anchor: str = typer.Option(None, help="Only cards that explain this file, symbol or region:<id>."),
                repo: Path = RepoOpt, limit: int = typer.Option(10)):
    """Find existing cards — run before writing a new one, and update a match instead of adding a duplicate."""
    from .semantic.explainers import search_cards
    emit(search_cards(repo.resolve(), query, anchor, limit))


@card_app.command("confirm")
def card_confirm(card_id: str, repo: Path = RepoOpt, run: str = typer.Option(None)):
    """After reviewing a stale card against the current code, refresh its anchors without changing its text."""
    from .semantic.explainers import confirm_card
    report = confirm_card(repo.resolve(), card_id, run)
    emit(report)
    raise typer.Exit(0 if report["ok"] else 1)


@card_app.command("delete")
def card_delete(card_id: str, repo: Path = RepoOpt, run: str = typer.Option(None)):
    """Remove a card (revertible with `codeatlas revert <run>`)."""
    from .semantic.explainers import delete_card
    report = delete_card(repo.resolve(), card_id, run)
    emit(report)
    raise typer.Exit(0 if report["ok"] else 1)


skill_app = typer.Typer(help="The agent skill that writes the semantic layer.")
app.add_typer(skill_app, name="skill")


@skill_app.command("install")
def skill_install(target: str = typer.Option("claude", help="claude → ~/.claude/skills, agents → ./.agents/skills, "
                                             "or a directory path."),
                  repo: Path = RepoOpt):
    """Copy the codeatlas skill where your coding agent looks for skills."""
    import shutil
    src = Path(__file__).parent / "skill"
    if target == "claude":
        dst = Path.home() / ".claude" / "skills" / "codeatlas"
    elif target == "agents":
        dst = repo.resolve() / ".agents" / "skills" / "codeatlas"
    else:
        dst = Path(target) / "codeatlas"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    emit({"ok": True, "installed": str(dst)})


@app.command()
def build(repo: Path = RepoOpt,
          out: Path = typer.Option(None, "--out", "-o", help="Output directory (default: .codemap/cache/site)."),
          no_source: bool = typer.Option(False, "--no-source", help="Do not embed source code in the site."),
          repack: bool = typer.Option(False, "--repack", help="Discard cached positions and compact the map.")):
    """Build the static map site (open index.html via any static file server)."""
    from .index import cache_dir
    from .site import build_site
    repo = repo.resolve()
    emit(build_site(repo, (out or cache_dir(repo) / "site").resolve(), include_source=not no_source, repack=repack))


@app.command()
def serve(repo: Path = RepoOpt,
          port: int = typer.Option(8765, help="Port to listen on."),
          scan_first: bool = typer.Option(True, "--scan/--no-scan", help="Run an incremental scan first."),
          no_source: bool = typer.Option(False, "--no-source"),
          watch: bool = typer.Option(True, "--watch/--no-watch", help="Rebuild when semantic files change.")):
    """Scan, build and serve the map at http://127.0.0.1:PORT."""
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from .index import cache_dir
    from .scan import scan as run_scan
    from .site import build_site
    repo = repo.resolve()
    if scan_first:
        rep = run_scan(repo)
        print(f"scanned {rep.files} files ({rep.parsed} parsed, {rep.reused} cached) in {rep.seconds}s", file=sys.stderr)
    out = cache_dir(repo) / "site"
    build_site(repo, out, include_source=not no_source, refresh_index=False)

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(out))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    stopped = threading.Event()
    if watch:
        from .watch import watch_semantics
        threading.Thread(target=watch_semantics, daemon=True,
                         args=(repo, lambda: build_site(repo, out, include_source=not no_source), stopped,
                               lambda msg: print(msg, file=sys.stderr, flush=True))).start()
    print(f"Code Atlas: http://127.0.0.1:{port}/", file=sys.stderr, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        httpd.server_close()


if __name__ == "__main__":
    app()
