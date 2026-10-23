"""Tier-1 extraction for Go, Java, Rust, C, C++ and C#: one tree walker, a small spec per language.

The walker finds definitions (types and their members, free functions, fields), imports, and references.
Two things make the references resolvable later:

- Receiver types. `svc.place()` means nothing until we know `svc` is an `OrderService`. Local variable
  types are read from declarations (`OrderService svc`, `var svc = new OrderService()`,
  `let svc = OrderService::new()`, `svc := orders.NewService()` → `orders.Service` by Go's New<T>
  convention), and member types from field declarations, so `s.store.Save()` becomes `Store.Save`.
- Out-of-line members. Go methods, Rust `impl` blocks and C++ `Type::method` definitions are attached to
  their type, so a method has the same qualified name wherever its body lives.
"""

from __future__ import annotations

import re

from . import body_hash, empty_facts, one_line
from .ts import parser_for, text_of

_COMMENT = r"//[^\n]*|/\*.*?\*/"
_GENERIC = re.compile(r"<[^<>]*>")
_DOTTED = re.compile(r"^[A-Za-z_][\w.]*$")
HEADER_EXT = (".h", ".hh", ".hpp", ".hxx", ".h++", ".inl")


def clean_type(t: str | None) -> str | None:
    """`const std::vector<Order>&` → `vector`, `*orders.Service` → `orders.Service`, `Box<dyn T>` → `Box`."""
    if not t:
        return None
    t = t.strip()
    for _ in range(3):
        t = _GENERIC.sub("", t)
    t = re.sub(r"\b(const|volatile|mut|dyn|impl|struct|class|enum|ref|out|in|readonly|unsigned|signed)\b", "", t)
    t = t.replace("::", ".").replace("[]", "").replace("?", "")
    t = re.sub(r"[*&\s]", "", t)
    t = t.lstrip("[]")
    if t.startswith("std."):
        return None  # standard library types never resolve inside the repo
    return t if _DOTTED.match(t) else None


