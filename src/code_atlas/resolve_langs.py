"""Import resolution for the spec-walker languages: Go, Java, C#, Rust, C and C++.

Each language finds the files an import refers to in its own way:

    Go      import path minus the module path from the nearest go.mod → a package directory
    Java    a.b.C → the file a/b/C.java; a.b.* → every file of that package directory
    C#      using A.B → every file that declares namespace A.B (and a file sees its own namespaces)
    Rust    crate::/super::/self:: paths over the crate's module tree (src/a/b.rs or src/a/b/mod.rs)
    C/C++   #include "x.h" relative to the file, then anywhere in the repo by path suffix

C and C++ also redirect a call that lands on a header declaration to the one definition with the same
qualified name in a source file, so the map points at the code that runs.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field

HEADER_EXT = (".h", ".hh", ".hpp", ".hxx", ".h++", ".inl")
C_LANGS = {"c", "cpp"}


@dataclass
class ImportResult:
    bindings: dict = field(default_factory=dict)   # local name → Binding
    stars: list[str] = field(default_factory=list)  # files whose top-level names become visible
    files: list[str] = field(default_factory=list)  # file-level import edges
    external: str | None = None
    resolved: bool = False


class LangImports:
    """Mixed into Resolver; uses its files/facts/paths/by_dir/stem/top/members/syms indexes."""

    def _lang_indexes(self):
        # Go modules: directory → module path
        self.go_mods: dict[str, str] = {}
        for p in self.paths:
            if posixpath.basename(p) == "go.mod":
                try:
                    text = (self.root / p).read_text("utf-8", "replace")
                except OSError:
                    continue
                m = re.search(r"^module\s+(\S+)", text, re.M)
                if m:
                    self.go_mods[posixpath.dirname(p)] = m.group(1)
        # C# namespaces → files that declare them
        self.ns_files: dict[str, list[str]] = defaultdict(list)
        for p, fx in self.facts.items():
            for ns in fx.get("namespaces", []):
                self.ns_files[ns].append(p)
        # Rust crates: directory of each Cargo.toml
        self.crates = sorted((posixpath.dirname(p) for p in self.paths if posixpath.basename(p) == "Cargo.toml"),
                             key=len, reverse=True)
        # C/C++: definitions in source files, by qualified name, for header → definition redirects
        self.c_defs: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for p, fx in self.facts.items():
            if self.files[p].lang in C_LANGS and not p.endswith(HEADER_EXT):
                for s in fx["symbols"]:
                    if s["k"] in ("function", "method"):
                        self.c_defs[s["q"]].append((p, s["q"]))

    # --- helpers --------------------------------------------------------------------------------------
    def _nearest(self, path: str, roots) -> str | None:
        d = posixpath.dirname(path)
        while True:
            if d in roots:
                return d
            if not d:
                return None
            d = posixpath.dirname(d)

    def _rust_src(self, path: str) -> str | None:
        """Module root of the crate containing `path`: <crate>/src, or the crate dir itself."""
        crate = next((c for c in self.crates if c == "" or path.startswith(c + "/")), None)
        if crate is None:
            return None
        src = posixpath.join(crate, "src")
        return src if path.startswith(src + "/") else crate

    def _rust_module_path(self, path: str, src: str) -> list[str]:
        rel = path[len(src) + 1:] if src and path.startswith(src + "/") else path
        parts = rel[:-3].split("/") if rel.endswith(".rs") else rel.split("/")
        if parts[-1] in ("mod", "main", "lib"):
            parts = parts[:-1]
        if parts and parts[0] == "bin":
            return []
        return parts

    def _rust_file(self, src: str, segs: list[str]) -> str | None:
        base = posixpath.join(src, *segs) if segs else src
        cands = [base + ".rs", posixpath.join(base, "mod.rs")] if segs else \
            [posixpath.join(src, "lib.rs"), posixpath.join(src, "main.rs")]
        return next((c for c in cands if c in self.facts), None)

    # --- per-language imports ------------------------------------------------------------------------
    def spec_import(self, path: str, imp: dict, lang: str):
        from .resolve import Binding
        r = ImportResult()
        spec = imp["m"]
        if lang == "go":
            root = self._nearest(path, self.go_mods)
            mod = self.go_mods.get(root) if root is not None else None
            if mod and (spec == mod or spec.startswith(mod + "/")):
                d = posixpath.normpath(posixpath.join(root, spec[len(mod):].lstrip("/"))) if root else spec[len(mod):].lstrip("/")
                d = "" if d == "." else d
                if d in self.by_dir:
                    r.resolved = True
                    files = self.by_dir[d]
                    if imp["star"]:
                        r.stars += files
                    elif imp["alias"]:
                        r.bindings[imp["alias"]] = Binding("dir", d)
                    return r
            r.external = spec.split("/")[0] if "." not in spec.split("/")[0] else "/".join(spec.split("/")[:3])
            return r
        if lang == "java":
            parts = spec.split(".")
            if imp["star"]:
                dirs = self.dir_suffix.get(tuple(parts), [])
                if dirs:
                    r.resolved = True
                    for d in dirs[:1]:
                        r.stars += self.by_dir[d]
                    return r
            else:
                hits = [h for h in self.stem.get(tuple(parts), []) if self.files[h].lang == "java"]
                if hits:
                    target = hits[0] if len(hits) == 1 else self._closest(path, hits, len(parts))
                    if target:
                        r.resolved = True
                        r.files.append(target)
                        cls = parts[-1]
                        if imp["names"]:  # import static a.b.C.m
                            for name, local in imp["names"]:
                                q = self.members[target].get(cls, {}).get(name)
                                if q:
                                    r.bindings[local] = Binding("symbol", target, q)
                        elif cls in self.top.get(target, {}):
                            r.bindings[imp["alias"] or cls] = Binding("symbol", target, self.top[target][cls])
                        return r
            r.external = ".".join(parts[:2])
            return r
        if lang == "csharp":
            files = self.ns_files.get(spec, [])
            if files:
                r.resolved = True
                if imp["alias"]:
                    for f in files:
                        r.bindings.setdefault(imp["alias"], Binding("dir", posixpath.dirname(f)))
                else:
                    r.stars += [f for f in files if f != path]
                return r
            r.external = spec.split(".")[0]
            return r
        if lang == "rust":
            src = self._rust_src(path)
            if src is None:
                r.external = spec.split("::")[0]
                return r
            mp = self._rust_module_path(path, src)
            segs = [s for s in spec.split("::") if s]
            if not segs:
                return r
            if segs[0] == "crate":
                absolute = segs[1:]
            elif segs[0] == "self":
                absolute = mp + segs[1:]
            elif segs[0] == "super":
                up = mp
                while segs and segs[0] == "super":
                    up, segs = up[:-1], segs[1:]
                absolute = up + segs
            elif self._rust_file(src, mp + segs[:1]):
                absolute = mp + segs
            elif self._rust_file(src, segs[:1]):
                absolute = segs
            else:
                r.external = segs[0]
                return r
            modfile = self._rust_file(src, absolute)
            if imp.get("mod"):  # `mod x;`
                if modfile:
                    r.resolved = True
                    r.files.append(modfile)
                    r.bindings[imp["alias"]] = Binding("module", modfile)
                return r
            if modfile is None:
                return r
            r.resolved = True
            r.files.append(modfile)
            if imp["star"]:
                r.stars.append(modfile)
            for name, local in imp["names"]:
                sub = self._rust_file(src, absolute + [name])
                if sub:
                    r.files.append(sub)
                    r.bindings[local] = Binding("module", sub)
                else:
                    r.bindings[local] = Binding("member", modfile, name)
            return r
        if lang in C_LANGS:
            if imp.get("system"):
                r.external = spec.split("/")[0].split(".")[0]
                return r
            rel = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
            target = rel if rel in self.facts else None
            if target is None:
                hits = [p for p in self.facts if p == spec or p.endswith("/" + spec)]
                if hits:  # longest shared directory prefix with the includer wins
                    target = max(hits, key=lambda h: (len(posixpath.commonprefix([h, path])), -len(h)))
            if target:
                r.resolved = True
                r.files.append(target)
                r.stars.append(target)
            else:
                r.external = spec
            return r
        return r

    # --- post-processing --------------------------------------------------------------------------------
    def c_definition(self, fp: str, q: str | None) -> tuple[str, str | None]:
        """A header declaration → its single definition in a source file, if there is exactly one."""
        if q and fp.endswith(HEADER_EXT):
            defs = self.c_defs.get(q, [])
            if len(defs) == 1:
                return defs[0]
        return fp, q

    def class_field_type(self, path: str, cls: str, name: str) -> str | None:
        """Type of field `name` of class `cls`, looking in this file and the files it can see (C++ headers)."""
        table, stars = self.bindings(path)
        for fp in [path] + list(stars):
            q = self.members.get(fp, {}).get(cls, {}).get(name)
            if q:
                ty = self.syms.get(fp, {}).get(q, {}).get("ty")
                if ty:
                    return ty
        return None
