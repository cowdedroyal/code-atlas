"""Rebuild static data when semantic files change; polling needs no dependency."""
from __future__ import annotations

import threading
from pathlib import Path


def semantic_stamp(repo: Path):
    root = repo / ".codemap"
    result = []
    for p in root.rglob("*"):
        if "cache" in p.relative_to(root).parts:
            continue
        try:
            if p.is_file():
                st = p.stat()
                result.append((str(p.relative_to(root)), st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            continue  # apply may remove a semantic shard while the watcher scans
    return tuple(sorted(result))


def watch_semantics(repo: Path, rebuild, stop: threading.Event, report, interval=0.5):
    previous = semantic_stamp(repo)
    while not stop.wait(interval):
        current = semantic_stamp(repo)
        if current == previous:
            continue
        # Wait for apply's multiple writes to settle before building a snapshot.
        if stop.wait(interval):
            return
        settled = semantic_stamp(repo)
        if settled != current:
            continue
        try:
            rebuild()
            previous = settled
            report("Semantic map updated; refresh the page.")
        except Exception as e:
            previous = settled
            report(f"Map rebuild failed: {e}")