class Walker:
    lang = ""
    DEFS: dict[str, str] = {}             # node type → symbol kind
    CONTAINERS: set[str] = set()          # walked with the same owner (bodies, lists, export wrappers)
    NAMESPACES: set[str] = set()          # named scopes that are not symbols (C++/C# namespaces)
    CALLS: set[str] = set()
    NEWS: dict[str, str] = {}             # node type → field holding the constructed type
    TYPE_IDS = {"type_identifier"}
    CLASSY = {"class", "interface", "enum"}

    def __init__(self, src: bytes):
        self.src = src
        self.facts = empty_facts()
        self.quals: set[str] = set()
        self.fields: dict[str, dict[str, str]] = {}   # class name → field → type
        self.class_names: set[str] = set()

    def t(self, n) -> str:
        return text_of(n, self.src)

    # --- overridable pieces -----------------------------------------------------------------------
    def name_of(self, node) -> str | None:
        n = node.child_by_field_name("name")
        return self.t(n) if n is not None else None

    def body_of(self, node):
        return node.child_by_field_name("body")

    def attach_to(self, node) -> str | None:
        """Owning type of an out-of-line member (Go receiver, Rust impl, C++ Type::method)."""
        return None

    def inherits(self, node) -> list[tuple[str, str]]:
        return []

    def import_(self, node) -> bool:
        return False

    def exported(self, node, name: str) -> bool:
        return not name.startswith("_")

    def local_types(self, node) -> list[tuple[str, str]]:
        """(variable, type) pairs declared by `node`, if it is a declaration."""
        return []

    def field_decls(self, node) -> list[tuple[str, str | None]]:
        """(field, type) pairs declared by a field node inside a class body."""
        return []

    def callee(self, node) -> str | None:
        fn = node.child_by_field_name("function")
        return self.t(fn) if fn is not None else None

    def extra_refs(self, node, owner: str, stack: list) -> bool:
        return False

    # --- driver -------------------------------------------------------------------------------------
    def run(self, root):
        self.collect_fields(root)
        self.walk(root, "", None)
        return self.facts

    def collect_fields(self, root):
        """Pre-pass: field types of every class in the file, so methods anywhere can use them."""
        stack = [(root, None)]
        while stack:
            n, cls = stack.pop()
            kind = self.DEFS.get(n.type)
            if kind in self.CLASSY or kind == "type":
                name = self.name_of(n)
                if name:
                    self.class_names.add(name)
                    cls = name
            elif cls and n.type not in self.CONTAINERS:
                for f, ty in self.field_decls(n):
                    if ty:
                        self.fields.setdefault(cls, {})[f] = ty
            stack.extend((c, cls) for c in n.named_children)

    def walk(self, node, owner: str, cls: str | None):
        for ch in node.named_children:
            if self.import_(ch):
                continue
            kind = self.DEFS.get(ch.type)
            if kind:
                self.define(ch, kind, owner, cls)
            elif ch.type in self.NAMESPACES:
                body = self.body_of(ch)
                if body is not None:
                    self.walk(body, owner, cls)
            elif ch.type in self.CONTAINERS:
                self.walk(ch, owner, cls)
            elif cls and self.field_decls(ch):
                for f, ty in self.field_decls(ch):
                    self.add(ch, f, "field", owner)
                    if ty:  # kept so other files can follow `obj.field.method()` through it
                        self.facts["symbols"][-1]["ty"] = ty
                self.refs(ch, owner, cls)
            else:
                self.refs(ch, owner, cls)

    def add(self, node, name: str, kind: str, owner: str, parent: str | None = None) -> str:
        q = f"{owner}.{name}" if owner else name
        base, i = q, 2
        while q in self.quals:
            q, i = f"{base}~{i}", i + 1
        self.quals.add(q)
        body = self.body_of(node)
        head_end = body.start_byte if body is not None and body.start_byte > node.start_byte else min(node.end_byte, node.start_byte + 240)
        self.facts["symbols"].append({
            "q": q, "n": name, "k": kind, "s": node.start_point[0] + 1, "e": node.end_point[0] + 1,
            "sig": one_line(self.src[node.start_byte:head_end].decode("utf-8", "replace").rstrip(" {=")),
            "p": parent if parent is not None else (owner or None), "h": body_hash(self.t(node), _COMMENT),
            "doc": self.doc_of(node), "x": self.exported(node, name),
        })
        return q

    def define(self, node, kind: str, owner: str, cls: str | None):
        name = self.name_of(node)
        if not name:
            self.refs(node, owner, cls)
            return
        attach = self.attach_to(node)
        parent = None
        if attach:  # out-of-line member: qualify by its type; parent only if the type is defined in this file
            owner_q = attach
            parent = attach if attach in self.quals else ""
            cls = attach
        else:
            owner_q = owner
        if kind == "function" and (cls or attach):
            kind = "method"
        q = self.add(node, name, kind, owner_q, parent=parent)
        if parent == "":  # top-level symbol whose qualified name carries the type
            self.facts["symbols"][-1]["p"] = None
        for target, rk in self.inherits(node):
            if target:
                self.facts["refs"].append({"f": q, "n": target, "k": rk, "line": node.start_point[0] + 1})
        body = self.body_of(node)
        if kind in self.CLASSY or kind == "type":
            if body is not None:
                self.walk(body, q, name)
            for f in ("parameters", "parameter_list", "type"):  # records, type aliases
                part = node.child_by_field_name(f)
                if part is not None and part != body:
                    self.refs(part, q, name)
        else:
            self.refs(node, q, cls)

    def doc_of(self, node) -> str | None:
        prev = node.prev_named_sibling
        lines = []
        while prev is not None and prev.type in ("comment", "line_comment", "block_comment") and \
                prev.end_point[0] >= node.start_point[0] - 1 - len(lines):
            lines.insert(0, self.t(prev))
            prev = prev.prev_named_sibling
        for raw in "\n".join(lines).splitlines():
            line = raw.strip().lstrip("/*!#").strip()
            if line and not line.startswith(("@", "<summary>", "</summary>")):
                return one_line(re.sub(r"</?\w+>", "", line))
        return None

    # --- references -------------------------------------------------------------------------------------
    def typed(self, callee: str, locals_: dict[str, str], cls: str | None) -> str:
        parts = callee.split(".")
        head = parts[0]
        if head in locals_:
            parts = locals_[head].split(".") + parts[1:]
        elif head in ("this", "self", "base") and cls and len(parts) >= 2:
            parts = [cls] + parts[1:]
        elif cls and head in self.fields.get(cls, {}) and len(parts) >= 2:  # C++/C#/Java implicit this
            parts = [cls] + parts
        else:
            return callee
        # follow field types along the chain: Service.store.Save → Store.Save
        while len(parts) >= 3 and parts[1] in self.fields.get(parts[0], {}):
            parts = self.fields[parts[0]][parts[1]].split(".") + parts[2:]
        return ".".join(parts)

    def norm(self, text: str | None) -> str | None:
        if not text:
            return None
        t = _GENERIC.sub("", text).replace("::", ".").replace("->", ".").replace("?.", ".").replace("!", "")
        t = re.sub(r"\s+", "", t)
        if _DOTTED.match(t):
            return t
        last = re.findall(r"([A-Za-z_]\w*)$", t)
        return "?." + last[0] if last else None

    def refs(self, node, owner: str, cls: str | None):
        locals_: dict[str, str] = {}
        if owner:  # local variable and parameter types inside a definition
            for n in _descend_all(node):
                for var, ty in self.local_types(n):
                    if ty:
                        locals_[var] = ty
            for var, ty in self.receiver(node, cls):
                if ty:
                    locals_.setdefault(var, ty)
        stack = [node]
        line = lambda n: n.start_point[0] + 1
        while stack:
            n = stack.pop()
            t = n.type
            if t in ("comment", "line_comment", "block_comment", "string_literal", "raw_string_literal",
                     "interpreted_string_literal", "string", "char_literal"):
                continue
            if self.import_(n):
                continue
            if n is not node and self.DEFS.get(t) in self.CLASSY:  # local/anonymous class: its own scope
                continue
            if self.extra_refs(n, owner, stack):
                continue
            if t in self.CALLS:
                name = self.norm(self.callee(n))
                if name:
                    self.facts["refs"].append({"f": owner, "n": self.typed(name, locals_, cls), "k": "call", "line": line(n)})
                stack.extend(c for c in n.named_children if c.type not in ("identifier",))
                continue
            if t in self.NEWS:
                ty = n.child_by_field_name(self.NEWS[t])
                name = clean_type(self.t(ty)) if ty is not None else None
                if name:
                    self.facts["refs"].append({"f": owner, "n": name, "k": "new", "line": line(n)})
                stack.extend(n.named_children)
                continue
            if t in self.TYPE_IDS:
                name = clean_type(self.t(n))
                if name and name[:1].isupper():
                    self.facts["refs"].append({"f": owner, "n": name, "k": "type", "line": line(n)})
                continue
            stack.extend(n.named_children)

    def receiver(self, node, cls) -> list[tuple[str, str]]:
        return []


