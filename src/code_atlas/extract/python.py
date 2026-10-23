"""Tier-1 Python extractor: classes, functions, methods, module/class fields, imports and calls."""

from __future__ import annotations

import re

from . import body_hash, empty_facts, one_line
from .ts import parser_for, text_of

_DOTTED = re.compile(r"^[A-Za-z_][\w.]*$")
_COMMENT = r"#[^\n]*"
_SKIP = {"string", "comment", "concatenated_string"}
# Bare names inside these are values being passed around (callbacks, registries), worth a "ref".
_VALUE_HOLDERS = {"argument_list", "keyword_argument", "list", "tuple", "dictionary", "pair", "return_statement",
                  "default_parameter", "typed_default_parameter", "set"}
_KEYED = {"keyword_argument", "default_parameter", "typed_default_parameter", "pair"}


def extract_python(text: str) -> dict:
    src = text.encode("utf-8", "surrogatepass")
    tree = parser_for("python").parse(src)
    w = _Walker(src)
    w.block(tree.root_node, "", None)
    w.facts["doc"] = w.docstring(tree.root_node)
    if w.syspath:
        w.facts["syspath"] = sorted(set(w.syspath))
    return w.facts


class _Walker:
    def __init__(self, src: bytes):
        self.src = src
        self.facts = empty_facts()
        self.quals: set[str] = set()
        self.values: dict[str, str] = {}   # module-level NAME = <expression text>, for sys.path evaluation
        self.syspath: list[str] = []
        self.symbol_node: dict = {}

    # --- definitions -------------------------------------------------------------------------
    def block(self, node, owner: str, cls: str | None):
        """Statements of a module, class body or a module-level if/try block."""
        for ch in node.named_children:
            t = ch.type
            if t == "decorated_definition":
                inner = ch.child_by_field_name("definition")
                q = self.define(inner, owner, cls) if inner is not None else owner
                for d in ch.named_children:
                    if d.type == "decorator":
                        self.refs(d, q, decorator=True)
            elif t in ("class_definition", "function_definition"):
                self.define(ch, owner, cls)
            elif t in ("import_statement", "import_from_statement", "future_import_statement"):
                self.imports(ch)
            elif t in ("expression_statement", "assignment"):
                self.assignment(ch, owner, cls)
            elif t == "if_statement" and not cls:
                cond = ch.child_by_field_name("condition")
                ctext = text_of(cond, self.src)
                if "__name__" in ctext and "__main__" in ctext:
                    self.facts["entry"] = True
                    self.refs(ch, owner)
                else:
                    self.compound(ch, owner, cls)
            elif t in ("try_statement", "with_statement") and not cls:
                self.compound(ch, owner, cls)
            else:
                self.refs(ch, owner)

    def compound(self, node, owner: str, cls: str | None):
        """Module-level if/try/with: definitions inside still belong to the module."""
        for sub in node.named_children:
            if sub.type == "block":
                self.block(sub, owner, cls)
            elif sub.type in ("elif_clause", "else_clause", "except_clause", "finally_clause"):
                self.compound(sub, owner, cls)
            else:
                self.refs(sub, owner)

    def define(self, node, owner: str, cls: str | None) -> str:
        name_node = node.child_by_field_name("name")
        name = text_of(name_node, self.src) if name_node is not None else "?"
        q = self.unique(f"{owner}.{name}" if owner else name)
        self.symbol_node[q] = node
        body = node.child_by_field_name("body")
        start = node.start_byte
        sig = self.src[start: body.start_byte if body is not None else node.end_byte].decode("utf-8", "replace")
        sig = one_line(sig.rstrip().rstrip(":"))
        is_class = node.type == "class_definition"
        kind = "class" if is_class else ("method" if cls else "function")
        self.facts["symbols"].append({
            "q": q, "n": name, "k": kind, "s": node.start_point[0] + 1, "e": node.end_point[0] + 1,
            "sig": sig, "p": owner or None, "h": body_hash(text_of(node, self.src), _COMMENT),
            "doc": self.docstring(body), "x": not name.startswith("_"),
        })
        if is_class:
            sup = node.child_by_field_name("superclasses")
            if sup is not None:
                for a in sup.named_children:
                    if a.type in ("identifier", "attribute"):
                        self.add_ref(q, self.callee(a), "inherit", a)
                    elif a.type == "keyword_argument":
                        self.refs(a, q)
            if body is not None:
                self.block(body, q, q)
        else:
            for f in ("parameters", "return_type"):
                part = node.child_by_field_name(f)
                if part is not None:
                    self.refs(part, q)
            if body is not None:
                self.refs(body, q)
        return q

    def assignment(self, stmt, owner: str, cls: str | None):
        a = stmt if stmt.type == "assignment" else (stmt.named_children[0] if stmt.named_children else None)
        if a is None or a.type != "assignment":
            self.refs(stmt, owner)
            return
        left = a.child_by_field_name("left")
        if left is not None and left.type == "identifier":
            name = text_of(left, self.src)
            if name == "__all__":
                return
            right = a.child_by_field_name("right")
            if right is not None and owner == "":
                self.values[name] = text_of(right, self.src)
            kind = "field" if cls else ("constant" if name.isupper() else "variable")
            q = self.unique(f"{owner}.{name}" if owner else name)
            self.facts["symbols"].append({
                "q": q, "n": name, "k": kind, "s": stmt.start_point[0] + 1, "e": stmt.end_point[0] + 1,
                "sig": one_line(text_of(stmt, self.src), 120), "p": owner or None,
                "h": body_hash(text_of(stmt, self.src), _COMMENT), "doc": None, "x": not name.startswith("_"),
            })
            for f in ("type", "right"):
                part = a.child_by_field_name(f)
                if part is not None:
                    self.refs(part, q)
        else:
            self.refs(stmt, owner)

    def docstring(self, body) -> str | None:
        if body is None or not body.named_children:
            return None
        first = body.named_children[0]
        if first.type == "expression_statement" and first.named_children:
            first = first.named_children[0]
        if first.type == "string":
            raw = text_of(first, self.src).strip("rRbBuUfF").strip("'\"").strip()
            return one_line(raw.splitlines()[0] if raw else None)
        return None

    def eval_path(self, expr: str) -> str | None:
        """Best-effort static value of a sys.path entry built from __file__, as a path relative to this file's dir.

        Handles Path(__file__).resolve().parent / "a" / "b", os.path.join(os.path.dirname(__file__), "..", "x"),
        and one level of module-level names (PROJECT_ROOT = Path(__file__).parent)."""
        for _ in range(3):
            names = re.findall(r"\b([A-Za-z_]\w*)\b", expr)
            subs = [n for n in names if n in self.values and n not in ("Path", "os", "sys", "str")]
            if not subs:
                break
            for n in subs:
                expr = re.sub(rf"\b{n}\b", "(" + self.values[n] + ")", expr)
        if "__file__" not in expr:
            return None
        ups = expr.count(".parent") + expr.count("dirname(")
        parts = [p for p in re.findall(r"[\"']([^\"']+)[\"']", expr) if p not in ("__file__",)]
        segs = [".."] * max(0, ups - 1) + [seg for p in parts for seg in p.replace("\\", "/").split("/") if seg and seg != "."]
        return "/".join(segs) or "."

    def unique(self, q: str) -> str:
        base, n = q, 2
        while q in self.quals:
            q, n = f"{base}~{n}", n + 1
        self.quals.add(q)
        return q

    # --- imports -------------------------------------------------------------------------------
    def imports(self, node):
        line = node.start_point[0] + 1
        if node.type == "import_statement":
            for n in node.children_by_field_name("name"):
                if n.type == "aliased_import":
                    mod = text_of(n.child_by_field_name("name"), self.src)
                    self.facts["imports"].append({"m": mod, "names": [], "star": False,
                                                  "alias": text_of(n.child_by_field_name("alias"), self.src),
                                                  "line": line, "re": False})
                else:
                    mod = text_of(n, self.src)
                    self.facts["imports"].append({"m": mod, "names": [], "star": False, "alias": mod,
                                                  "line": line, "re": False})
            return
        if node.type == "future_import_statement":
            return
        mod_node = node.child_by_field_name("module_name")
        mod = text_of(mod_node, self.src).replace(" ", "") if mod_node is not None else ""
        names, star = [], False
        for ch in node.named_children:
            if ch.type == "wildcard_import":
                star = True
        for n in node.children_by_field_name("name"):
            if n.type == "aliased_import":
                names.append([text_of(n.child_by_field_name("name"), self.src),
                              text_of(n.child_by_field_name("alias"), self.src)])
            else:
                t = text_of(n, self.src)
                names.append([t, t])
        self.facts["imports"].append({"m": mod, "names": names, "star": star, "alias": None, "line": line, "re": False})

    # --- references ----------------------------------------------------------------------------
    def callee(self, fn) -> str:
        t = text_of(fn, self.src).replace(" ", "").replace("\n", "")
        if _DOTTED.match(t):
            return t
        if fn.type == "attribute":
            attr = fn.child_by_field_name("attribute")
            obj = fn.child_by_field_name("object")
            a = text_of(attr, self.src) if attr is not None else "?"
            if obj is not None and obj.type == "call" and text_of(obj, self.src).startswith("super("):
                return "super." + a
            return "?." + a
        return "?"

    def add_ref(self, owner: str, name: str, kind: str, node):
        if name and name != "?":
            self.facts["refs"].append({"f": owner, "n": name, "k": kind, "line": node.start_point[0] + 1})

    def refs(self, node, owner: str, decorator: bool = False):
        if node is None:
            return
        stack = [node]
        while stack:
            n = stack.pop()
            t = n.type
            if t in _SKIP:
                continue
            if t == "call":
                fn = n.child_by_field_name("function")
                if fn is not None and text_of(fn, self.src) in ("sys.path.insert", "sys.path.append"):
                    args = n.child_by_field_name("arguments")
                    if args is not None and args.named_children:
                        if owner:  # local names (pkg_root = ROOT / "x") used inside a function
                            for a in _descend(self.symbol_node.get(owner, node), ("assignment",)):
                                left, right = a.child_by_field_name("left"), a.child_by_field_name("right")
                                if left is not None and left.type == "identifier" and right is not None:
                                    self.values.setdefault(text_of(left, self.src), text_of(right, self.src))
                        rel = self.eval_path(text_of(args.named_children[-1], self.src))
                        if rel is not None:
                            self.syspath.append(rel)
                if fn is not None:
                    self.add_ref(owner, self.callee(fn), "decorator" if decorator else "call", n)
                    if fn.type not in ("identifier", "attribute") or not _DOTTED.match(text_of(fn, self.src).replace(" ", "")):
                        stack.append(fn)
                args = n.child_by_field_name("arguments")
                if args is not None:
                    stack.append(args)
                continue
            if t in ("import_statement", "import_from_statement"):
                self.imports(n)
                continue
            if t == "decorator" or (decorator and t in ("identifier", "attribute") and n.parent is not None and n.parent.type == "decorator"):
                inner = n.named_children[0] if t == "decorator" and n.named_children else n
                if inner.type in ("identifier", "attribute"):
                    self.add_ref(owner, self.callee(inner), "decorator", inner)
                    continue
                stack.append(inner)
                continue
            if t == "type":
                for idn in _descend(n, ("identifier", "attribute")):
                    self.add_ref(owner, self.callee(idn), "type", idn)
                continue
            if t in _VALUE_HOLDERS:
                for ch in n.named_children:
                    if ch.type in ("identifier", "attribute") and (t not in _KEYED or ch == n.child_by_field_name("value")):
                        self.add_ref(owner, self.callee(ch), "ref", ch)
                    else:
                        stack.append(ch)
                continue
            stack.extend(n.named_children)


def _descend(node, types):
    out, stack = [], [node]
    while stack:
        n = stack.pop()
        if n.type in types:
            out.append(n)
        else:
            stack.extend(n.named_children)
    return out
