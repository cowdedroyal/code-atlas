import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const src = await readFile(new URL("../src/Explainers.tsx", import.meta.url), "utf8");

test("the viewer does not inject a script into untrusted card HTML", () => {
  assert.ok(!src.includes("ESCAPE_SHIM"));
  assert.ok(!src.includes("withEscapeShim"));
});

test("the host still closes the reader on the kit's atlas:close message", () => {
  assert.match(src, /type === "atlas:close"[\s\S]{0,80}?close\(\)/);
});
