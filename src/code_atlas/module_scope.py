"""Symbol endpoints for execution outside named declarations in the L2 viewer.

The scanner's original ownership and L1 aggregation remain intact. A module
scope is a derived symbol including anonymous callbacks, not a declaration
enclosing the file's other symbols.
"""
from __future__ import annotations

EXECUTION_EDGES = {"calls", "instantiates", "references"}


def declaration_folds(text: str, lang: str, symbols: list[dict]) -> list[dict]:
    """Fold declaration bodies, preserving decorators, signatures and closing braces.

    Use the same grammar as the scanner. Only fold complete interior lines so
    default arguments, base classes, and executable code on a shared line remain
    visible. Anonymous callbacks absent from the symbol list remain visible too.
    """
    from .extract.ts import parser_for
    if lang in ("vue", "svelte"):
        import re
        from .extract.ecmascript import _SCRIPT
        result = []
        for match in _SCRIPT.finditer(text):
            attrs, source = match.group(1), match.group(2)
            offset = text.count("\n", 0, match.start(2))
            script_lang = "typescript" if re.search(r'lang\s*=\s*["\']ts', attrs) else "javascript"
            shifted = [{**s, "start": s["start"] - offset, "end": s["end"] - offset} for s in symbols]
            for fold in declaration_folds(source, script_lang, shifted):
                result.append({**fold, "from": fold["from"] + offset, "to": fold["to"] + offset})
        return result
    declarations = [s for s in symbols if s["kind"] in
                    {"function", "method", "class", "interface", "namespace", "type", "enum"}]
    spans = {(s["start"], s["end"]): s for s in sorted(declarations, key=lambda s: s["id"], reverse=True)}
    tree = parser_for(lang).parse(text.encode("utf-8", "replace"))
    stack = [tree.root_node]
    folds = {}
    while stack:
        node = stack.pop()
        span = (node.start_point[0] + 1, node.end_point[0] + 1)
        symbol = spans.get(span)
        if symbol:
            body = node.child_by_field_name("body")
            if body is None:  # const renderer = (...) => { ... }
                candidates = list(node.named_children)
                while candidates:
                    child = candidates.pop()
                    if child.type in ("arrow_function", "function_expression", "function", "class"):
                        body = child.child_by_field_name("body")
                        break
                    if child.type in ("variable_declarator", "parenthesized_expression", "as_expression", "call_expression", "arguments"):
                        candidates.extend(child.named_children)
            if body is not None:
                first = max(span[0] + 1, body.start_point[0] + (1 if lang == "python" else 2))
                last = body.end_point[0] + (1 if lang == "python" else 0)
                if first <= last:
                    folds[(first, last)] = {"from": first, "to": last, "name": symbol["name"], "id": symbol["id"]}
        stack.extend(reversed(node.named_children))
    result = []
    for (first, last), fold in sorted(folds.items(), key=lambda x: (x[0][0], -x[0][1])):
        if not any(f["from"] <= first and last <= f["to"] for f in result):
            result.append(fold)
    return result


def execution_symbols(files: dict, symbols: dict, edges: list[dict]) -> tuple[list[dict], list[dict], dict[str, str]]:
    def eligible(edge):
        return edge["type"] in EXECUTION_EDGES and edge.get("via") != "link"

    needed = {node for edge in edges if eligible(edge) for node in (edge["src"], edge["dst"])
              if node in files and files[node].get("tier") in ("1", "2")
              and files[node].get("kind") in ("source", "test")}
    ids = set(symbols)
    scopes = {}
    result = list(symbols.values())
    for path in sorted(needed):
        qual = "@module"
        n = 2
        while f"{path}#{qual}" in ids:
            qual = f"@module~{n}"
            n += 1
        sid = f"{path}#{qual}"
        scopes[path] = sid
        ids.add(sid)
        result.append({"id": sid, "path": path, "qual": qual, "name": "Module scope", "kind": "module_scope",
                       "parent": path, "start": 1, "end": max(1, files[path].get("lines") or 1),
                       "sig": None, "doc": None, "hash": "", "exported": False})
    projected = [{**edge, "src": scopes.get(edge["src"], edge["src"]),
                  "dst": scopes.get(edge["dst"], edge["dst"])} if eligible(edge) else dict(edge) for edge in edges]
    return result, projected, scopes
