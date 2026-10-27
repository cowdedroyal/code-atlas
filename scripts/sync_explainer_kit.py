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
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "src" / "code_atlas" / "explainer_kit"
FILES = ["__init__.py", "pack.py", "media.py", "card.schema.json", "kit/v1.css", "kit/v1.js",
         "media/vendor/vega.min.js", "media/vendor/vega-interpreter.min.js", "media/vendor/README.md"]


def source_dir() -> Path:
    candidates = [Path(os.environ["ATLAS_EXPLAINER_DIR"])] if os.environ.get("ATLAS_EXPLAINER_DIR") else [
        ROOT / "extensions" / "atlas-explainer", ROOT.parent / "atlas-explainer"]
    for base in candidates:
        if (base / "src" / "atlas_explainer").is_dir():
            return base / "src" / "atlas_explainer"
    sys.exit("atlas-explainer not found (tried " + ", ".join(map(str, candidates)) + "); run `git submodule update --init` or set ATLAS_EXPLAINER_DIR")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    src = source_dir()
    drifted = []
    for rel in FILES:
        a, b = src / rel, DEST / rel
        # Compare with LF endings: a checkout with core.autocrlf=true (Windows) has CRLF in the working tree.
        want = a.read_bytes().replace(b"\r\n", b"\n")
        if not (b.exists() and b.read_bytes().replace(b"\r\n", b"\n") == want):
            drifted.append(rel)
            if not args.check:
                b.parent.mkdir(parents=True, exist_ok=True)
                b.write_bytes(want)
    if args.check:
        if drifted:
            print("vendored explainer kit differs from atlas-explainer: " + ", ".join(drifted), file=sys.stderr)
            return 1
        return 0
    print("updated: " + (", ".join(drifted) or "nothing (already in sync)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
