"""Thin helpers around tree-sitter-language-pack (grammars are downloaded on first use and cached)."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=None)
def parser_for(lang: str):
    import tree_sitter_language_pack as tslp
    return tslp.get_parser(lang)


@lru_cache(maxsize=None)
def language_for(lang: str):
    import tree_sitter_language_pack as tslp
    return tslp.get_language(lang)


def text_of(node, src: bytes) -> str:
    if node is None:
        return ""
    return src[node.start_byte: node.end_byte].decode("utf-8", "replace")


def prefetch(langs) -> list[str]:
    """Download grammars up front so parallel workers don't race on the cache."""
    failed = []
    for lang in sorted(set(langs)):
        try:
            language_for(lang)
        except Exception:
            failed.append(lang)
    return failed
