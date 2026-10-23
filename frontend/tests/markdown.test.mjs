import assert from "node:assert/strict";
import test from "node:test";
import { parseMarkdown } from "../src/markdownEngine.ts";
import { decodeMarkdownText, headingSlug, isMarkdown, repositoryLink, safeMarkdownLink } from "../src/markdownClient.ts";

test("GFM parses headings, lists, tables, quotes, links and fenced code", () => {
  const source = '# Demo\n\n**bold** and `code`\n\n- [x] ready\n\n> quote\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\n[read](README.md)\n\n```python\ndef hello():\n    return 42\n```\n';
  const tokens = parseMarkdown(source);
  assert(tokens.some(t => t.type === "heading" && t.depth === 1));
  assert(tokens.some(t => t.type === "list" && t.items[0].checked));
  assert(tokens.some(t => t.type === "table" && t.rows.length === 1));
  assert(tokens.some(t => t.type === "blockquote"));
  assert(tokens.some(t => t.type === "code" && t.lang === "python" && t.text === "def hello():\n    return 42"));
  assert.strictEqual(parseMarkdown(source), tokens);
});
test("source HTML remains tokens and unsafe link schemes are rejected", () => {
  assert(parseMarkdown('<script>alert(1)</script>')[0].type === "html");
  for (const href of ['javascript:alert(1)', 'java\nscript:alert(1)', 'javascript&#58;alert(1)', 'data:text/html,x', 'file:///x', '//example.com', '\\\\example.com']) {
    assert.equal(safeMarkdownLink(href), null, href);
  }
  assert.equal(safeMarkdownLink('https://example.com/?a=1&amp;b=2'), 'https://example.com/?a=1&b=2');
  assert.equal(safeMarkdownLink('README.md#usage'), 'README.md#usage');
});
test("repository paths and unicode anchors resolve without escaping the repo", () => {
  assert.equal(repositoryLink('../README.md#usage', 'docs/design.md'), 'README.md');
  assert.equal(repositoryLink('./sub%20folder/doc.md', 'docs/design.md'), 'docs/sub folder/doc.md');
  assert.equal(repositoryLink('../../private.txt', 'docs/design.md'), null);
  assert.equal(headingSlug('标题 Name & value!'), '标题-name--value');
  assert.equal(decodeMarkdownText('&amp; &#x4e2d; &#34;'), '& 中 "');
  assert.equal(isMarkdown(null, 'ReadMe.MD'), true);
});
test("parsing a long document retains every block for batched rendering", () => {
  const source = Array.from({ length: 500 }, (_, i) => `## Heading ${i}\n\nparagraph ${i}\n`).join('\n');
  assert.equal(parseMarkdown(source).filter(t => t.type === "heading").length, 500);
});
