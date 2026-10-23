"""Tier-1 JavaScript / TypeScript / TSX extractor (also the <script> block of Vue and Svelte files).

Symbols are module-level declarations and class members; functions nested inside function bodies
(event handlers, closures) are not separate symbols — their calls are attributed to the enclosing one.
"""

from __future__ import annotations

import re

from . import body_hash, empty_facts, one_line
from .ts import parser_for, text_of

_DOTTED = re.compile(r"^[A-Za-z_$][\w$.]*$")
_COMMENT = r"//[^\n]*|/\*.*?\*/"
_FUNC_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
_DECL_KIND = {
    "function_declaration": "function", "generator_function_declaration": "function",
    "class_declaration": "class", "abstract_class_declaration": "class", "class": "class",
    "interface_declaration": "interface", "type_alias_declaration": "type", "enum_declaration": "enum",
    "module": "namespace", "internal_module": "namespace",
}
_SKIP = {"string", "template_string", "comment", "regex", "number"}
_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)


def extract_ecmascript(text: str, lang: str) -> dict:
    src = text.encode("utf-8", "surrogatepass")
    tree = parser_for(lang).parse(src)
    w = _Walker(src)
    w.module(tree.root_node)
    w.facts["doc"] = _header_comment(text)
    if text.startswith("#!"):
        w.facts["entry"] = True
    return w.facts


def extract_sfc(text: str) -> dict:
    """Vue/Svelte: parse every <script> block with the JS/TS walker and shift line numbers."""
    facts = empty_facts()
    for m in _SCRIPT.finditer(text):
        attrs, body = m.group(1), m.group(2)
        lang = "typescript" if re.search(r'lang\s*=\s*["\']ts', attrs) else "javascript"
        offset = text.count("\n", 0, m.start(2))
        part = extract_ecmascript(body, lang)
        for s in part["symbols"]:
            s["s"] += offset
            s["e"] += offset
        for key in ("imports", "refs"):
            for r in part[key]:
                r["line"] += offset
        for key in ("symbols", "imports", "refs"):
            facts[key].extend(part[key])
    return facts


