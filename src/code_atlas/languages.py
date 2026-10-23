"""Which parser handles a file, and how deep the extraction goes.

Tier 1: hand-written AST walkers with precise imports, scopes and calls (Python, JS/TS, and a spec-driven
walker for Go, Java, Rust, C, C++, C#).
Tier 2: any grammar in tree-sitter-language-pack — structure via `process()`, calls via the grammar's tags.scm.
Doc / data formats get lightweight outline extractors (headings, top-level keys, tables).
Everything else is a file node without symbols.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

TIER1 = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
    ".cs": "csharp",
    ".c": "c",
    # C++ grammar for every header: it parses C headers too, and C++ projects mostly use .h
    ".h": "cpp", ".hh": "cpp", ".hpp": "cpp", ".hxx": "cpp", ".h++": "cpp", ".inl": "cpp",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".c++": "cpp",
}
SPEC_LANGS = {"go", "java", "rust", "csharp", "c", "cpp"}

# Single-file components: the <script> block is parsed with the tier-1 walker.
SFC = {".vue": "vue", ".svelte": "svelte"}

OUTLINE = {
    ".md": "markdown", ".mdx": "markdown", ".markdown": "markdown",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".json": "json", ".jsonc": "json",
    ".sql": "sql",
}
OUTLINE_NAMES = {"dockerfile": "dockerfile", "containerfile": "dockerfile", "makefile": "makefile"}

# Extensions tree-sitter would claim but that carry no code structure worth parsing.
PLAIN = {".txt", ".rst", ".adoc", ".csv", ".tsv", ".log", ".ini", ".cfg", ".conf", ".env", ".example",
         ".lock", ".svg", ".xml", ".gitignore", ".gitkeep", ".dockerignore", ".editorconfig"}


@dataclass(frozen=True)
class LangInfo:
    lang: str | None  # tree-sitter language name, or an outline format name
    tier: str         # "1" | "2" | "outline" | "none"


def detect(path: str) -> LangInfo:
    p = PurePosixPath(path)
    ext = p.suffix.lower()
    name = p.name.lower()
    if ext in TIER1:
        return LangInfo(TIER1[ext], "1")
    if ext in SFC:
        return LangInfo(SFC[ext], "1")
    if ext in OUTLINE:
        return LangInfo(OUTLINE[ext], "outline")
    if name in OUTLINE_NAMES or name.startswith("dockerfile"):
        return LangInfo(OUTLINE_NAMES.get(name, "dockerfile"), "outline")
    if ext in PLAIN or not ext:
        return LangInfo(None, "none")
    try:
        import tree_sitter_language_pack as tslp
        lang = tslp.detect_language_from_path(path)
    except Exception:
        lang = None
    if lang and lang not in ("vimdoc", "html", "css", "scss", "markdown", "json", "yaml", "toml"):
        return LangInfo(lang, "2")
    return LangInfo(lang, "none")
