import test from "node:test";
import assert from "node:assert/strict";
import { explainersFor, parseCardMessage, selectionForId } from "../src/explainerLinks.ts";

const graph = {
  regions: [{ id: "region:root" }, { id: "region:domain" }],
  files: [{ id: "app/models.py" }, { id: "main.py" }],
  explainers: [
    { id: "a", anchors: ["app/models.py#User", "region:domain"] },
    { id: "b", anchors: ["main.py"] },
    { id: "c", anchors: ["app/models.py#User.greet"] },
  ],
};

test("a file shows the cards on its symbols, a symbol the ones on its members, a region only its own", () => {
  const ids = (sel) => explainersFor(graph, sel).map((c) => c.id);
  assert.deepEqual(ids({ type: "file", index: 0 }), ["a", "c"]);
  assert.deepEqual(ids({ type: "file", index: 1 }), ["b"]);
  assert.deepEqual(ids({ type: "symbol", id: "app/models.py#User", file: 0 }), ["a", "c"]);
  assert.deepEqual(ids({ type: "symbol", id: "app/models.py#User.greet", file: 0 }), ["a", "c"]);  // a card on the class covers its methods
  assert.deepEqual(ids({ type: "symbol", id: "app/models.py#User.greeting", file: 0 }), ["a"]);  // but not a sibling that merely shares a prefix
  assert.deepEqual(ids({ type: "region", index: 1 }), ["a"]);
  assert.deepEqual(ids({ type: "region", index: 0 }), []);
  assert.deepEqual(ids(null), []);
  assert.deepEqual(explainersFor({ ...graph, explainers: undefined }, { type: "file", index: 0 }), []);
});

test("anchors and atlas:// targets resolve to selections; unknown ones do not", () => {
  assert.deepEqual(selectionForId(graph, "region:domain"), { type: "region", index: 1 });
  assert.deepEqual(selectionForId(graph, "main.py"), { type: "file", index: 1 });
  assert.deepEqual(selectionForId(graph, "app/models.py#User.greet"), { type: "symbol", id: "app/models.py#User.greet", file: 0 });
  assert.equal(selectionForId(graph, "nope.py"), null);
  assert.equal(selectionForId(graph, "region:ghost"), null);
});

test("only well-formed card messages are accepted, with heights clamped", () => {
  assert.deepEqual(parseCardMessage({ type: "atlas:ready", kit: 1 }), { type: "atlas:ready", kit: 1 });
  assert.deepEqual(parseCardMessage({ type: "atlas:height", height: 99999 }), { type: "atlas:height", height: 20000 });
  assert.deepEqual(parseCardMessage({ type: "atlas:open", target: "a.py#f" }), { type: "atlas:open", target: "a.py#f" });
  for (const bad of [null, "x", {}, { type: "atlas:height", height: "5" }, { type: "atlas:height", height: NaN },
                     { type: "atlas:open", target: 5 }, { type: "atlas:open", target: "x".repeat(500) }, { type: "other" }])
    assert.equal(parseCardMessage(bad), null);
});
