"""Turn per-file facts into repository-wide edges.

Imports become bindings (local name → module or exported member). References are then resolved with
a ladder, most certain first, and every edge records how it was found and how sure we are:

    self/this/super member      0.9
    same-file definition        0.9
    imported binding            0.9  (member of an imported module: 0.85)
    star import / re-export     0.8
    same package (Go, Java, …)  0.7
    global unique name          0.5  (only for languages without explicit per-name imports)

A name that matches nothing is dropped rather than guessed: an unconnected node is honest, a wrong
edge is misleading.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .languages import SPEC_LANGS
from .resolve_langs import C_LANGS, LangImports

JS_LANGS = {"javascript", "typescript", "tsx", "vue", "svelte"}
JS_EXTS = [".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".json"]
SAME_PACKAGE_LANGS = {"go", "java", "kotlin", "scala", "swift", "csharp", "c", "cpp", "objc", "dart", "php"}
GLOBAL_UNIQUE_LANGS = SAME_PACKAGE_LANGS | {"rust", "ruby", "lua", "elixir", "bash", "r", "julia", "zig"}
CODE_KINDS = {"class", "interface", "type", "enum", "function", "method", "constant", "variable", "field", "namespace"}
CALLABLE = {"function", "method", "class"}
EDGE_OF = {"call": "calls", "new": "instantiates", "inherit": "inherits", "implements": "implements",
           "jsx": "calls", "decorator": "references", "type": "references", "ref": "references"}
_SPLIT = re.compile(r"[.:/\\]+")


@dataclass
class Binding:
    kind: str            # module | member | dir
    path: str            # file path, or directory for "dir"
    name: str | None = None


@dataclass
class Edge:
    src: str
    dst: str
    type: str
    conf: float
    via: str
    count: int = 1


@dataclass
class Resolution:
    edges: list[Edge] = field(default_factory=list)
    externals: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    stats: dict[str, int] = field(default_factory=lambda: defaultdict(int))


class Resolver(LangImports):
    def __init__(self, root: Path, files: dict[str, object], facts: dict[str, dict]):
        self.root = root
        self.files = files                    # path → FileEntry
        self.facts = facts                    # path → facts (only parsed files)
        self.paths = {p for p, f in files.items() if f.kind not in ("vendored", "generated", "binary")}
        self.syms: dict[str, dict[str, dict]] = {p: {s["q"]: s for s in fx["symbols"]} for p, fx in facts.items()}
        self.top: dict[str, dict[str, str]] = {}
        self.members: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
        for p, fx in facts.items():
            top = {}
            for s in fx["symbols"]:
                if s["p"] is None and "." in s["q"] and s["k"] in ("method", "field"):
                    # out-of-line member (Go method in another file, C++ Type::method): member of its type
                    self.members[p][s["q"].rsplit(".", 1)[0]].setdefault(s["n"], s["q"])
                elif s["p"] is None:
                    top.setdefault(s["n"], s["q"])
                else:
                    self.members[p][s["p"]].setdefault(s["n"], s["q"])
            self.top[p] = top
        self.by_dir: dict[str, list[str]] = defaultdict(list)
        for p in facts:
            self.by_dir[posixpath.dirname(p)].append(p)
        self._py_index()
        # extra import roots from sys.path manipulation, relative to the repo
        self.extra_roots: dict[str, list[str]] = {}
        for p, fx in facts.items():
            roots = []
            for rel in fx.get("syspath", []):
                r = posixpath.normpath(posixpath.join(posixpath.dirname(p), rel))
                if not r.startswith(".."):
                    roots.append("" if r == "." else r)
            if roots:
                self.extra_roots[p] = roots
        self._stem_index()
        self._tsconfigs()
        self._lang_indexes()
        self.global_names: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for p, fx in facts.items():
            if self.files[p].lang in GLOBAL_UNIQUE_LANGS:
                for s in fx["symbols"]:
                    if s["k"] in CODE_KINDS and (s["p"] is None or s["k"] == "method"):
                        self.global_names[s["n"]].append((p, s["q"]))
        self._bindings: dict[str, tuple[dict[str, Binding], list[str]]] = {}
        self.bases: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        self._export_cache: dict[tuple[str, str], tuple[str, str | None] | None] = {}

    # --- indexes ---------------------------------------------------------------------------------
    def _py_index(self):
        self.py_mod: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for p in self.facts:
            if self.files[p].lang != "python":
                continue
            parts = p[:-3].split("/") if p.endswith(".py") else p[:-4].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):
                self.py_mod[tuple(parts[i:])].append(p)
        self.py_dirs = {posixpath.dirname(p) for p in self.facts if self.files[p].lang == "python"}
        self.py_dir_names = {part for d in self.py_dirs if d for part in d.split("/")}

    def _stem_index(self):
        self.stem: dict[tuple[str, ...], list[str]] = defaultdict(list)
        self.dir_suffix: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for p in self.facts:
            base = p.rsplit(".", 1)[0] if "." in posixpath.basename(p) else p
            parts = base.split("/")
            for i in range(len(parts)):
                self.stem[tuple(parts[i:])].append(p)
        for d in self.by_dir:
            parts = d.split("/") if d else []
            for i in range(len(parts)):
                self.dir_suffix[tuple(parts[i:])].append(d)

    def _tsconfigs(self):
        self.ts_cfg: dict[str, tuple[str, dict[str, list[str]]]] = {}
        for p in self.paths:
            name = posixpath.basename(p)
            if name not in ("tsconfig.json", "jsconfig.json") and not re.match(r"tsconfig\.[\w.]+\.json$", name):
                continue
            try:
                raw = (self.root / p).read_text("utf-8", "replace")
                raw = re.sub(r"//[^\n]*|/\*.*?\*/", "", raw, flags=re.S)
                cfg = json.loads(re.sub(r",(\s*[}\]])", r"\1", raw))
            except (OSError, ValueError):
                continue
            opts = cfg.get("compilerOptions") or {}
            d = posixpath.dirname(p)
            base = posixpath.normpath(posixpath.join(d, opts.get("baseUrl", "."))) if opts.get("baseUrl") or opts.get("paths") else None
            if base is None:
                continue
            base = "" if base == "." else base
            prev = self.ts_cfg.get(d)
            if prev is None or name == "tsconfig.json" or opts.get("paths"):
                self.ts_cfg[d] = (base, opts.get("paths") or {})

    # --- module resolution ---------------------------------------------------------------------
    def _closest(self, importer: str, candidates: list[str], drop_parts) -> str | None:
        """Several files match a module name: prefer one whose package root contains the importer.

        `drop_parts` is how many trailing path segments the module name accounts for (int, or a
        function of the candidate path)."""
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        imp_dir = posixpath.dirname(importer)

        def score(c):
            parts = c.split("/")
            drop = drop_parts(c) if callable(drop_parts) else drop_parts
            root = "/".join(parts[: max(0, len(parts) - drop)])
            inside = root == "" or imp_dir == root or imp_dir.startswith(root + "/")
            return (inside, len(root) if inside else -len(root), -len(c))
        best = max(candidates, key=score)
        if not score(best)[0]:
            return None  # several equally foreign matches: ambiguous, don't guess
        return best

    def resolve_python(self, importer: str, spec: str) -> tuple[str | None, list[str]]:
        """→ (file for the module or None, module parts) for `spec` like `a.b`, `.x`, `..`."""
        dots = len(spec) - len(spec.lstrip("."))
        rest = spec[dots:]
        parts = [x for x in rest.split(".") if x]
        if dots:
            base = posixpath.dirname(importer).split("/") if posixpath.dirname(importer) else []
            base = base[: len(base) - (dots - 1)] if dots > 1 else base
            full = base + parts
            cand = "/".join(full)
            for c in (cand + ".py", cand + "/__init__.py", cand + ".pyi"):
                if c in self.facts:
                    return c, full
            return None, full
        if not parts:
            return None, []
        # sys.path roots the file adds, then its own directory (on sys.path when run as a script)
        for root in self.extra_roots.get(importer, []) + [posixpath.dirname(importer)]:
            cand = "/".join(([root] if root else []) + parts)
            for c in (cand + ".py", cand + "/__init__.py", cand + ".pyi"):
                if c in self.facts:
                    return c, parts
        hits = self.py_mod.get(tuple(parts), [])
        hit = self._closest(importer, hits, lambda c: len(parts) + (1 if c.endswith("/__init__.py") else 0))
        return hit, parts

    def resolve_js(self, importer: str, spec: str) -> str | None:
        if not spec:
            return None
        if spec.startswith(".") or spec.startswith("/"):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec)) if spec.startswith(".") else spec.lstrip("/")
            return self._js_file(base)
        cfg = self._ts_config_for(importer)
        if cfg:
            base_url, paths = cfg
            for pattern, targets in paths.items():
                if pattern.endswith("*"):
                    if not spec.startswith(pattern[:-1]):
                        continue
                    tail = spec[len(pattern) - 1:]
                elif spec == pattern:
                    tail = ""
                else:
                    continue
                for t in targets:
                    hit = self._js_file(posixpath.normpath(posixpath.join(base_url, t.replace("*", tail))))
                    if hit:
                        return hit
            hit = self._js_file(posixpath.normpath(posixpath.join(base_url, spec)))
            if hit:
                return hit
        return None

    def _ts_config_for(self, importer: str):
        d = posixpath.dirname(importer)
        while True:
            if d in self.ts_cfg:
                return self.ts_cfg[d]
            if not d:
                return None
            d = posixpath.dirname(d)

    def _js_file(self, base: str) -> str | None:
        base = base.lstrip("./") if base.startswith("./") else base
        if base in self.paths:
            return base
        stem = re.sub(r"\.(m|c)?js$|\.jsx$", "", base)
        for ext in JS_EXTS:
            if stem + ext in self.paths:
                return stem + ext
        for ext in JS_EXTS:
            if f"{base}/index{ext}" in self.paths:
                return f"{base}/index{ext}"
        return None

    def resolve_generic(self, importer: str, spec: str) -> Binding | None:
        if spec.startswith(".") or "/" in spec or spec.endswith((".h", ".hpp", ".hh")):
            rel = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
            if rel in self.paths:
                return Binding("module", rel)
        parts = [x for x in _SPLIT.split(spec) if x and x not in ("crate", "self", "super", "std", "*")]
        if not parts:
            return None
        for n in (len(parts), len(parts) - 1):
            if n <= 0:
                break
            key = tuple(parts[:n])
            hit = self._closest(importer, self.stem.get(key, []), n)
            if hit:
                return Binding("module", hit) if n == len(parts) else Binding("member", hit, parts[n])
            dirs = self.dir_suffix.get(key, [])
            if dirs and n == len(parts):
                d = self._closest(importer, dirs, n) if len(dirs) > 1 else dirs[0]
                return Binding("dir", d)
        # path-like specs (C includes): match by trailing path segments anywhere in the repo
        hits = [p for p in self.paths if p.endswith("/" + spec) or p == spec]
        if hits:
            return Binding("module", self._closest(importer, hits, len(spec.split("/"))))
        return None

    # --- bindings ------------------------------------------------------------------------------
    def bindings(self, path: str) -> tuple[dict[str, Binding], list[str]]:
        if path in self._bindings:
            return self._bindings[path]
        self._bindings[path] = ({}, [])  # cycle guard
        table: dict[str, Binding] = {}
        stars: list[str] = []
        lang = self.files[path].lang
        for imp in self.facts.get(path, {}).get("imports", []):
            spec = imp["m"]
            if lang == "python":
                target, parts = self.resolve_python(path, spec)
                if imp["alias"]:
                    if target:
                        table[imp["alias"]] = Binding("module", target)
                    continue
                for name, local in imp["names"]:
                    sub, _ = self.resolve_python(path, spec + ("" if spec.endswith(".") else ".") + name) if spec else (None, [])
                    if sub:
                        table[local] = Binding("module", sub)
                    elif target:
                        table[local] = Binding("member", target, name)
                if imp["star"] and target:
                    stars.append(target)
            elif lang in JS_LANGS:
                target = self.resolve_js(path, spec)
                if not target:
                    continue
                if imp["alias"]:
                    table[imp["alias"]] = Binding("module", target)
                for name, local in imp["names"]:
                    table[local] = Binding("member", target, name)
                if imp["star"]:
                    stars.append(target)
            elif lang in SPEC_LANGS:
                r = self.spec_import(path, imp, lang)
                table.update(r.bindings)
                stars.extend(r.stars)
            else:
                b = self.resolve_generic(path, spec)
                if not b:
                    continue
                local = imp["alias"] or (_SPLIT.split(spec.rstrip("/"))[-1] if spec else None)
                if b.kind == "member":
                    table[b.name] = b
                elif local:
                    table[local] = b
                for name, alias in imp["names"]:
                    if b.kind != "dir":
                        table[alias] = Binding("member", b.path, name)
                if imp["star"] and b.kind == "module":
                    stars.append(b.path)
        if lang == "csharp":  # a file sees its own namespace and every enclosing one
            for ns in self.facts.get(path, {}).get("namespaces", []):
                parts = ns.split(".")
                for i in range(len(parts), 0, -1):
                    stars.extend(f for f in self.ns_files.get(".".join(parts[:i]), []) if f != path)
        self._bindings[path] = (table, stars)
        return table, stars

    def export_lookup(self, path: str, name: str, depth: int = 0, seen: set[tuple[str, str]] | None = None) -> tuple[str, str | None] | None:
        key = (path, name)
        if depth == 0:
            if key not in self._export_cache:
                self._export_cache[key] = self._export_lookup(path, name, depth, seen)
            return self._export_cache[key]
        return self._export_lookup(path, name, depth, seen)

    def _export_lookup(self, path: str, name: str, depth: int = 0, seen: set[tuple[str, str]] | None = None) -> tuple[str, str | None] | None:
        """Where does module `path` get its exported `name` from? → (file, qual or None for a submodule)."""
        if depth > 5 or path not in self.facts:
            return None
        seen = set() if seen is None else seen
        key = (path, name)
        if key in seen:
            return None
        seen.add(key)
        if name in self.top.get(path, {}):
            return path, self.top[path][name]
        if name == "default":
            d = self.facts[path].get("default")
            if d and d in self.syms.get(path, {}):
                return path, d
            if d and d in self.top.get(path, {}):  # `export default Name;`
                return path, self.top[path][d]
            exported = [s for s in self.facts[path]["symbols"] if s["p"] is None and s["x"] and s["k"] in CALLABLE]
            if len(exported) == 1:
                return path, exported[0]["q"]
            return path, None
        table, stars = self.bindings(path)
        b = table.get(name)
        if b is not None:
            if b.kind == "module":
                return b.path, None
            if b.kind == "member":
                return self.export_lookup(b.path, b.name, depth + 1, seen)
        for s in stars:
            hit = self.export_lookup(s, name, depth + 1, seen)
            if hit and hit[1]:
                return hit
        return None

    # --- reference ladder ----------------------------------------------------------------------
    def enclosing_class(self, path: str, owner: str) -> str | None:
        q = owner
        syms = self.syms[path]
        while q:
            s = syms.get(q)
            if s is None:
                return None
            if s["k"] in ("class", "interface"):
                return q
            q = s["p"]
        return None

    def member(self, path: str, cls: str, name: str, depth: int = 0) -> tuple[str, str] | None:
        q = self.members[path].get(cls, {}).get(name)
        if q:
            return path, q
        if depth < 4:
            for bp, bq in self.bases.get((path, cls), []):
                hit = self.member(bp, bq, name, depth + 1)
                if hit:
                    return hit
        return None

    def resolve_ref(self, path: str, ref: dict, _depth: int = 0) -> tuple[str, str | None, float, str] | None:
        """→ (file, qual or None, confidence, via)."""
        hit = self._resolve_ref(path, ref, _depth)
        if hit and self.files[path].lang in C_LANGS:
            fp, q = self.c_definition(hit[0], hit[1])
            hit = (fp, q, hit[2], hit[3])
        return hit

    def _resolve_ref(self, path: str, ref: dict, _depth: int) -> tuple[str, str | None, float, str] | None:
        parts = ref["n"].split(".")
        head = parts[0]
        owner = ref["f"]
        lang = self.files[path].lang
        if head in ("self", "this", "cls", "super") and len(parts) >= 2:
            cls = self.enclosing_class(path, owner)
            if cls:
                if head == "super":
                    for bp, bq in self.bases.get((path, cls), []):
                        hit = self.member(bp, bq, parts[1])
                        if hit:
                            return hit[0], hit[1], 0.9, "super"
                    return None
                hit = self.member(path, cls, parts[1])
                if hit:
                    return hit[0], hit[1], 0.9, "self"
            return None
        if head == "?":
            return self._method_guess(path, parts[-1])
        if lang in SPEC_LANGS and len(parts) == 1 and owner:
            # implicit this: a bare call inside a method may be a member of the class or of a base class
            cls = self.enclosing_class(path, owner) or (owner.rsplit(".", 1)[0] if "." in owner else None)
            if cls:
                hit = self.member(path, cls, head)
                if hit and hit[1] != owner:
                    return hit[0], hit[1], 0.85, "self"
        # same-file definition
        if head in self.top[path]:
            q = self.top[path][head]
            if len(parts) > 1:
                m = self.member(path, q, parts[1])
                if m:
                    return m[0], m[1], 0.85, "local"
            return path, q, 0.9, "local"
        table, stars = self.bindings(path)
        # longest dotted prefix that is bound (python `import a.b.c` binds "a.b.c")
        for n in range(len(parts), 0, -1):
            key = ".".join(parts[:n])
            b = table.get(key)
            if b is None:
                continue
            rest = parts[n:]
            if b.kind == "symbol":
                if rest:
                    m = self.member(b.path, b.name, rest[0])
                    if m:
                        return m[0], m[1], 0.9, "import"
                return b.path, b.name, 0.9, "import"
            if b.kind == "member":
                hit = self.export_lookup(b.path, b.name)
                if hit is None:
                    return b.path, None, 0.6, "import"
                fp, q = hit
                if q and rest:
                    m = self.member(fp, q, rest[0])
                    if m:
                        return m[0], m[1], 0.85, "import"
                return fp, q, 0.9, "import"
            if b.kind == "module":
                if not rest:
                    return b.path, None, 0.9, "import"
                hit = self.export_lookup(b.path, rest[0])
                if hit:
                    fp, q = hit
                    if q and len(rest) > 1:
                        m = self.member(fp, q, rest[1])
                        if m:
                            return m[0], m[1], 0.8, "import"
                    return fp, q, 0.85, "import"
                return b.path, None, 0.7, "import"
            if b.kind == "dir" and rest:
                for fp in self.by_dir.get(b.path, []):
                    if rest[0] in self.top.get(fp, {}):
                        q = self.top[fp][rest[0]]
                        if len(rest) > 1:  # pkg.Type.Method — the method may live in another file of the package
                            for mp in self.by_dir.get(b.path, []):
                                m = self.members.get(mp, {}).get(q, {}).get(rest[1])
                                if m:
                                    return mp, m, 0.85, "package"
                        return fp, q, 0.85, "package"
                return None
        for s in stars:
            hit = self.export_lookup(s, head)
            if hit and hit[1]:
                if len(parts) > 1:
                    m = self.member(hit[0], hit[1], parts[1])
                    if m:
                        return m[0], m[1], 0.8, "star"
                return hit[0], hit[1], 0.8, "star"
        if lang in SAME_PACKAGE_LANGS:
            for fp in self.by_dir.get(posixpath.dirname(path), []):
                if fp != path and head in self.top.get(fp, {}):
                    q = self.top[fp][head]
                    if len(parts) > 1:
                        for mp in [fp] + self.by_dir.get(posixpath.dirname(path), []):
                            m = self.members.get(mp, {}).get(q, {}).get(parts[1])
                            if m:
                                return mp, m, 0.65, "package"
                    return fp, q, 0.7, "package"
        if lang in SPEC_LANGS and owner and len(parts) >= 2 and _depth == 0:
            # `store_.save()` inside OrderService::place: the field is declared in a header
            cls = owner.rsplit(".", 1)[0] if "." in owner else None
            ty = self.class_field_type(path, cls, head) if cls else None
            if ty:
                return self.resolve_ref(path, {**ref, "n": ".".join([ty.rsplit(".", 1)[-1]] + parts[1:])}, 1)
        if lang in GLOBAL_UNIQUE_LANGS and len(parts) == 1 and ref["k"] in ("call", "new", "inherit", "implements", "jsx") and len(head) >= 3:
            name = parts[-1] if len(parts) > 1 else head
            hits = [h for h in self.global_names.get(name, []) if h[0] != path]
            if len(hits) == 1:
                return hits[0][0], hits[0][1], 0.5, "unique"
        return None

    def _method_guess(self, path: str, name: str):
        """`foo().bar()` / `x.bar()` with an unknown receiver: accept only a unique method among imported files."""
        table, stars = self.bindings(path)
        targets = {b.path for b in table.values() if b.kind != "dir"} | set(stars) | {path}
        hits = []
        for fp in targets:
            for cls, mem in self.members.get(fp, {}).items():
                if name in mem:
                    hits.append((fp, mem[name]))
        if len(hits) == 1:
            return hits[0][0], hits[0][1], 0.4, "guess"
        return None

    # --- driver ----------------------------------------------------------------------------------
    def run(self) -> Resolution:
        res = Resolution()
        agg: dict[tuple[str, str, str], Edge] = {}

        def add(src, dst, etype, conf, via):
            if src == dst:
                return
            k = (src, dst, etype)
            e = agg.get(k)
            if e:
                e.count += 1
                e.conf = max(e.conf, conf)
            else:
                agg[k] = Edge(src, dst, etype, conf, via)

        # pass 1: inheritance, so self.x can climb to base classes in pass 2
        for path, fx in self.facts.items():
            for ref in fx["refs"]:
                if ref["k"] == "inherit" and ref["f"]:
                    hit = self.resolve_ref(path, ref)
                    if hit and hit[1]:
                        self.bases[(path, ref["f"])].append((hit[0], hit[1]))

        for path, fx in self.facts.items():
            entry = self.files[path]
            lang = entry.lang
            # file-level import edges
            for imp in fx["imports"]:
                target = None
                if lang == "python":
                    target, parts = self.resolve_python(path, imp["m"])
                    if not target and imp["names"] and imp["m"]:
                        for name, _ in imp["names"]:
                            sub, _ = self.resolve_python(path, imp["m"].rstrip(".") + "." + name if not imp["m"].endswith(".") else imp["m"] + name)
                            if sub:
                                add(path, sub, "tests" if entry.kind == "test" else "imports", 1.0, "import")
                    if not target and not imp["m"].startswith(".") and parts and parts[0] not in self.py_dir_names:
                        res.externals[path].add(parts[0])
                elif lang in JS_LANGS:
                    target = self.resolve_js(path, imp["m"])
                    if not target and imp["m"] and not imp["m"].startswith("."):
                        spec = imp["m"]
                        res.externals[path].add("/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0])
                elif lang in SPEC_LANGS:
                    r = self.spec_import(path, imp, lang)
                    t_kind = lambda t: self.files[t].kind if t in self.files else "source"
                    for t in dict.fromkeys(r.files):
                        add(path, t, "tests" if entry.kind == "test" and t_kind(t) != "test" else "imports", 1.0, "import")
                    if r.external:
                        res.externals[path].add(r.external)
                    res.stats["imports"] += 1
                    res.stats["imports_resolved"] += 1 if r.resolved else 0
                    continue
                else:
                    b = self.resolve_generic(path, imp["m"])
                    if b is not None:
                        target = b.path if b.kind != "dir" else None
                        if b.kind == "dir":
                            for fp in self.by_dir.get(b.path, [])[:1]:
                                target = fp
                    elif imp["m"]:
                        res.externals[path].add(_SPLIT.split(imp["m"])[0])
                if target:
                    t_kind = self.files[target].kind if target in self.files else "source"
                    etype = "tests" if entry.kind == "test" and t_kind != "test" else "imports"
                    add(path, target, etype, 1.0, "import")
                res.stats["imports"] += 1
                res.stats["imports_resolved"] += 1 if target else 0
            for link in fx.get("links", []):
                t = posixpath.normpath(posixpath.join(posixpath.dirname(path), link["m"]))
                if t in self.paths:
                    add(path, t, "references", 1.0, "link")
                elif t.rstrip("/") in self.by_dir:
                    first = sorted(self.by_dir[t.rstrip("/")])[:1]
                    for fp in first:
                        add(path, fp, "references", 0.8, "link")
            # symbol-level edges
            for ref in fx["refs"]:
                res.stats["refs"] += 1
                hit = self.resolve_ref(path, ref)
                if not hit:
                    continue
                fp, q, conf, via = hit
                res.stats["refs_resolved"] += 1
                src = f"{path}#{ref['f']}" if ref["f"] else path
                dst = f"{fp}#{q}" if q else fp
                etype = EDGE_OF.get(ref["k"], "references")
                if etype == "calls" and q and self.syms.get(fp, {}).get(q, {}).get("k") == "class":
                    etype = "instantiates"
                if etype == "calls" and q and self.syms.get(fp, {}).get(q, {}).get("k") not in CALLABLE:
                    etype = "references"
                add(src, dst, etype, conf, via)
        res.edges = list(agg.values())
        return res