def _descend_all(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.named_children)


def _string_content(walker, node) -> str:
    frag = next((c for c in node.named_children if "content" in c.type or c.type == "string_fragment"), None)
    return walker.t(frag) if frag is not None else walker.t(node).strip("\"'`<>")


# --- Go ---------------------------------------------------------------------------------------------------
class GoWalker(Walker):
    lang = "go"
    DEFS = {"function_declaration": "function", "method_declaration": "method", "type_spec": "type",
            "const_spec": "constant", "var_spec": "variable"}
    CONTAINERS = {"type_declaration", "const_declaration", "var_declaration", "field_declaration_list"}
    CALLS = {"call_expression"}
    NEWS = {"composite_literal": "type"}

    def name_of(self, node):
        n = node.child_by_field_name("name")
        if n is None and node.type in ("const_spec", "var_spec"):
            n = next((c for c in node.named_children if c.type == "identifier"), None)
        return self.t(n) if n is not None else None

    def define(self, node, kind, owner, cls):
        if node.type == "type_spec":
            ty = node.child_by_field_name("type")
            kind = {"struct_type": "class", "interface_type": "interface"}.get(ty.type if ty is not None else "", "type")
        if node.type in ("const_spec", "var_spec") and owner:
            self.refs(node, owner, cls)
            return
        super().define(node, kind, owner, cls)

    def body_of(self, node):
        if node.type == "type_spec":
            ty = node.child_by_field_name("type")
            if ty is not None:
                return next((c for c in ty.named_children if c.type in ("field_declaration_list", "method_spec_list")), ty)
            return None
        return node.child_by_field_name("body")

    def attach_to(self, node):
        if node.type != "method_declaration":
            return None
        recv = node.child_by_field_name("receiver")
        if recv is None:
            return None
        for p in recv.named_children:
            ty = p.child_by_field_name("type")
            if ty is not None:
                return clean_type(self.t(ty))
        return None

    def receiver(self, node, cls):
        if node.type == "method_declaration":
            recv = node.child_by_field_name("receiver")
            for p in recv.named_children if recv is not None else []:
                n, ty = p.child_by_field_name("name"), p.child_by_field_name("type")
                if n is not None and ty is not None:
                    return [(self.t(n), clean_type(self.t(ty)))]
        return []

    def field_decls(self, node):
        if node.type != "field_declaration":
            return []
        ty = node.child_by_field_name("type")
        names = [self.t(c) for c in node.children_by_field_name("name")]
        return [(n, clean_type(self.t(ty)) if ty is not None else None) for n in names]

    def local_types(self, node):
        if node.type == "short_var_declaration" or node.type == "var_spec":
            left = node.child_by_field_name("left") or node.child_by_field_name("name")
            right = node.child_by_field_name("right") or node.child_by_field_name("value")
            ty = node.child_by_field_name("type")
            names = [self.t(c) for c in (left.named_children if left is not None and left.type == "expression_list" else [left]) if c is not None]
            if ty is not None:
                return [(n, clean_type(self.t(ty))) for n in names]
            vals = right.named_children if right is not None and right.type == "expression_list" else ([right] if right is not None else [])
            out = []
            for n, v in zip(names, vals):
                out.append((n, self.value_type(v)))
            return out
        if node.type == "parameter_declaration":
            ty = node.child_by_field_name("type")
            return [(self.t(c), clean_type(self.t(ty))) for c in node.children_by_field_name("name")] if ty is not None else []
        return []

    def value_type(self, v) -> str | None:
        if v.type == "unary_expression":
            v = v.child_by_field_name("operand") or v
        if v.type == "composite_literal":
            ty = v.child_by_field_name("type")
            return clean_type(self.t(ty)) if ty is not None else None
        if v.type == "call_expression":
            fn = self.t(v.child_by_field_name("function"))
            m = re.match(r"^(?:(\w+)\.)?New(\w+)$", fn)  # Go convention: NewT() returns *T
            if m:
                return f"{m.group(1)}.{m.group(2)}" if m.group(1) else m.group(2)
        return None

    def import_(self, node):
        if node.type == "package_clause":
            pkg = next((c for c in node.named_children), None)
            if pkg is not None:
                self.facts["package"] = self.t(pkg)
                if self.t(pkg) == "main":
                    self.facts["entry"] = True
            return True
        if node.type != "import_spec":
            return node.type in ("import_declaration", "import_spec_list") and self._imports(node)
        path = node.child_by_field_name("path")
        name = node.child_by_field_name("name")
        spec = _string_content(self, path) if path is not None else ""
        alias = self.t(name) if name is not None else spec.rsplit("/", 1)[-1]
        if alias == "_":
            return True
        self.facts["imports"].append({"m": spec, "names": [], "star": alias == ".", "alias": None if alias == "." else alias,
                                      "line": node.start_point[0] + 1, "re": False})
        return True

    def _imports(self, node):
        for c in node.named_children:
            self.import_(c)
        return True

    def exported(self, node, name):
        return name[:1].isupper()


