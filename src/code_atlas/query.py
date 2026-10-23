"""Read-side queries over the structure index (used by the CLI and the site builder)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .index import connect, meta_get


def status(repo: Path) -> dict:
    db = connect(repo)
    files = db.execute("select kind, lang, tier from files").fetchall()
    langs = Counter((r["lang"], r["tier"]) for r in files if r["kind"] in ("source", "test"))
    stats = json.loads(meta_get(db, "stats", "{}") or "{}")
    out = {
        "repo": str(repo),
        "scanned_at": meta_get(db, "scanned_at"),
        "commit": meta_get(db, "commit"),
        "files": dict(Counter(r["kind"] for r in files)),
        "languages": [{"lang": l, "tier": t, "files": n} for (l, t), n in langs.most_common()],
        "symbols": dict(db.execute("select kind, count(*) from symbols group by kind order by 2 desc").fetchall()),
        "edges": dict(db.execute("select type, count(*) from edges group by type order by 2 desc").fetchall()),
        "resolution": {
            "imports": f"{stats.get('imports_resolved', 0)}/{stats.get('imports', 0)}",
            "refs": f"{stats.get('refs_resolved', 0)}/{stats.get('refs', 0)}",
        },
        "externals": [r[0] for r in db.execute(
            "select pkg, count(*) n from externals group by pkg order by n desc limit 25")],
    }
    db.close()
    return out


def _edges(db, col: str, ids: list[str], limit: int):
    other = "dst" if col == "src" else "src"
    q = (f"select {other} as node, type, conf, via, count from edges where {col} in ({','.join('?' * len(ids))}) "
         f"order by count desc, conf desc limit ?")
    return [dict(r) for r in db.execute(q, (*ids, limit))]


def show(repo: Path, target: str, code: bool = False, limit: int = 40) -> dict:
    db = connect(repo)
    alias = db.execute("select new from aliases where old = ?", (target,)).fetchone()
    if alias:
        target = alias["new"]
    f = db.execute("select * from files where path = ?", (target,)).fetchone()
    s = db.execute("select * from symbols where id = ?", (target,)).fetchone()
    if not f and not s:
        hits = db.execute("select id, kind, sig from symbols where name = ? or id like ? limit 20",
                          (target, f"%{target}%")).fetchall()
        files = db.execute("select path, kind from files where path like ? limit 20", (f"%{target}%",)).fetchall()
        db.close()
        return {"ok": False, "error": "not found", "candidates": [dict(h) for h in hits] + [dict(x) for x in files]}
    out: dict = {"ok": True}
    if f:
        out["file"] = dict(f)
        syms = db.execute('select id, qual, kind, start, "end", sig, doc from symbols where path = ? order by start',
                          (target,)).fetchall()
        out["symbols"] = [dict(r) for r in syms]
        ids = [target] + [r["id"] for r in syms]
        out["externals"] = [r[0] for r in db.execute("select pkg from externals where path = ?", (target,))]
    else:
        out["symbol"] = dict(s)
        ids = [target] + [r[0] for r in db.execute("select id from symbols where id like ?", (target + ".%",))]
        if code:
            lines = (repo / s["path"]).read_text("utf-8", "replace").splitlines()
            out["code"] = "\n".join(lines[s["start"] - 1: s["end"]])
    out["outgoing"] = _edges(db, "src", ids, limit)
    out["incoming"] = _edges(db, "dst", ids, limit)
    db.close()
    return out
