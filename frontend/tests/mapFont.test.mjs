import assert from "node:assert/strict";
import test from "node:test";
import { mapFontRenderer } from "../src/mapFont.ts";

test("j preserves its measured left overhang, descender and advance", () => {
  const previous = globalThis.document;
  const calls = [];
  const canvas = { width: 0, height: 0 };
  const ctx = {
    measureText: () => ({ width: 16.71875, actualBoundingBoxLeft: 6, actualBoundingBoxRight: 14,
      actualBoundingBoxAscent: 48, actualBoundingBoxDescent: 16, fontBoundingBoxAscent: 62, fontBoundingBoxDescent: 16 }),
    fillText: (...args) => calls.push(args),
    getImageData: (_x, _y, width, height) => ({ width, height }),
  };
  canvas.getContext = () => ctx;
  globalThis.document = { createElement: () => canvas };
  try {
    const renderer = mapFontRenderer({ fontSize: 64, fontWeight: 600, fontFamily: "Segoe UI" });
    assert.deepEqual(renderer.measure("j"), { left: -6, width: 20, advance: 16.71875, ascent: 48, descent: 16 });
    const glyph = renderer.draw("j");
    assert.deepEqual(calls, [["j", 6, 48]]);
    assert.equal(glyph.data.width, 20);
    assert.equal(glyph.data.height, 64);
    assert.equal(glyph.left, 6);
    assert.equal(renderer.measure().ascent, 62);
  } finally { globalThis.document = previous; }
});
