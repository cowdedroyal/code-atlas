"""Rebuild static data when semantic files change; polling needs no dependency."""
from __future__ import annotations

import threading
import time
from pathlib import Path


def semantic_stamp(repo: Path):
    root = repo / ".codemap"
    return tuple(sorted((str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size)
                        for p in root.rglob("*") if p.is_file() and "cache" not in p.relative_to(root).parts))


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