# --- Java -------------------------------------------------------------------------------------------------
class JavaWalker(Walker):
    lang = "java"
    DEFS = {"class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum",
            "record_declaration": "class", "annotation_type_declaration": "interface",
            "method_declaration": "function", "constructor_declaration": "function"}
    CONTAINERS = {"class_body", "interface_body", "enum_body", "enum_body_declarations"}
    CALLS = {"method_invocation"}
    NEWS = {"object_creation_expression": "type"}

    def callee(self, node):
        obj, name = node.child_by_field_name("object"), node.child_by_field_name("name")
        if name is None:
            return None
        return f"{self.t(obj)}.{self.t(name)}" if obj is not None else self.t(name)

    def inherits(self, node):
        out = []
        sup = node.child_by_field_name("superclass")
        if sup is not None:
            out += [(clean_type(self.t(c)), "inherit") for c in sup.named_children]
        ifs = node.child_by_field_name("interfaces")
        if ifs is not None:
            for tl in ifs.named_children:
                out += [(clean_type(self.t(c)), "implements") for c in (tl.named_children if tl.type == "type_list" else [tl])]
        for c in node.named_children:
            if c.type == "extends_interfaces":
                for tl in c.named_children:
                    out += [(clean_type(self.t(x)), "inherit") for x in tl.named_children]
        return out

    def field_decls(self, node):
        if node.type not in ("field_declaration", "constant_declaration"):
            return []
        ty = node.child_by_field_name("type")
        return [(self.t(d.child_by_field_name("name")), clean_type(self.t(ty)) if ty is not None else None)
                for d in node.children_by_field_name("declarator") if d.child_by_field_name("name") is not None]

    def local_types(self, node):
        if node.type in ("local_variable_declaration", "formal_parameter", "field_declaration"):
            ty = node.child_by_field_name("type")
            out = []
            if node.type == "formal_parameter":
                n = node.child_by_field_name("name")
                return [(self.t(n), clean_type(self.t(ty)))] if n is not None and ty is not None else []
            for d in node.children_by_field_name("declarator"):
                n, v = d.child_by_field_name("name"), d.child_by_field_name("value")
                t = clean_type(self.t(ty)) if ty is not None and self.t(ty) != "var" else None
                if t is None and v is not None and v.type == "object_creation_expression":
                    t = clean_type(self.t(v.child_by_field_name("type")))
                if n is not None:
                    out.append((self.t(n), t))
            return out
        return []

    def import_(self, node):
        if node.type == "package_declaration":
            self.facts["package"] = self.t(node.named_children[0]) if node.named_children else None
            return True
        if node.type != "import_declaration":
            return False
        star = any(c.type == "asterisk" for c in node.named_children)
        static = "static" in self.t(node).split()[:2]
        target = next((self.t(c) for c in node.named_children if c.type in ("scoped_identifier", "identifier")), "")
        if star:
            self.facts["imports"].append({"m": target, "names": [], "star": True, "alias": None,
                                          "line": node.start_point[0] + 1, "re": False})
        elif static:  # import static a.b.C.m → member m of class C
            cls, _, member = target.rpartition(".")
            self.facts["imports"].append({"m": cls, "names": [[member, member]], "star": False, "alias": None,
                                          "line": node.start_point[0] + 1, "re": False, "cls": True})
        else:
            self.facts["imports"].append({"m": target, "names": [], "star": False, "alias": target.rsplit(".", 1)[-1],
                                          "line": node.start_point[0] + 1, "re": False, "cls": True})
        return True

    def exported(self, node, name):
        mods = next((self.t(c) for c in node.named_children if c.type == "modifiers"), "")
        return "private" not in mods

    def define(self, node, kind, owner, cls):
        if node.type == "method_declaration" and self.name_of(node) == "main" and "static" in self.t(node)[:200]:
            self.facts["entry"] = True
        super().define(node, kind, owner, cls)


