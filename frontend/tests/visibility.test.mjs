import assert from "node:assert/strict";
import test from "node:test";
import { focusedRegionEdges, refAtLevel, regionAtPoint, relationTouchesNode, visibleFiles } from "../src/visibility.ts";

const graph = {
  regions: [{ parent: null, box: [0, 0, 1000, 1000] }, { parent: 0, box: [0, 0, 300, 300] }],
  files: [{ region: 1, box: [10, 10, 20, 20] }, { region: 1, box: [900, 900, 20, 20] }],
};
const view = [0, 0, 400, 400];

test("unlocked zoom progresses through regions, files and symbols", () => {
  assert.deepEqual(visibleFiles(graph, view, 0.1, null), { level: 0, dotFiles: [], boxFiles: [] });
  assert.deepEqual(visibleFiles(graph, view, 1, null), { level: 1, dotFiles: [0], boxFiles: [] });
  assert.deepEqual(visibleFiles(graph, view, 4, null), { level: 2, dotFiles: [], boxFiles: [0] });
});

for (const level of [0, 1, 2]) {
  test(`L${level} stays locked across zoom thresholds and culls offscreen files`, () => {
    for (const scale of [0.01, 0.1, 1, 4, 100]) {
      assert.deepEqual(visibleFiles(graph, view, scale, { level, zoom: 0 }), {
        level, dotFiles: level === 1 ? [0] : [], boxFiles: level === 2 ? [0] : [],
      });
    }
    assert.equal(visibleFiles(graph, [2000, 2000, 100, 100], 4, { level, zoom: 0 }).level, level);
  });
}

test("unlock immediately restores the automatic zoom level", () => {
  assert.equal(visibleFiles(graph, view, 4, { level: 0, zoom: 0 }).level, 0);
  assert.equal(visibleFiles(graph, view, 4, null).level, 2);
});

test("relations accept only endpoints at the current level, including mixed endpoint pairs", () => {
  const refs = [{ t: "region", i: 1 }, { t: "file", i: 0 }, { t: "symbol", id: "a#f", file: 0 }];
  for (const level of [0, 1, 2]) {
    for (const [a, src] of refs.entries()) for (const [b, dst] of refs.entries()) {
      assert.equal(refAtLevel(src, level) && refAtLevel(dst, level), a === level && b === level);
    }
  }
});

test("root files use their own box as the dot threshold", () => {
  const rootFiles = { ...graph, files: [{ region: 0, box: [10, 10, 60, 60] }] };
  assert.equal(visibleFiles(rootFiles, view, 1, null).level, 0);
  assert.equal(visibleFiles(rootFiles, view, 1.16, null).level, 0);
  assert.equal(visibleFiles(rootFiles, view, 1.17, null).level, 2);
});

test("L0 focus includes incoming and outgoing edges, even to small/offscreen neighbors", () => {
  const graph = { regions: [{ parent: null }, { parent: 0 }, { parent: 0 }, { parent: 0 }],
    region_edges: [[1, 2, 10], [3, 1, 2], [2, 3, 20]] };
  assert.deepEqual(focusedRegionEdges(graph, new Set([1]), 1), [[1, 2, 10], [3, 1, 2]]);
  assert.deepEqual(focusedRegionEdges(graph, new Set([1, 2, 3]), null), [[2, 3, 20], [1, 2, 10], [3, 1, 2]]);
  assert.deepEqual(focusedRegionEdges(graph, new Set([1, 2, 3]), 0), []);
});

test("semantic focus keeps only relations touching the active node", () => {
  const rel = (src, dst) => ({ src, dst });
  const r1 = { t: "region", i: 1 }, f1 = { t: "file", i: 1 }, f2 = { t: "file", i: 2 };
  const sym = { t: "symbol", id: "a#F.m", file: 1 };
  assert(relationTouchesNode(rel({ t: "region", i: 1 }, { t: "region", i: 2 }), { type: "region", index: 1 }));
  assert(relationTouchesNode(rel({ t: "region", i: 3 }, { t: "region", i: 1 }), { type: "region", index: 1 }));
  assert(!relationTouchesNode(rel({ t: "region", i: 2 }, { t: "region", i: 3 }), { type: "region", index: 1 }));
  assert(relationTouchesNode(rel(f1, f2), { type: "file", index: 1 }));
  assert(relationTouchesNode(rel(f2, f1), { type: "file", index: 1 }));
  assert(!relationTouchesNode(rel(f2, { t: "file", i: 3 }), { type: "file", index: 1 }));
  assert(relationTouchesNode(rel(f2, sym), { type: "file", index: 1 }));
  assert(relationTouchesNode(rel(sym, f2), { type: "symbol", id: "a#F.m", file: 1 }));
  assert(relationTouchesNode(rel({ t: "symbol", id: "a#F.m.x", file: 1 }, f2), { type: "symbol", id: "a#F.m", file: 1 }));
  assert(!relationTouchesNode(rel(f2, { t: "file", i: 3 }), { type: "symbol", id: "a#F.m", file: 1 }));
  assert(relationTouchesNode(rel(r1, f2), null));
});

test("L0 center clicks resolve the innermost region even when an edge overlays it", () => {
  const graph = { regions: [ { depth: 0, box: [0, 0, 1000, 1000] }, { depth: 1, box: [10, 10, 400, 400] },
    { depth: 2, box: [20, 20, 100, 100] } ] };
  assert.equal(regionAtPoint(graph, [200, 200]), 1);
  assert.equal(regionAtPoint(graph, [70, 70]), 2);
  assert.equal(regionAtPoint(graph, [800, 800]), null);
});
