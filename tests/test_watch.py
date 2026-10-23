import threading
from code_atlas.watch import watch_semantics, semantic_stamp


def test_semantic_watcher_rebuilds_after_settled_changes_ignores_cache(tmp_path):
    root = tmp_path / ".codemap"
    root.mkdir()
    (root / "cache").mkdir()
    initial = semantic_stamp(tmp_path)
    (root / "cache" / "index").write_text("cache")
    assert semantic_stamp(tmp_path) == initial
    stop, updated = threading.Event(), threading.Event()
    thread = threading.Thread(target=watch_semantics, args=(tmp_path, updated.set, stop, lambda _: None, .02))
    thread.start()
    try:
        import time
        time.sleep(.05)
        (root / "notes.jsonl").write_text("changed")
        assert updated.wait(2)
    finally:
        stop.set()
        thread.join(2)