# --- C# ---------------------------------------------------------------------------------------------------
class CSharpWalker(JavaWalker):
    lang = "csharp"
    DEFS = {"class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum",
            "struct_declaration": "class", "record_declaration": "class", "record_struct_declaration": "class",
            "method_declaration": "function", "constructor_declaration": "function", "delegate_declaration": "type"}
    CONTAINERS = {"declaration_list", "enum_member_declaration_list"}
    NAMESPACES = {"namespace_declaration", "file_scoped_namespace_declaration"}
    CALLS = {"invocation_expression"}
    NEWS = {"object_creation_expression": "type"}
    TYPE_IDS = {"identifier_type"}

    def walk(self, node, owner, cls):
        for ch in node.named_children:  # remember declared namespaces: `using X.Y` is resolved through them
            if ch.type in self.NAMESPACES:
                n = ch.child_by_field_name("name")
                if n is not None:
                    self.facts.setdefault("namespaces", []).append(self.t(n))
                if ch.type == "file_scoped_namespace_declaration":
                    self.walk(ch, owner, cls)
                    continue
        super().walk(node, owner, cls)

    def body_of(self, node):
        b = node.child_by_field_name("body")
        if b is None and node.type in ("record_declaration", "file_scoped_namespace_declaration"):
            b = next((c for c in node.named_children if c.type == "declaration_list"), None)
        return b

    def callee(self, node):
        fn = node.child_by_field_name("function")
        return self.t(fn) if fn is not None else None

    def inherits(self, node):
        bl = next((c for c in node.named_children if c.type == "base_list"), None)
        if bl is None:
            return []
        out = []
        for i, c in enumerate(bl.named_children):
            name = clean_type(self.t(c))
            if name:  # C#: by convention interfaces are IName
                out.append((name, "implements" if re.match(r"^I[A-Z]", name.rsplit(".", 1)[-1]) else "inherit"))
        return out

    def field_decls(self, node):
        if node.type == "property_declaration":
            n, ty = node.child_by_field_name("name"), node.child_by_field_name("type")
            return [(self.t(n), clean_type(self.t(ty)) if ty is not None else None)] if n is not None else []
        if node.type == "field_declaration":
            vd = next((c for c in node.named_children if c.type == "variable_declaration"), None)
            if vd is None:
                return []
            ty = vd.child_by_field_name("type")
            out = []
            for d in vd.named_children:
                if d.type == "variable_declarator":
                    n = d.child_by_field_name("name") or next((c for c in d.named_children if c.type == "identifier"), None)
                    t = clean_type(self.t(ty)) if ty is not None and self.t(ty) != "var" else None
                    if t is None:
                        oc = next((c for c in _descend_all(d) if c.type == "object_creation_expression"), None)
                        t = clean_type(self.t(oc.child_by_field_name("type"))) if oc is not None else None
                    if n is not None:
                        out.append((self.t(n), t))
            return out
        return []

    def local_types(self, node):
        if node.type == "variable_declaration":
            ty = node.child_by_field_name("type")
            out = []
            for d in node.named_children:
                if d.type != "variable_declarator":
                    continue
                n = d.child_by_field_name("name") or next((c for c in d.named_children if c.type == "identifier"), None)
                t = clean_type(self.t(ty)) if ty is not None and self.t(ty) != "var" else None
                if t is None:
                    oc = next((c for c in _descend_all(d) if c.type == "object_creation_expression"), None)
                    t = clean_type(self.t(oc.child_by_field_name("type"))) if oc is not None else None
                if n is not None:
                    out.append((self.t(n), t))
            return out
        if node.type == "parameter":
            n, ty = node.child_by_field_name("name"), node.child_by_field_name("type")
            return [(self.t(n), clean_type(self.t(ty)))] if n is not None and ty is not None else []
        return []

    def import_(self, node):
        if node.type != "using_directive":
            return False
        names = [c for c in node.named_children if c.type in ("qualified_name", "identifier")]
        if not names:
            return True
        alias = node.child_by_field_name("name")
        target = self.t(names[-1])
        if alias is not None and self.t(alias) != target:
            self.facts["imports"].append({"m": target, "names": [], "star": False, "alias": self.t(alias),
                                          "line": node.start_point[0] + 1, "re": False, "ns": True})
        else:
            self.facts["imports"].append({"m": target, "names": [], "star": True, "alias": None,
                                          "line": node.start_point[0] + 1, "re": False, "ns": True})
        return True

    def exported(self, node, name):
        mods = " ".join(self.t(c) for c in node.named_children if c.type == "modifier")
        return "private" not in mods

    def define(self, node, kind, owner, cls):
        if node.type == "method_declaration" and self.name_of(node) == "Main":
            self.facts["entry"] = True
        Walker.define(self, node, kind, owner, cls)


