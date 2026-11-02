import assert from "node:assert/strict";
import test from "node:test";
import { repositoryLink, safeMarkdownLink } from "../src/markdownClient.ts";

test("angle-bracket destinations with spaces stay valid links", () => {
  // marked hands these to us raw: [x](<my file.md>) -> "my file.md"
  assert.equal(safeMarkdownLink("my file.md"), "my file.md");
  assert.equal(safeMarkdownLink("docs/Design Doc.md"), "docs/Design Doc.md");
  assert.equal(safeMarkdownLink("https://example.com/a b?x=1"), "https://example.com/a b?x=1");
  assert.equal(safeMarkdownLink("  README.md  "), "README.md");
  // control characters and unsafe schemes are still rejected
  for (const href of ["a\tb.md", "a\nb.md", "a\u0000b.md", "java\nscript:alert(1)", "javascript:alert(1)"])
    assert.equal(safeMarkdownLink(href), null, JSON.stringify(href));
});

test("repository-absolute links resolve without a base path, relative ones do not", () => {
  assert.equal(repositoryLink("/src/a.py", undefined), "src/a.py");
  assert.equal(repositoryLink("/docs/Design Doc.md", undefined), "docs/Design Doc.md");
  assert.equal(repositoryLink("/src/a.py#L4", "docs/n.md"), "src/a.py");
  assert.equal(repositoryLink("/../../etc/passwd", undefined), null);
  assert.equal(repositoryLink("a.py", undefined), null);
  assert.equal(repositoryLink("../README.md", undefined), null);
  assert.equal(repositoryLink("#usage", undefined), null);
});
