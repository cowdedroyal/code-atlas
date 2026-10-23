from code_atlas.highlight import has_browser_grammar, source_tokens


def test_lazy_catalog_covers_primary_outline_and_common_tier2_languages():
    for lang in ("python", "javascript", "typescript", "tsx", "go", "java", "rust", "c", "cpp", "csharp",
                 "vue", "svelte", "markdown", "yaml", "toml", "json", "sql", "dockerfile", "makefile",
                 "kotlin", "ruby", "php", "swift", "scala", "lua", "powershell", "commonlisp"):
        assert has_browser_grammar(lang), lang


def test_tree_sitter_fallback_preserves_unicode_and_highlights_gomod():
    source = 'module example.com/中文\n\ngo 1.23\n// 中文注释\nrequire example.org/pkg v1.2.3\n'
    assert not has_browser_grammar("gomod")
    rows = source_tokens(source, "gomod")
    assert "\n".join("".join(t["text"] for t in row) for row in rows) == source
    assert rows[0][0] == {"text": "module", "className": "hljs-keyword"}
    assert rows[3][0] == {"text": "// 中文注释", "className": "hljs-comment"}