# --- Rust -------------------------------------------------------------------------------------------------
class RustWalker(Walker):
    lang = "rust"
    DEFS = {"function_item": "function", "function_signature_item": "function", "struct_item": "class",
            "enum_item": "enum", "union_item": "class", "trait_item": "interface", "type_item": "type",
            "const_item": "constant", "static_item": "constant", "macro_definition": "function"}
    CONTAINERS = {"declaration_list"}
    CALLS = {"call_expression"}
    NEWS = {"struct_expression": "name"}

    def walk(self, node, owner, cls):
        for ch in node.named_children:
            if ch.type == "impl_item":
                ty = clean_type(self.t(ch.child_by_field_name("type")))
                trait = ch.child_by_field_name("trait")
                if trait is not None and ty:
                    self.facts["refs"].append({"f": ty if ty in self.quals else "", "n": clean_type(self.t(trait)),
                                               "k": "implements", "line": ch.start_point[0] + 1})
                body = ch.child_by_field_name("body")
                if body is not None and ty:
                    self._impl = ty
                    for item in body.named_children:
                        kind = self.DEFS.get(item.type)
                        if kind:
                            self.define(item, kind, owner, ty)
                        else:
                            self.refs(item, owner, ty)
                    self._impl = None
                continue
            if ch.type == "mod_item":
                name = self.name_of(ch)
                body = self.body_of(ch)
                if body is None and name:  # `mod x;` → the module lives in x.rs / x/mod.rs
                    self.facts["imports"].append({"m": f"self::{name}", "names": [], "star": False, "alias": name,
                                                  "line": ch.start_point[0] + 1, "re": False, "mod": True})
                elif body is not None:
                    self.walk(body, owner, cls)
                continue
            self._walk_one(ch, owner, cls)

    _impl: str | None = None

    def _walk_one(self, ch, owner, cls):
        if self.import_(ch):
            return
        kind = self.DEFS.get(ch.type)
        if kind:
            self.define(ch, kind, owner, cls)
        elif ch.type in self.CONTAINERS:
            self.walk(ch, owner, cls)
        elif cls and self.field_decls(ch):
            for f, ty in self.field_decls(ch):
                self.add(ch, f, "field", owner)
                if ty:
                    self.facts["symbols"][-1]["ty"] = ty
        else:
            self.refs(ch, owner, cls)

    def attach_to(self, node):
        return self._impl

    def name_of(self, node):
        n = node.child_by_field_name("name")
        return self.t(n) if n is not None else None

    def field_decls(self, node):
        if node.type != "field_declaration":
            return []
        n, ty = node.child_by_field_name("name"), node.child_by_field_name("type")
        return [(self.t(n), clean_type(self.t(ty)) if ty is not None else None)] if n is not None else []

    def collect_fields(self, root):
        super().collect_fields(root)

    def define(self, node, kind, owner, cls):
        if node.type == "struct_item":
            body = node.child_by_field_name("body")
            name = self.name_of(node)
            for f in (body.named_children if body is not None else []):
                for fn, ty in self.field_decls(f):
                    if ty and name:
                        self.fields.setdefault(name, {})[fn] = ty
        if node.type == "function_item" and self.name_of(node) == "main" and not cls:
            self.facts["entry"] = True
        super().define(node, kind, owner, cls)

    def local_types(self, node):
        if node.type == "let_declaration":
            pat, ty, v = node.child_by_field_name("pattern"), node.child_by_field_name("type"), node.child_by_field_name("value")
            if pat is None or pat.type != "identifier":
                return []
            if ty is not None:
                return [(self.t(pat), clean_type(self.t(ty)))]
            if v is not None:
                if v.type == "struct_expression":
                    return [(self.t(pat), clean_type(self.t(v.child_by_field_name("name"))))]
                if v.type == "call_expression":  # T::new(), T::default(), T::from(..)
                    fn = self.t(v.child_by_field_name("function"))
                    m = re.match(r"^([A-Z]\w*)::(new|default|from|with_\w+|builder|open|create)$", fn)
                    if m:
                        return [(self.t(pat), m.group(1))]
            return []
        if node.type == "parameter":
            pat, ty = node.child_by_field_name("pattern"), node.child_by_field_name("type")
            return [(self.t(pat), clean_type(self.t(ty)))] if pat is not None and ty is not None and pat.type == "identifier" else []
        return []

    def receiver(self, node, cls):
        return [("self", cls)] if cls else []

    def typed(self, callee, locals_, cls):
        if callee.startswith("Self.") and cls:
            callee = cls + callee[4:]
        return super().typed(callee, locals_, cls)

    def extra_refs(self, n, owner, stack):
        if n.type == "macro_invocation":  # println!("{}", charge(x)): calls inside the token tree
            toks = [c for c in _descend_all(n) if c.type == "token_tree"]
            for tt in toks:
                kids = tt.named_children
                for a, b in zip(kids, kids[1:]):
                    if a.type == "identifier" and b.type == "token_tree" and self.t(b).startswith("("):
                        self.facts["refs"].append({"f": owner, "n": self.t(a), "k": "call", "line": a.start_point[0] + 1})
            return True
        return False

    def import_(self, node):
        if node.type != "use_declaration":
            return False
        arg = node.child_by_field_name("argument")
        groups: dict[str, list[list[str]]] = {}
        stars: list[str] = []

        def expand(n, prefix: list[str]):
            t = n.type
            if t in ("scoped_identifier", "identifier", "crate", "self", "super"):
                segs = prefix + self.t(n).split("::")
                groups.setdefault("::".join(segs[:-1]), []).append([segs[-1], segs[-1]])
            elif t == "use_as_clause":
                path, alias = n.child_by_field_name("path"), n.child_by_field_name("alias")
                segs = prefix + self.t(path).split("::")
                groups.setdefault("::".join(segs[:-1]), []).append([segs[-1], self.t(alias)])
            elif t == "scoped_use_list":
                path = n.child_by_field_name("path")
                p = prefix + (self.t(path).split("::") if path is not None else [])
                lst = n.child_by_field_name("list")
                for c in (lst.named_children if lst is not None else []):
                    expand(c, p)
            elif t == "use_list":
                for c in n.named_children:
                    expand(c, prefix)
            elif t == "use_wildcard":
                inner = n.named_children[0] if n.named_children else None
                stars.append("::".join(prefix + (self.t(inner).split("::") if inner is not None else [])))

        if arg is not None:
            expand(arg, [])
        line = node.start_point[0] + 1
        for m, names in groups.items():
            names = [[a, b] for a, b in names if a != "self"] + [[m.rsplit("::", 1)[-1], b] for a, b in names if a == "self"]
            self.facts["imports"].append({"m": m, "names": names, "star": False, "alias": None, "line": line, "re": False})
        for m in stars:
            self.facts["imports"].append({"m": m, "names": [], "star": True, "alias": None, "line": line, "re": False})
        return True

    def exported(self, node, name):
        return any(c.type == "visibility_modifier" for c in node.named_children)


