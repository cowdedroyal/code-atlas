"""Outlines for documents and config: headings, top-level keys, tables, build stages and targets.

These files have no calls, but putting their structure on the map lets a reader see that, say,
`docker-compose.yml` defines four services, and that a README section links to the code it describes.
"""

from __future__ import annotations

import json
import re

from . import body_hash, empty_facts, one_line

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_FENCE = re.compile(r"^\s*(```|~~~)")
_MD_LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
_YAML_KEY = re.compile(r"^([A-Za-z_][\w.\-/]*|\"[^\"]+\"|'[^']+')\s*:(\s|$)")
_TOML_TABLE = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*$")
_TOML_KEY = re.compile(r"^([A-Za-z_][\w\-]*)\s*=")
_SQL = re.compile(r"create\s+(?:or\s+replace\s+)?(?:temp(?:orary)?\s+)?(table|view|materialized\s+view|function|procedure|index|type|trigger)\s+(?:if\s+not\s+exists\s+)?([\w.\"`\[\]]+)", re.I)
_DOCKER_FROM = re.compile(r"^\s*FROM\s+(\S+)(?:\s+AS\s+(\S+))?", re.I)
_MAKE_TARGET = re.compile(r"^([A-Za-z0-9_.\-/]+)\s*:(?!=)")
# Second-level keys worth showing for well-known files (compose services, CI jobs, npm scripts…).
_EXPAND = {"services", "jobs", "scripts", "workflows", "stages", "paths", "components", "definitions", "volumes"}


def extract_outline(path: str, fmt: str | None, text: str) -> dict:
    facts = empty_facts()
    lines = text.splitlines()
    fn = {"markdown": _markdown, "yaml": _yaml, "toml": _toml, "json": _json, "sql": _sql,
          "dockerfile": _dockerfile, "makefile": _makefile}.get(fmt or "")
    if fn:
        fn(lines, facts)
    _finish(lines, facts)
    return facts


def _sym(facts, name, kind, line, parent=None, sig=None):
    q = f"{parent}/{name}" if parent else name
    base, n = q, 2
    existing = {s["q"] for s in facts["symbols"]}
    while q in existing:
        q, n = f"{base}~{n}", n + 1
    facts["symbols"].append({"q": q, "n": name, "k": kind, "s": line, "e": line, "sig": one_line(sig, 120),
                             "p": parent, "h": "", "doc": None, "x": True})
    return q


def _finish(lines, facts):
    """End line of each symbol = line before the next sibling-or-ancestor starts; hash that span."""
    syms = facts["symbols"]
    depth = {}
    for s in syms:
        depth[s["q"]] = depth.get(s["p"], -1) + 1 if s["p"] else 0
    for i, s in enumerate(syms):
        end = len(lines)
        for t in syms[i + 1:]:
            if depth[t["q"]] <= depth[s["q"]]:
                end = t["s"] - 1
                break
        s["e"] = max(s["s"], end)
        s["h"] = body_hash("\n".join(lines[s["s"] - 1: s["e"]]))


def _markdown(lines, facts):
    stack: list[tuple[int, str]] = []
    fence = False
    for i, line in enumerate(lines, 1):
        if _MD_FENCE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        m = _MD_HEADING.match(line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            q = _sym(facts, title.replace("/", "∕"), "section", i, stack[-1][1] if stack else None, line)
            stack.append((level, q))
        for link in _MD_LINK.findall(line):
            if not re.match(r"^[a-z]+:", link):
                facts["links"].append({"m": link, "line": i})


def _yaml(lines, facts):
    top = None
    child_indent = None
    for i, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#") or line.startswith("---"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        m = _YAML_KEY.match(line.strip())
        if indent == 0:
            if m:
                name = m.group(1).strip("\"'")
                top = _sym(facts, name, "key", i, None, line)
                top_name = name
                child_indent = None
            else:
                top = None
            continue
        if top and m and top_name in _EXPAND:
            if child_indent is None:
                child_indent = indent
            if indent == child_indent:
                _sym(facts, m.group(1).strip("\"'"), "key", i, top, line)


def _toml(lines, facts):
    table = None
    for i, line in enumerate(lines, 1):
        m = _TOML_TABLE.match(line)
        if m:
            table = _sym(facts, m.group(1).strip(), "table", i, None, line)
            continue
        if table is None:
            m = _TOML_KEY.match(line)
            if m:
                _sym(facts, m.group(1), "key", i, None, line)


def _json(lines, facts):
    text = "\n".join(lines)
    try:
        data = json.loads(text)
    except ValueError:
        try:  # jsonc / tsconfig: strip comments and trailing commas
            data = json.loads(re.sub(r",(\s*[}\]])", r"\1", re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)))
        except ValueError:
            return
    if not isinstance(data, dict):
        return
    cursor = 0

    def find(key, start):
        pat = re.compile(r'^\s*"' + re.escape(key) + r'"\s*:')
        for j in range(start, len(lines)):
            if pat.match(lines[j]):
                return j
        return None

    for key, value in data.items():
        j = find(key, cursor)
        if j is None:
            continue
        cursor = j + 1
        q = _sym(facts, key, "key", j + 1, None, lines[j])
        if key in _EXPAND and isinstance(value, dict) and len(value) <= 60:
            sub = cursor
            for k2 in value:
                j2 = find(k2, sub)
                if j2 is not None:
                    sub = j2 + 1
                    _sym(facts, k2, "key", j2 + 1, q, lines[j2])


def _sql(lines, facts):
    for i, line in enumerate(lines, 1):
        for m in _SQL.finditer(line):
            kind = m.group(1).lower().split()[-1]
            _sym(facts, m.group(2).strip('"`[]'), {"view": "view", "table": "table"}.get(kind, kind), i, None, line)


def _dockerfile(lines, facts):
    for i, line in enumerate(lines, 1):
        m = _DOCKER_FROM.match(line)
        if m:
            _sym(facts, m.group(2) or m.group(1), "stage", i, None, line)
        if re.match(r"^\s*(ENTRYPOINT|CMD)\b", line, re.I):
            facts["entry"] = True


def _makefile(lines, facts):
    for i, line in enumerate(lines, 1):
        m = _MAKE_TARGET.match(line)
        if m and not m.group(1).startswith("."):
            _sym(facts, m.group(1), "target", i, None, line)