class _Walker:
    def __init__(self, src: bytes):
        self.src = src
        self.facts = empty_facts()
        self.quals: set[str] = set()
        self.exported_names: set[str] = set()

    def t(self, node) -> str:
        return text_of(node, self.src)

    # --- module level --------------------------------------------------------------------------
    def module(self, root):
        for ch in root.named_children:
            self.statement(ch, exported=False)
        for s in self.facts["symbols"]:
            if s["p"] is None and s["n"] in self.exported_names:
                s["x"] = True

    def statement(self, node, exported: bool):
        t = node.type
        if t == "import_statement":
            self.import_stmt(node)
        elif t == "export_statement":
            self.export_stmt(node)
        elif t in _DECL_KIND:
            self.declaration(node, "", exported)
        elif t in ("lexical_declaration", "variable_declaration"):
            self.variables(node, exported)
        elif t == "ambient_declaration":
            for ch in node.named_children:
                self.statement(ch, exported)
        elif t == "expression_statement":
            self.module_expression(node)
        else:
            self.refs(node, "")

    def import_stmt(self, node):
        src_node = node.child_by_field_name("source")
        spec = self.string(src_node)
        imp = {"m": spec, "names": [], "star": False, "alias": None, "line": node.start_point[0] + 1, "re": False}
        for clause in node.named_children:
            if clause.type != "import_clause":
                continue
            for c in clause.named_children:
                if c.type == "identifier":
                    imp["names"].append(["default", self.t(c)])
                elif c.type == "namespace_import":
                    ids = [x for x in c.named_children if x.type == "identifier"]
                    if ids:
                        imp["alias"] = self.t(ids[0])
                elif c.type == "named_imports":
                    for s in c.named_children:
                        if s.type == "import_specifier":
                            name = self.t(s.child_by_field_name("name"))
                            alias = s.child_by_field_name("alias")
                            imp["names"].append([name, self.t(alias) if alias is not None else name])
        self.facts["imports"].append(imp)

    def export_stmt(self, node):
        decl = node.child_by_field_name("declaration")
        src_node = node.child_by_field_name("source")
        line = node.start_point[0] + 1
        is_default = any(c.type == "default" for c in node.children)
        if decl is not None:
            if decl.type in ("lexical_declaration", "variable_declaration"):
                self.variables(decl, True)
            elif decl.type in _DECL_KIND:
                q = self.declaration(decl, "", True)
                if is_default:  # `export default function MapView` — what `import MapView from` binds to
                    self.facts["default"] = q
            else:
                self.statement(decl, True)
            return
        clause = next((c for c in node.named_children if c.type == "export_clause"), None)
        if src_node is not None:  # re-export
            names = []
            if clause is not None:
                for s in clause.named_children:
                    if s.type == "export_specifier":
                        name = self.t(s.child_by_field_name("name"))
                        alias = s.child_by_field_name("alias")
                        names.append([name, self.t(alias) if alias is not None else name])
            ns = next((c for c in node.named_children if c.type == "namespace_export"), None)
            self.facts["imports"].append({"m": self.string(src_node), "names": names,
                                          "star": clause is None and ns is None,
                                          "alias": self.t(ns.named_children[0]) if ns is not None and ns.named_children else None,
                                          "line": line, "re": True})
            return
        if clause is not None:
            for s in clause.named_children:
                if s.type == "export_specifier":
                    self.exported_names.add(self.t(s.child_by_field_name("name")))
            return
        # export default <expression>
        for ch in node.named_children:
            if ch.type == "identifier":
                self.exported_names.add(self.t(ch))
                self.facts["default"] = self.t(ch)
            elif ch.type in _FUNC_VALUES or ch.type == "class":
                self.facts["default"] = self.declaration(ch, "", True, fallback_name="default")
            else:
                self.refs(ch, "")

    def module_expression(self, node):
        expr = node.named_children[0] if node.named_children else None
        if expr is not None and expr.type == "assignment_expression":
            left = self.t(expr.child_by_field_name("left"))
            if left.startswith("module.exports") or left.startswith("exports."):
                right = expr.child_by_field_name("right")
                if right is not None and right.type in _FUNC_VALUES | {"class"}:
                    name = left.split(".")[-1] if left.startswith("exports.") else "default"
                    self.declaration(right, "", True, fallback_name=name)
                    return
                if right is not None and right.type == "object":
                    for p in right.named_children:
                        if p.type == "shorthand_property_identifier":
                            self.exported_names.add(self.t(p))
        self.refs(node, "")

    # --- declarations --------------------------------------------------------------------------
    def declaration(self, node, owner: str, exported: bool, fallback_name: str | None = None, name: str | None = None):
        kind = _DECL_KIND.get(node.type, "function")
        if name is None:
            nn = node.child_by_field_name("name")
            name = self.t(nn) if nn is not None else (fallback_name or "anonymous")
        q = self.add_symbol(node, name, kind, owner, exported)
        if kind == "class":
            for h in node.named_children:
                if h.type == "class_heritage":
                    for c in h.named_children:
                        if c.type == "extends_clause":
                            for v in c.named_children:
                                if v.type in ("identifier", "member_expression"):
                                    self.ref(q, self.callee(v), "inherit", v)
                                else:
                                    self.refs(v, q)
                        elif c.type == "implements_clause":
                            for v in c.named_children:
                                self.ref(q, self.t(v).split("<")[0], "implements", v)
                elif h.type in ("identifier", "member_expression") and node.child_by_field_name("name") != h:
                    pass
            body = node.child_by_field_name("body")
            if body is not None:
                self.class_body(body, q)
        elif kind == "interface":
            for c in node.named_children:
                if c.type == "extends_type_clause":
                    for v in c.named_children:
                        self.ref(q, self.t(v).split("<")[0], "inherit", v)
            self.refs(node.child_by_field_name("body"), q)
        else:
            for f in ("parameters", "return_type", "body", "value", "type_parameters"):
                part = node.child_by_field_name(f)
                if part is not None:
                    self.refs(part, q)
        return q

    def class_body(self, body, cls: str):
        for m in body.named_children:
            t = m.type
            if t in ("method_definition", "method_signature", "abstract_method_signature"):
                nn = m.child_by_field_name("name")
                name = self.t(nn) if nn is not None else "?"
                q = self.add_symbol(m, name, "method", cls, True)
                for f in ("parameters", "return_type", "body"):
                    part = m.child_by_field_name(f)
                    if part is not None:
                        self.refs(part, q)
            elif t in ("public_field_definition", "field_definition", "property_signature"):
                nn = m.child_by_field_name("name") or m.child_by_field_name("property")
                name = self.t(nn) if nn is not None else "?"
                value = m.child_by_field_name("value")
                kind = "method" if value is not None and value.type in _FUNC_VALUES else "field"
                q = self.add_symbol(m, name, kind, cls, True)
                for f in ("type", "value"):
                    part = m.child_by_field_name(f)
                    if part is not None:
                        self.refs(part, q)
            elif t == "decorator":
                self.refs(m, cls)
            else:
                self.refs(m, cls)

    def variables(self, node, exported: bool):
        const = self.t(node).lstrip().startswith("const")
        for d in node.named_children:
            if d.type != "variable_declarator":
                continue
            nn = d.child_by_field_name("name")
            value = d.child_by_field_name("value")
            if nn is None or nn.type != "identifier":
                self.refs(d, "")
                continue
            name = self.t(nn)
            if value is not None and value.type == "call_expression" and self.t(value.child_by_field_name("function")) == "require":
                self.require(value, name)
                continue
            if value is not None and _is_function_like(value):
                kind = "class" if value.type == "class" else "function"
            else:
                kind = "constant" if const else "variable"
            q = self.add_symbol(d, name, kind, "", exported, span=node if len(node.named_children) == 1 else d)
            for f in ("type", "value"):
                part = d.child_by_field_name(f)
                if part is not None:
                    self.refs(part, q)

    def require(self, call, local: str):
        args = call.child_by_field_name("arguments")
        s = next((a for a in args.named_children if a.type == "string"), None) if args is not None else None
        if s is not None:
            self.facts["imports"].append({"m": self.string(s), "names": [], "star": False, "alias": local,
                                          "line": call.start_point[0] + 1, "re": False})

    def add_symbol(self, node, name: str, kind: str, owner: str, exported: bool, span=None) -> str:
        span = span or node
        q = f"{owner}.{name}" if owner else name
        base, n = q, 2
        while q in self.quals:
            q, n = f"{base}~{n}", n + 1
        self.quals.add(q)
        body = node.child_by_field_name("body")
        value = node.child_by_field_name("value")
        if body is None and value is not None:  # const f = (…) => { … }
            body = value.child_by_field_name("body")
        head_end = body.start_byte if body is not None and body.start_byte > node.start_byte else min(node.end_byte, node.start_byte + 200)
        sig = one_line(self.src[span.start_byte: head_end].decode("utf-8", "replace").rstrip(" {=>"))
        self.facts["symbols"].append({
            "q": q, "n": name, "k": kind, "s": span.start_point[0] + 1, "e": span.end_point[0] + 1,
            "sig": sig, "p": owner or None, "h": body_hash(self.t(span), _COMMENT),
            "doc": self.jsdoc(span), "x": exported or (owner != "" and not name.startswith("#")),
        })
        return q

    def jsdoc(self, node) -> str | None:
        prev = node.prev_named_sibling
        if prev is None and node.parent is not None and node.parent.type == "export_statement":
            prev = node.parent.prev_named_sibling
        if prev is not None and prev.type == "comment" and prev.end_point[0] >= node.start_point[0] - 1:
            raw = self.t(prev)
            if raw.startswith("/**"):
                lines = [l.strip().lstrip("*").strip() for l in raw[3:-2].splitlines()]
                lines = [l for l in lines if l and not l.startswith("@")]
                return one_line(lines[0]) if lines else None
        return None

    # --- references ----------------------------------------------------------------------------
    def string(self, node) -> str:
        if node is None:
            return ""
        frag = next((c for c in node.named_children if c.type == "string_fragment"), None)
        return self.t(frag) if frag is not None else self.t(node).strip("'\"`")

    def callee(self, fn) -> str:
        t = re.sub(r"\s+|\?", "", self.t(fn))
        if _DOTTED.match(t):
            return t
        if fn.type == "member_expression":
            prop = fn.child_by_field_name("property")
            return "?." + (self.t(prop) if prop is not None else "?")
        return "?"

    def ref(self, owner: str, name: str, kind: str, node):
        if name and name != "?":
            self.facts["refs"].append({"f": owner, "n": name, "k": kind, "line": node.start_point[0] + 1})

    def refs(self, node, owner: str):
        if node is None:
            return
        stack = [node]
        while stack:
            n = stack.pop()
            t = n.type
            if t in _SKIP:
                continue
            if t == "call_expression":
                fn = n.child_by_field_name("function")
                args = n.child_by_field_name("arguments")
                if fn is not None and fn.type == "import":  # dynamic import()
                    s = next((a for a in args.named_children if a.type == "string"), None) if args is not None else None
                    if s is not None:
                        self.facts["imports"].append({"m": self.string(s), "names": [], "star": False, "alias": None,
                                                      "line": n.start_point[0] + 1, "re": False})
                    continue
                if fn is not None:
                    name = self.callee(fn)
                    if name == "require" and args is not None:
                        self.require(n, None)
                        continue
                    self.ref(owner, name, "call", n)
                    if not _DOTTED.match(re.sub(r"\s+|\?", "", self.t(fn))):
                        stack.append(fn)
                if args is not None:
                    stack.append(args)
                continue
            if t == "new_expression":
                c = n.child_by_field_name("constructor")
                if c is not None:
                    self.ref(owner, self.callee(c), "new", n)
                a = n.child_by_field_name("arguments")
                if a is not None:
                    stack.append(a)
                continue
            if t in ("jsx_opening_element", "jsx_self_closing_element"):
                nm = n.child_by_field_name("name")
                if nm is not None:
                    tag = self.t(nm)
                    if tag[:1].isupper() or "." in tag:
                        self.ref(owner, tag, "jsx", n)
                for a in n.named_children:
                    if a.type == "jsx_attribute":
                        stack.append(a)
                continue
            if t == "jsx_expression":
                for ch in n.named_children:
                    if ch.type in ("identifier", "member_expression"):
                        self.ref(owner, self.callee(ch), "ref", ch)
                    else:
                        stack.append(ch)
                continue
            if t in ("type_identifier",):
                self.ref(owner, self.t(n), "type", n)
                continue
            if t == "nested_type_identifier":
                self.ref(owner, self.t(n), "type", n)
                continue
            if t in ("arguments", "array", "pair", "return_statement", "shorthand_property_identifier"):
                if t == "shorthand_property_identifier":
                    self.ref(owner, self.t(n), "ref", n)
                    continue
                for ch in n.named_children:
                    if ch.type in ("identifier", "member_expression") and (t != "pair" or ch == n.child_by_field_name("value")):
                        self.ref(owner, self.callee(ch), "ref", ch)
                    else:
                        stack.append(ch)
                continue
            if t == "import_statement":
                self.import_stmt(n)
                continue
            stack.extend(n.named_children)


_HEADER = re.compile(r"\A(?:#![^\n]*\n)?\s*(?:/\*\*?(?P<block>.*?)\*/|(?P<lines>(?://[^\n]*\n\s*)+))", re.S)


def _header_comment(text: str) -> str | None:
    m = _HEADER.match(text)
    if not m:
        return None
    raw = m.group("block") if m.group("block") is not None else m.group("lines")
    for line in raw.splitlines():
        line = line.strip().lstrip("/*").strip()
        if line and not line.startswith(("@", "eslint", "prettier", "Copyright", "SPDX")):
            return one_line(line)
    return None


def _is_function_like(value) -> bool:
    if value.type in _FUNC_VALUES or value.type == "class":
        return True
    # React.memo(() => …), forwardRef(function …), styled(...)…: a call wrapping a function literal
    if value.type == "call_expression":
        args = value.child_by_field_name("arguments")
        return args is not None and any(a.type in _FUNC_VALUES for a in args.named_children)
    return False
