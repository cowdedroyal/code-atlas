"""Import precise references from a SCIP index (scip-python, scip-typescript, scip-java, rust-analyzer, …).

The heuristic resolver is good but guesses at receivers it cannot type. When a compiler-grade indexer has
been run, `codeatlas scan --scip index.scip` replaces the symbol-level edges of every file the index covers
with the references the indexer found; import and inheritance edges stay. The protobuf wire format is decoded
directly — only the handful of fields needed — so no protobuf dependency is required.

    Index            2: documents
    Document         1: relative_path  2: occurrences  4: language
    Occurrence       1: range (deprecated packed int32: line, char[, end line], end char)  2: symbol
                     3: symbol_roles   8: SingleLineRange / 9: MultiLineRange (field 1 = line; preferred)
    symbol_roles     bit 0 = Definition
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

DEFINITION = 0x1


@dataclass
class Occurrence:
    line: int          # 0-based
    symbol: str
    roles: int


@dataclass
class Document:
    path: str
    language: str = ""
    occurrences: list[Occurrence] = field(default_factory=list)


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes):
    """Yield (field number, wire type, value) — value is an int or a bytes slice."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(buf, i)
        elif wt == 1:
            v, i = buf[i:i + 8], i + 8
        elif wt == 2:
            ln, i = _varint(buf, i)
            v, i = buf[i:i + ln], i + ln
        elif wt == 5:
            v, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
        yield num, wt, v


def _packed(v, wt) -> list[int]:
    if wt == 0:
        return [v]
    out, i = [], 0
    while i < len(v):
        x, i = _varint(v, i)
        out.append(x)
    return out


def load(path) -> list[Document]:
    data = open(path, "rb").read()
    docs = []
    for num, wt, v in _fields(data):
        if num != 2 or wt != 2:
            continue
        doc = Document("")
        for dn, dwt, dv in _fields(v):
            if dn == 1:
                doc.path = bytes(dv).decode("utf-8")
            elif dn == 4:
                doc.language = bytes(dv).decode("utf-8")
            elif dn == 2:
                rng, typed_line, sym, roles = [], None, "", 0
                for on, owt, ov in _fields(dv):
                    if on == 1:
                        rng += _packed(ov, owt)
                    elif on in (8, 9):  # SingleLineRange / MultiLineRange: field 1 is the (start) line
                        typed_line = next((x for n_, _, x in _fields(ov) if n_ == 1), 0)  # proto3 omits 0
                    elif on == 2:
                        sym = bytes(ov).decode("utf-8")
                    elif on == 3:
                        roles = ov
                line = typed_line if typed_line is not None else (rng[0] if rng else None)
                if line is not None and sym and not sym.startswith("local "):
                    doc.occurrences.append(Occurrence(line, sym, roles))
        docs.append(doc)
    return docs


_DESCRIPTOR = re.compile(r"(`[^`]+`|[A-Za-z_$][\w$]*)(?:\([^)]*\))?[#./:!]?\s*$")


def symbol_name(symbol: str) -> str | None:
    """Last descriptor of a SCIP symbol: `scip-python python pkg 0.1 mod/Cls#run().` → `run`."""
    m = _DESCRIPTOR.search(symbol.rstrip())
    return m.group(1).strip("`") if m else None


class _Spans:
    """Innermost-symbol lookup by line for one file."""

    def __init__(self, symbols: list[dict]):
        self.syms = sorted(symbols, key=lambda s: (s["start"], -s["end"]))
        self.starts = [s["start"] for s in self.syms]

    def innermost(self, line1: int, name: str | None = None) -> dict | None:
        cands = [s for s in self.syms[: bisect_right(self.starts, line1)] if s["start"] <= line1 <= s["end"]]
        size = lambda s: s["end"] - s["start"]
        if name is not None:  # a definition: prefer our symbol with the indexer's name, starting on this line
            named = [s for s in cands if s["name"] == name]
            exact = [s for s in named if s["start"] == line1]
            if exact or named:
                return min(exact or named, key=size)
            return None
        return min(cands, key=size) if cands else None


def edges(docs: list[Document], symbols: list[dict], mapped: set[str]) -> tuple[list[tuple], set[str]]:
    """→ ([(src, dst, type, count)], files covered by the index)."""
    by_file: dict[str, list[dict]] = {}
    for s in symbols:
        by_file.setdefault(s["path"], []).append(s)
    spans = {p: _Spans(v) for p, v in by_file.items()}
    kind_of = {s["id"]: s["kind"] for s in symbols}
    covered = {d.path for d in docs if d.path in mapped}
    # where each SCIP symbol is defined, mapped to our node (parameters, locals… have no node and are skipped)
    target: dict[str, str] = {}
    for d in docs:
        if d.path not in spans:
            continue
        for o in d.occurrences:
            if o.roles & DEFINITION and o.symbol not in target:
                s = spans[d.path].innermost(o.line + 1, symbol_name(o.symbol) or "")
                if s is not None:
                    target[o.symbol] = s["id"]
    counts: dict[tuple[str, str, str], int] = {}
    for d in docs:
        if d.path not in mapped:
            continue
        sp = spans.get(d.path)
        for o in d.occurrences:
            if o.roles & DEFINITION or o.symbol not in target:
                continue
            src_sym = sp.innermost(o.line + 1) if sp else None
            src = src_sym["id"] if src_sym is not None else d.path
            dst = target[o.symbol]
            if src == dst or src.startswith(dst + ".") or dst.startswith(src + "."):
                continue
            kind = kind_of.get(dst)
            etype = "calls" if kind in ("function", "method") else "references"
            counts[(src, dst, etype)] = counts.get((src, dst, etype), 0) + 1
    return [(s, d, t, c) for (s, d, t), c in counts.items()], covered
