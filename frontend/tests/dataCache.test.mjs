import test from "node:test";
import assert from "node:assert/strict";

const previousDocument = globalThis.document, previousFetch = globalThis.fetch;
globalThis.document = { baseURI: "https://example.org/atlas/" };
let dataPromise;
const data = () => (dataPromise ??= import("../src/data.ts"));

test.after(() => { globalThis.document = previousDocument; globalThis.fetch = previousFetch; });

test("a failed chunk is not cached and does not drop the rest of the batch", async () => {
  let attempts = 0;
  globalThis.fetch = async (url) => {
    if (String(url).endsWith("chunks/0.json")) {
      attempts++;
      if (attempts === 1) throw new Error("offline");
      return { ok: true, json: async () => ({ symbols: [{ id: "a" }], edges: [] }) };
    }
    return { ok: true, json: async () => ({ symbols: [{ id: "b" }], edges: [] }) };
  };
  const d = await data();
  const batch = await Promise.all([d.loadChunk(0), d.loadChunk(1)]);
  assert.deepEqual(batch.map((c) => c.symbols.length), [0, 1]);  // the bad chunk is empty, the good one survives
  assert.equal((await d.loadChunk(0)).symbols.length, 1);       // and the next call retries instead of replaying it
  assert.equal(attempts, 2);
});

test("a transient search failure is retried instead of cached as empty", async () => {
  let calls = 0;
  globalThis.fetch = async (url) => {
    assert.ok(String(url).endsWith("search.json"));
    calls++;
    if (calls === 1) return { ok: false, status: 500 };
    return { ok: true, json: async () => [["sym", "function", 0, "sym", 0, 0, 0, 0]] };
  };
  const d = await data();
  assert.deepEqual(await d.loadSearch(), []);
  assert.equal((await d.loadSearch()).length, 1);
  assert.equal(calls, 2);
});

test("a 404 code payload is a legitimate empty result and stays cached", async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; return { ok: false, status: 404 }; };
  const d = await data();
  assert.deepEqual(await d.loadCode(7), {});
  assert.deepEqual(await d.loadCode(7), {});
  assert.equal(calls, 1);
});

test("cards are fetched as JSON payloads, not executable .html", async () => {
  const requests = [];
  globalThis.fetch = async (url) => { requests.push(String(url)); return { ok: true, json: async () => "<html>card</html>" }; };
  const d = await data();
  assert.equal(await d.loadCard("a b"), "<html>card</html>");
  assert.ok(requests[0].endsWith("explainers/a%20b.json"));
});