# --- C / C++ ----------------------------------------------------------------------------------------------
class CppWalker(Walker):
    lang = "cpp"
    DEFS = {"function_definition": "function", "class_specifier": "class", "struct_specifier": "class",
            "union_specifier": "class", "enum_specifier": "enum", "type_definition": "type", "alias_declaration": "type"}
    CONTAINERS = {"declaration_list", "template_declaration", "linkage_specification", "preproc_ifdef", "preproc_if",
                  "preproc_else", "preproc_elif", "field_declaration_list", "export_declaration"}
    NAMESPACES = {"namespace_definition"}
    CALLS = {"call_expression"}
    NEWS = {"new_expression": "type"}
    _NAME_TYPES = {"identifier", "field_identifier", "type_identifier", "destructor_name", "operator_name",
                   "qualified_identifier", "namespace_identifier"}

    def declarator_name(self, d) -> str | None:
        while d is not None and d.type not in self._NAME_TYPES:
            nxt = d.child_by_field_name("declarator")
            if nxt is None:
                nxt = next((c for c in d.named_children if c.type in self._NAME_TYPES or "declarator" in c.type), None)
            d = nxt
        return self.t(d) if d is not None else None

    def name_of(self, node):
        if node.type == "function_definition":
            full = self.declarator_name(node.child_by_field_name("declarator"))
            return full.replace("::", ".").rsplit(".", 1)[-1] if full else None
        if node.type == "type_definition":
            return self.declarator_name(node.child_by_field_name("declarator"))
        if node.type in ("class_specifier", "struct_specifier", "union_specifier", "enum_specifier"):
            if node.child_by_field_name("body") is None:
                return None  # a forward declaration or a use, not a definition
        n = node.child_by_field_name("name")
        return self.t(n).replace("::", ".").rsplit(".", 1)[-1] if n is not None else None

    def define(self, node, kind, owner, cls):
        if node.type == "type_definition":  # typedef struct { … } name;
            inner = node.child_by_field_name("type")
            if inner is not None and inner.child_by_field_name("body") is not None and inner.child_by_field_name("name") is None:
                name = self.name_of(node)
                if name:
                    q = self.add(node, name, "class", owner)
                    self.walk(inner.child_by_field_name("body"), q, name)
                return
        if node.type == "function_definition" and self.name_of(node) == "main" and not cls:
            self.facts["entry"] = True
        super().define(node, kind, owner, cls)

    def attach_to(self, node):
        if node.type != "function_definition":
            return None
        full = self.declarator_name(node.child_by_field_name("declarator"))
        if full and "::" in full:
            scope = full.rsplit("::", 1)[0]
            scope = scope.rsplit("::", 1)[-1]
            return _GENERIC.sub("", scope) or None
        return None

    def walk(self, node, owner, cls):
        for ch in node.named_children:
            if cls and ch.type in ("field_declaration", "declaration"):
                d = ch.child_by_field_name("declarator")
                if d is not None and (d.type == "function_declarator" or any(x.type == "function_declarator" for x in _descend_all(d))):
                    name = self.declarator_name(d)
                    if name:  # a member function declared in the class body
                        self.add(ch, name.rsplit("::", 1)[-1], "method", owner)
                    continue
            self._walk_one(ch, owner, cls)

    def _walk_one(self, ch, owner, cls):
        if self.import_(ch):
            return
        kind = self.DEFS.get(ch.type)
        if kind:
            self.define(ch, kind, owner, cls)
        elif ch.type in self.NAMESPACES:
            body = self.body_of(ch)
            if body is not None:
                self.walk(body, owner, cls)
        elif ch.type in self.CONTAINERS:
            self.walk(ch, owner, cls)
        elif cls and self.field_decls(ch):
            for f, ty in self.field_decls(ch):
                self.add(ch, f, "field", owner)
                if ty:
                    self.facts["symbols"][-1]["ty"] = ty
            self.refs(ch, owner, cls)
        elif ch.type == "declaration" and not owner:
            d = ch.child_by_field_name("declarator")
            fd = next((x for x in _descend_all(d) if x.type == "function_declarator"), None) if d is not None else None
            if fd is not None:
                name = self.declarator_name(fd)
                if name:  # a free function declared in a header (also `const char *f(int)`)
                    self.add(ch, name.replace("::", ".").rsplit(".", 1)[-1], "function", owner)
            self.refs(ch, owner, cls)
        else:
            self.refs(ch, owner, cls)

    def field_decls(self, node):
        if node.type != "field_declaration":
            return []
        ty = node.child_by_field_name("type")
        out = []
        for d in node.children_by_field_name("declarator"):
            if d.type == "function_declarator":
                continue
            name = self.declarator_name(d)
            if name:
                t = clean_type(self.t(ty)) if ty is not None else None
                out.append((name, t.rsplit(".", 1)[-1] if t else None))
        return out

    def local_types(self, node):
        if node.type in ("declaration", "parameter_declaration"):
            ty = node.child_by_field_name("type")
            if ty is None:
                return []
            tname = clean_type(self.t(ty))
            if tname:  # shop::OrderService → OrderService: namespaces are not part of symbol names
                tname = tname.rsplit(".", 1)[-1]
            out = []
            for d in node.children_by_field_name("declarator"):
                name = self.declarator_name(d)
                if not name:
                    continue
                t = tname
                if self.t(ty) == "auto":
                    nx = next((c for c in _descend_all(d) if c.type == "new_expression"), None)
                    t = clean_type(self.t(nx.child_by_field_name("type"))) if nx is not None else None
                out.append((name, t))
            return out
        return []

    def import_(self, node):
        if node.type != "preproc_include":
            return False
        path = node.child_by_field_name("path")
        if path is None:
            return True
        local = path.type == "string_literal"
        spec = _string_content(self, path)
        self.facts["imports"].append({"m": spec, "names": [], "star": True, "alias": None,
                                      "line": node.start_point[0] + 1, "re": False, "include": True, "system": not local})
        return True

    def exported(self, node, name):
        return not self.t(node).lstrip().startswith("static ")

    def receiver(self, node, cls):
        return [("this", cls)] if cls else []


class CWalker(CppWalker):
    lang = "c"


WALKERS = {"go": GoWalker, "java": JavaWalker, "csharp": CSharpWalker, "rust": RustWalker, "cpp": CppWalker, "c": CWalker}


def extract_spec(text: str, lang: str) -> dict:
    src = text.encode("utf-8", "surrogatepass")
    tree = parser_for(lang).parse(src)
    w = WALKERS[lang](src)
    facts = w.run(tree.root_node)
    first = tree.root_node.named_children[0] if tree.root_node.named_children else None
    if first is not None and first.type in ("comment", "line_comment", "block_comment"):
        facts["doc"] = _first_comment_line(w.t(first))
    return facts


def _first_comment_line(raw: str) -> str | None:
    for line in raw.splitlines():
        line = line.strip().lstrip("/*!#").strip()
        if line and not line.startswith(("@", "Copyright", "SPDX", "-*-")):
            return one_line(line)
    return None
