"""Copy the explainer kit (manifest schema, component kit, lint/pack) from the atlas-explainer repository.

    uv run python scripts/sync_explainer_kit.py            # copy
    uv run python scripts/sync_explainer_kit.py --check    # exit 1 if the vendored copy has drifted

The copy lives in src/code_atlas/explainer_kit/ and is committed, so Code Atlas builds and tests without the other
repository. Never edit it by hand; change atlas-explainer and sync. The source is the git submodule
extensions/atlas-explainer (`git submodule update --init`), or $ATLAS_EXPLAINER_DIR, or a checkout next to this one.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "src" / "code_atlas" / "explainer_kit"
FILES = ["__init__.py", "pack.py", "media.py", "card.schema.json", "kit/v1.css", "kit/v1.js",
         "media/vendor/vega.min.js", "media/vendor/vega-interpreter.min.js", "media/vendor/README.md",
         "media/vendor/LICENSE-vega.txt", "media/vendor/LICENSE-vega-interpreter.txt"]
# Directories copied wholesale: anything new upstream here must exist here too, or a build that reads it fails.
# The package root is not one of them — it also holds cli.py and __main__.py, which are deliberately not vendored.
COPIED_DIRS = sorted({str(Path(rel).parent.as_posix()) for rel in FILES} - {"."})
TEXT = {".py", ".js", ".css", ".json", ".md", ".txt"}


def source_dir() -> Path:
    candidates = [Path(os.environ["ATLAS_EXPLAINER_DIR"])] if os.environ.get("ATLAS_EXPLAINER_DIR") else [
        ROOT / "extensions" / "atlas-explainer", ROOT.parent / "atlas-explainer"]
    for base in candidates:
        if (base / "src" / "atlas_explainer").is_dir():
            return base / "src" / "atlas_explainer"
    sys.exit("atlas-explainer not found (tried " + ", ".join(map(str, candidates)) + "); run `git submodule update --init` or set ATLAS_EXPLAINER_DIR")


def _norm(rel: str, data: bytes) -> bytes:
    """Compare with LF endings: a checkout with core.autocrlf=true (Windows) has CRLF in the working tree.
    Binary files are never rewritten — PNG and friends may legitimately contain CRLF bytes."""
    return data.replace(b"\r\n", b"\n") if Path(rel).suffix.lower() in TEXT else data


def _tree(base: Path) -> set[str]:
    """Files under the wholesale-copied directories, relative to the package root."""
    out = set()
    for rel in COPIED_DIRS:
        d = base / rel
        if d.is_dir():
            out |= {p.relative_to(base).as_posix() for p in d.rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    src = source_dir()
    wanted = set(FILES) | _tree(src)  # FILES is hand-picked; the copied directories are mirrored
    changed = [rel for rel in sorted(wanted) if not (src / rel).is_file()
               or not ((DEST / rel).exists() and _norm(rel, (DEST / rel).read_bytes()) == _norm(rel, (src / rel).read_bytes()))]
    extra = sorted(_tree(DEST) - wanted)  # vendored files upstream no longer has (or never had)
    if args.check:
        if changed or extra:
            parts = []
            if changed:
                parts.append("changed or missing upstream: " + ", ".join(changed))
            if extra:
                parts.append("not upstream: " + ", ".join(extra))
            print("vendored explainer kit is not in sync with atlas-explainer (" + "; ".join(parts) + ")", file=sys.stderr)
            return 1
        return 0
    for rel in changed:
        b = DEST / rel
        if (src / rel).is_file():
            b.parent.mkdir(parents=True, exist_ok=True)
            b.write_bytes(_norm(rel, (src / rel).read_bytes()))
        else:
            b.unlink(missing_ok=True)
    for rel in extra:
        (DEST / rel).unlink(missing_ok=True)
    updated = [f"updated: {rel}" for rel in changed if (src / rel).is_file()]
    removed = [f"removed: {rel}" for rel in changed + extra if not (src / rel).is_file()]
    print("\n".join(updated + removed) or "updated: nothing (already in sync)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
