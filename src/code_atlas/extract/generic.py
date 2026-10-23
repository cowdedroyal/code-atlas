"""Tier-2 extractor for any grammar in tree-sitter-language-pack.

Definitions come from the pack's `process()` structure; calls come from the grammar's tags.scm
(`@reference.call` / `@reference.class`). Imports are recovered from the statement text.
"""

from __future__ import annotations

import re
from functools import lru_cache

from . import body_hash, empty_facts, one_line
from .ts import language_for, parser_for, text_of

_KIND = {"class": "class", "struct": "class", "trait": "interface", "interface": "interface", "enum": "enum",
         "impl": "class", "module": "namespace", "namespace": "namespace", "function": "function",
         "method": "method"}
_SYM_KIND = {"class": "class", "interface": "interface", "enum": "enum", "type": "type", "constant": "constant",
             "function": "function", "module": "namespace"}
_COMMENT = r"//[^\n]*|/\*.*?\*/|#[^\n]*"
_QUOTED = re.compile(r"""["'<]([^"'<>]+)["'>]""")
_PATHISH = re.compile(r"(?:import|use|from|include|require|using|load|open|extern\s+crate)\s+(?:static\s+)?([\w.:/\\*{}, ]+)")
_DOTTED = re.compile(r"^[A-Za-z_][\w.:]*$")


@lru_cache(maxsize=None)
def _tags_query(lang: str):
    import tree_sitter_language_pack as tslp
    from tree_sitter import Query
    q = tslp.get_tags_query(lang)
    if not q:
        return None
    try:
        return Query(language_for(lang), q)
    except Exception:
        return None


def extract_generic(text: str, lang: str) -> dict:
    import tree_sitter_language_pack as tslp
    from tree_sitter_language_pack.options import ProcessConfig

    facts = empty_facts()
    try:
        r = tslp.process(text, ProcessConfig(language=lang, structure=True, imports=True, symbols=True,
                                             parse_timeout_ms=5000))
    except Exception:
        return facts
    lines = text.splitlines()
    quals: set[str] = set()

    def add(item, owner):
        name = item.name or "anonymous"
        q = f"{owner}.{name}" if owner else name
        base, n = q, 2
        while q in quals:
            q, n = f"{base}~{n}", n + 1
        quals.add(q)
        kind = _KIND.get(str(item.kind).lower(), "function")
        if kind == "function" and owner:
            kind = "method"
        sp = item.span
        body = "\n".join(lines[sp.start_line: sp.end_line + 1])
        facts["symbols"].append({
            "q": q, "n": name, "k": kind, "s": sp.start_line + 1, "e": sp.end_line + 1,
            "sig": one_line(item.signature), "p": owner or None, "h": body_hash(body, _COMMENT),
            "doc": one_line((item.doc_comment or "").strip().splitlines()[0] if item.doc_comment else None),
            "x": (item.visibility or "public") not in ("private", "protected"),
        })
        for ch in item.children:
            add(ch, q)

    for it in r.structure:
        add(it, "")
    have = {s["n"] for s in facts["symbols"]}
    for s in r.symbols:  # types/constants the structure pass may miss (e.g. Go structs)
        if s.name in have or s.span is None:
            continue
        kind = _SYM_KIND.get(str(s.kind).lower())
        if not kind:
            continue
        have.add(s.name)
        quals.add(s.name)
        body = "\n".join(lines[s.span.start_line: s.span.end_line + 1])
        facts["symbols"].append({"q": s.name, "n": s.name, "k": kind, "s": s.span.start_line + 1,
                                 "e": s.span.end_line + 1, "sig": one_line(lines[s.span.start_line] if s.span.start_line < len(lines) else None),
                                 "p": None, "h": body_hash(body, _COMMENT), "doc": None, "x": True})

    for imp in r.imports:
        spec = _import_spec(imp.source)
        if spec:
            facts["imports"].append({"m": spec, "names": [[i, i] for i in imp.items], "star": imp.is_wildcard,
                                     "alias": imp.alias, "line": (imp.span.start_line + 1) if imp.span else 0,
                                     "re": False})

    _attach_methods(facts["symbols"])
    facts["refs"] = _tag_refs(text, lang, facts["symbols"])
    if re.search(r"^\s*(func|fn)\s+main\s*\(|public\s+static\s+void\s+main\s*\(|^#!", text, re.M):
        facts["entry"] = True
    return facts


_GO_RECV = re.compile(r"^func\s*\(\s*\w*\s*\*?\s*([A-Za-z_]\w*)")


def _attach_methods(symbols: list[dict]):
    """Put Go receiver methods and Rust/Swift `impl X` / `extension X` members under the type X itself."""
    types: dict[str, str] = {}
    for s in symbols:  # first declaration wins: the struct, not its impl block
        if s["p"] is None and s["k"] in ("class", "interface", "type", "enum"):
            types.setdefault(s["n"], s["q"])

    def move(s, parent):
        old = s["q"]
        s["p"], s["q"] = parent, f"{parent}.{s['n']}"
        if s["k"] == "function":
            s["k"] = "method"
        for c in symbols:
            if c["p"] == old:
                move(c, s["q"])

    drop = []
    for s in symbols:
        if s["p"] is None and s["k"] in ("function", "method") and s["sig"]:
            m = _GO_RECV.match(s["sig"])
            if m and m.group(1) in types:
                move(s, types[m.group(1)])
        elif s["p"] is None and s["q"] != types.get(s["n"], s["q"]) and s["n"] in types:
            target = types[s["n"]]
            for c in symbols:
                if c["p"] == s["q"]:
                    move(c, target)
            drop.append(s)
    for s in drop:
        symbols.remove(s)
    seen: set[str] = set()
    for s in symbols:  # moving may create duplicates
        base, n = s["q"], 2
        while s["q"] in seen:
            s["q"], n = f"{base}~{n}", n + 1
        seen.add(s["q"])


def _import_spec(stmt: str) -> str | None:
    stmt = stmt.strip()
    m = _QUOTED.search(stmt)
    if m:
        return m.group(1)
    m = _PATHISH.search(stmt)
    if m:
        spec = m.group(1).strip().rstrip(";").split(" as ")[0].strip()
        spec = re.sub(r"::\{.*$|\.\*$|::\*$", "", spec)
        return spec or None
    return None


def _tag_refs(text: str, lang: str, symbols: list[dict]) -> list[dict]:
    from tree_sitter import QueryCursor
    q = _tags_query(lang)
    if q is None:
        return []
    src = text.encode("utf-8", "surrogatepass")
    tree = parser_for(lang).parse(src)
    spans = sorted(((s["s"], s["e"], s["q"]) for s in symbols), key=lambda x: (x[0], -x[1]))
    out = []
    for _, caps in QueryCursor(q).matches(tree.root_node):
        ref_kind = next((k for k in caps if k.startswith("reference.")), None)
        if ref_kind is None or "name" not in caps:
            continue
        ref_node = caps[ref_kind][0]
        name_node = caps["name"][0]
        head = text_of(ref_node, src).split("(")[0].replace(" ", "").replace("\n", "")
        name = head if _DOTTED.match(head) and head.endswith(text_of(name_node, src)) else text_of(name_node, src)
        line = ref_node.start_point[0] + 1
        owner = ""
        for s, e, qual in spans:  # innermost enclosing symbol
            if s <= line <= e:
                owner = qual
        kind = {"reference.call": "call", "reference.class": "new", "reference.implementation": "implements",
                "reference.type": "type"}.get(ref_kind, "ref")
        out.append({"f": owner, "n": name.replace("::", "."), "k": kind, "line": line})
    return out
