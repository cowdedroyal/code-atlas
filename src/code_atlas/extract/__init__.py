"""Per-file extraction: source text in, JSON-serialisable facts out.

Facts are cached by content hash, so this layer must be a pure function of (path, text).

    {"symbols": [{"q": qualified name, "n": name, "k": kind, "s": start line, "e": end line,
                  "sig": signature, "p": parent qual | None, "h": body hash, "doc": first doc line,
                  "x": exported}],
     "imports": [{"m": module spec, "names": [[imported, local]], "star": bool, "alias": local | None,
                  "line": n, "re": re-export}],
     "refs":    [{"f": enclosing qual ("" = module level), "n": callee/name text, "k": kind, "line": n}],
     "links":   [{"m": relative path, "line": n}],      # docs → files
     "entry":   bool,                                    # runnable entry point (main guard, bin script…)
     "doc":     first line of the module docstring / file header comment, or None,
     "syspath": [dir relative to this file's directory]}   # Python sys.path.insert/append we could evaluate

Lines are 1-based. Ref kinds: call | new | inherit | implements | decorator | jsx | type | ref.
"""

from __future__ import annotations

import hashlib
import re

EXTRACTOR_VERSION = 6

_WS = re.compile(r"\s+")


def body_hash(text: str, comment: str | None = None) -> str:
    if comment:
        text = re.sub(comment, "", text)
    return hashlib.sha1(_WS.sub(" ", text).strip().encode("utf-8", "surrogatepass")).hexdigest()[:16]


def one_line(text: str | None, limit: int = 160) -> str | None:
    if not text:
        return None
    text = _WS.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def empty_facts() -> dict:
    return {"symbols": [], "imports": [], "refs": [], "links": [], "entry": False, "doc": None}


def extract(path: str, lang: str | None, tier: str, text: str) -> dict:
    if tier == "1":
        if lang == "python":
            from .python import extract_python
            return extract_python(text)
        if lang in ("vue", "svelte"):
            from .ecmascript import extract_sfc
            return extract_sfc(text)
        if lang in ("go", "java", "rust", "csharp", "c", "cpp"):
            from .spec import extract_spec
            return extract_spec(text, lang)
        from .ecmascript import extract_ecmascript
        return extract_ecmascript(text, lang)
    if tier == "2":
        from .generic import extract_generic
        return extract_generic(text, lang)
    if tier == "outline":
        from .outline import extract_outline
        return extract_outline(path, lang, text)
    return empty_facts()
