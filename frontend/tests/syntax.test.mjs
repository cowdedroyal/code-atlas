import assert from "node:assert/strict";
import test from "node:test";
import { codeLanguage, codeWindow, highlightCode, MAX_HIGHLIGHT_CHARS } from "../src/syntax.ts";
import { htmlTokens, tokenize } from "../src/syntaxEngine.ts";
import { grammarNames } from "../src/languageNames.ts";

const plain = rows => rows.map(row => row.map(t => t.text).join("")).join("\n");
test("language aliases and extensions choose explicit grammars", () => {
  assert.equal(codeLanguage("tsx", "App.tsx"), "typescript");
  assert.equal(codeLanguage(null, "package.json"), "json");
  assert.equal(codeLanguage("toml", "pyproject.toml"), "ini");
});
test("rendered excerpts remain bounded even for a huge symbol", () => {
  const source = Array.from({ length: 2000 }, (_, i) => `line ${i + 1}`).join("\n");
  const excerpt = codeWindow(source, 100, 1800);
  assert.equal(excerpt.lo, 100);
  assert.equal(excerpt.hi, 499);
  assert.equal(excerpt.lines.length, 400);
  assert.equal(excerpt.prefix.split("\n").length, 499);
  assert.equal(excerpt.remaining, 1301);
  assert.equal(codeWindow(source, 100, 1800, 800).hi, 899);
});
test("symbol excerpts include exactly their inclusive range, with no neighboring declaration", () => {
  const source = 'const before = 1;\ninterface Props {\n  title: string\n}\nclass Panel {\n  render() {}\n}\nconst after = 2;';
  assert.deepEqual(codeWindow(source, 2, 4).lines, ['interface Props {', '  title: string', '}']);
  assert.equal(codeWindow(source, 2, 4).remaining, 0);
  assert.deepEqual(codeWindow(source, 5, 7).lines, ['class Panel {', '  render() {}', '}']);
});
test("HTML escaping and multiline nested scopes preserve source without injecting markup", () => {
  const source = `<script>alert('x')</script> &amp; \"hi\"\n\n`;
  const escaped = '<span class="hljs-string">&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; &amp;amp; &quot;hi&quot;\n\n</span>';
  const rows = htmlTokens(escaped);
  assert.equal(plain(rows), source);
  assert.equal(rows[0][0].className, "hljs-string");
  const nested = htmlTokens('<span class="hljs-string">a<span class="hljs-subst">b\nc</span>d</span>');
  assert.equal(plain(nested), "ab\ncd");
  assert.equal(nested[1][0].className, "hljs-string hljs-subst");
});
test("Python multiline string context survives starting an excerpt mid-string", async () => {
  const source = 'def example():\n    """first\n    second\n    third"""\n    return 42\n';
  const rows = await tokenize(source, "python", 3);
  assert.equal(plain(rows), source.split("\n").slice(2).join("\n"));
  assert(rows[0].some(t => t.className?.includes("hljs-string")));
  assert(rows[2].some(t => t.className?.includes("hljs-keyword")));
});
test("all supported grammars retain exact source text", async () => {
  for (const lang of grammarNames) {
    const source = `// comment 中文\nkey = "<b>&amp;'\\n";\n\n`;
    assert.equal(plain(await tokenize(source, lang, 1)), source, lang);
  }
});
test("Vue/Svelte TypeScript scripts and TSX markup load embedded language grammars", async () => {
  for (const lang of ["vue", "svelte"]) {
    const source = '<script lang="ts">const count: number = 42;</script>\n<style>.root { color: red; }</style>\n<div>{count}</div>';
    const rows = await tokenize(source, lang, 1);
    assert.equal(plain(rows), source);
    assert(rows[0].some(t => t.text === "const" && t.className?.includes("hljs-keyword")), lang);
    assert(rows[0].some(t => t.text === "42" && t.className?.includes("hljs-number")), lang);
  }
  const rows = await tokenize('const view = <button title="hello"/>;', "typescript", 1);
  assert(rows[0].some(t => t.text === "button" && t.className?.includes("hljs-name")));
});
test("oversized or unsupported code safely uses plain text", async () => {
  assert.equal(await highlightCode("x".repeat(MAX_HIGHLIGHT_CHARS + 1), "python", 1), null);
  assert.equal(await tokenize("hello", "unsupported", 1), null);
});
