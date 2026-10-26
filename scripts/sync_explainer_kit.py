"""Copy the explainer kit (manifest schema, component kit, lint/pack) from the atlas-explainer repository.

    uv run python scripts/sync_explainer_kit.py            # copy
    uv run python scripts/sync_explainer_kit.py --check    # exit 1 if the vendored copy has drifted

The copy lives in src/code_atlas/explainer_kit/ and is committed, so Code Atlas builds and tests without the other
repository. Never edit it by hand; change atlas-explainer and sync. The checkout is found next to this one
(../atlas-explainer) or through $ATLAS_EXPLAINER_DIR.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "src" / "code_atlas" / "explainer_kit"
FILES = ["__init__.py", "pack.py", "card.schema.json", "kit/v1.css", "kit/v1.js"]


def source_dir() -> Path:
    src = Path(os.environ.get("ATLAS_EXPLAINER_DIR") or ROOT.parent / "atlas-explainer") / "src" / "atlas_explainer"
    if not src.is_dir():
        sys.exit(f"atlas-explainer not found at {src}; set ATLAS_EXPLAINER_DIR")
    return src


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    src = source_dir()
    drifted = []
    for rel in FILES:
        a, b = src / rel, DEST / rel
        same = b.exists() and a.read_bytes() == b.read_bytes()
        if not same:
            drifted.append(rel)
            if not args.check:
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(a, b)
    if args.check:
        if drifted:
            print("vendored explainer kit differs from atlas-explainer: " + ", ".join(drifted), file=sys.stderr)
            return 1
        return 0
    print("updated: " + (", ".join(drifted) or "nothing (already in sync)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
