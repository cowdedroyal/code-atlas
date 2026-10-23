"""Syntax tokens for languages absent from the viewer's lazy grammar catalog.

Reuse the scanner's tree-sitter grammar and bundled highlights query. A grammar
without a highlights query still gets strings, comments, numbers and keywords
from AST leaves. Offsets remain UTF-8 byte offsets until source is decoded.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

MAX_SOURCE_CHARS = 100_000


@lru_cache(maxsize=1)
def _catalog():
    return json.loads(Path(__file__).with_name("highlight_languages.json").read_text("utf-8"))


def has_browser_grammar(lang: str | None) -> bool:
    if not lang:
        return False
    catalog = _catalog()
    return catalog["aliases"].get(lang, lang) in catalog["names"]


def _style(capture: str) -> tuple[str, int] | None:
    group = capture.split(".")[0]
    classes = {"comment": ("comment", 100), "string": ("string", 90), "character": ("string", 90),
               "number": ("number", 70), "float": ("number", 70), "boolean": ("literal", 70),
               "keyword": ("keyword", 60), "conditional": ("keyword", 60), "repeat": ("keyword", 60),
               "exception": ("keyword", 60), "include": ("keyword", 60), "type": ("type", 50),
               "constructor": ("type", 50), "function": ("title", 40), "method": ("title", 40),
               "property": ("attr", 30), "tag": ("name", 30), "attribute": ("attr", 30),
               "constant": ("literal", 20), "label": ("symbol", 20)}
    if group in classes:
        name, priority = classes[group]
        return "hljs-" + name, priority
    return None


def source_tokens(text: str, lang: str) -> list[list[dict]]:
    from .extract.ts import language_for, parser_for
    from tree_sitter import Query, QueryCursor
    import tree_sitter_language_pack as pack

    text = text[:MAX_SOURCE_CHARS]
    src = text.encode("utf-8", "replace")
    tree = parser_for(lang).parse(src)
    ranges: list[tuple[int, int, str, int]] = []
    query = pack.get_highlights_query(lang)
    if query:
        try:
            captures = QueryCursor(Query(language_for(lang), query)).captures(tree.root_node)
            for capture, nodes in captures.items():
                style = _style(capture)
                if style:
                    ranges.extend((n.start_byte, n.end_byte, *style) for n in nodes if n.end_byte > n.start_byte)
        except Exception:
            pass  # an outdated upstream query must not break a static build

    # Fill semantic categories even if this grammar has no bundled query.
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        kind = node.type.lower()
        group = None
        if "comment" in kind:
            group = "comment"
        elif any(s in kind for s in ("string", "quoted", "char_literal", "character_literal")):
            group = "string"
        elif any(s in kind for s in ("number", "integer", "float", "decimal", "numeric")):
            group = "number"
        elif node.child_count == 0 and not node.is_named:
            value = src[node.start_byte:node.end_byte].decode("utf-8", "replace")
            if re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", value):
                group = "keyword"
        if group:
            ranges.append((node.start_byte, node.end_byte, *_style(group)))
        else:
            stack.extend(reversed(node.children))

    # Sweep overlapping scopes once, choosing broad comment/string scopes ahead
    # of identifiers inside them. Every source byte is emitted exactly once.
    events: dict[int, list[tuple[bool, int]]] = {}
    for i, (start, end, _, _) in enumerate(ranges):
        events.setdefault(start, []).append((True, i))
        events.setdefault(end, []).append((False, i))
    active: set[int] = set()
    rows: list[list[dict]] = [[]]
    points = sorted({0, len(src), *events})
    for j, start in enumerate(points[:-1]):
        for add, i in events.get(start, []):
            (active.add if add else active.discard)(i)
        end = points[j + 1]
        style = ranges[max(active, key=lambda i: (ranges[i][3], ranges[i][0], i))][2] if active else None
        for k, part in enumerate(src[start:end].decode("utf-8", "replace").split("\n")):
            if k:
                rows.append([])
            if part:
                token = {"text": part, **({"className": style} if style else {})}
                if rows[-1] and rows[-1][-1].get("className") == style:
                    rows[-1][-1]["text"] += part
                else:
                    rows[-1].append(token)
    return rows
