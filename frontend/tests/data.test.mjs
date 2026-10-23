import test from "node:test";
import assert from "node:assert/strict";

test("fresh page data revalidates HTTP caches while loaded chunks stay cached in memory", async () => {
  const previousDocument = globalThis.document, previousFetch = globalThis.fetch;
  globalThis.document = { baseURI: "https://example.org/atlas/" };
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push([url, options]);
    return { ok: true, json: async () => ({}) };
  };
  try {
    const data = await import("../src/data.ts");
    await data.loadGraph(); await data.loadChunk(0); await data.loadChunk(0);
    await data.loadCode(0); await data.loadSyntax(0); await data.loadSearch();
    assert.equal(requests.length, 5);
    assert(requests.every(([url, options]) => url.startsWith("https://example.org/atlas/data/") && options.cache === "no-cache"));
  } finally { globalThis.document = previousDocument; globalThis.fetch = previousFetch; }
});
