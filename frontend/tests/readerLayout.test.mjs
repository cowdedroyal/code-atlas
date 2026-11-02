import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const declarations = (selector) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = css.match(new RegExp(escaped + "\\s*\\{([^}]*)\\}"));
  assert.ok(m, `missing rule ${selector}`);
  return m[1];
};

test("the reader body reserves its scrollbar gutter so the card height cannot toggle the text scale", () => {
  // Without a stable gutter, gaining the scrollbar narrows the body, which lowers --ex-scale, which shrinks the card,
  // which removes the scrollbar again: a resize loop that re-lays out the card every frame.
  assert.match(declarations(".reader-body"), /scrollbar-gutter:\s*stable/);
  // Cinema does not scroll; a stable gutter there would only leave a blank strip.
  assert.match(declarations(".reader.cinema .reader-body"), /scrollbar-gutter:\s*auto/);
});

test("an active grip drag disables the card frame so pointer moves stay in the host page", () => {
  assert.match(declarations(".reader.dragging .reader-frame"), /pointer-events:\s*none/);
});
