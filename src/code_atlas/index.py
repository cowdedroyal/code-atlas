"""The structure index: a rebuildable SQLite cache under .codemap/cache/."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
create table if not exists meta (k text primary key, v text);
create table if not exists files (
  path text primary key, kind text, lang text, tier text, size integer, lines integer,
  hash text, entry integer default 0, doc text, mtime integer);
create table if not exists symbols (
  id text primary key, path text, qual text, name text, kind text, start integer, "end" integer,
  sig text, parent text, hash text, doc text, exported integer);
create index if not exists symbols_path on symbols(path);
create index if not exists symbols_name on symbols(name);
create table if not exists edges (src text, dst text, type text, conf real, via text, count integer);
create index if not exists edges_src on edges(src);
create index if not exists edges_dst on edges(dst);
create table if not exists externals (path text, pkg text);
create table if not exists facts_cache (path text primary key, hash text, version integer, json text);
create table if not exists aliases (old text primary key, new text, scan text);
"""


def cache_dir(repo: Path) -> Path:
    return repo / ".codemap" / "cache"


def connect(repo: Path) -> sqlite3.Connection:
    d = cache_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    gi = repo / ".codemap" / ".gitignore"
    if not gi.exists():
        gi.write_text("cache/\n", encoding="utf-8")
    db = sqlite3.connect(d / "index.sqlite")
    db.row_factory = sqlite3.Row
    db.execute("pragma journal_mode = wal")
    db.execute("pragma synchronous = normal")
    db.executescript(SCHEMA)
    cols = {r[1] for r in db.execute("pragma table_info(files)")}
    for col, decl in (("doc", "text"), ("mtime", "integer")):  # index written by an older version
        if col not in cols:
            db.execute(f"alter table files add column {col} {decl}")
    if "anchor_hash" not in cols:
        db.execute("alter table files add column anchor_hash text")
    return db


def meta_get(db, k: str, default=None):
    row = db.execute("select v from meta where k = ?", (k,)).fetchone()
    return row["v"] if row else default


def meta_set(db, k: str, v) -> None:
    db.execute("insert into meta (k, v) values (?, ?) on conflict(k) do update set v = excluded.v", (k, str(v)))
