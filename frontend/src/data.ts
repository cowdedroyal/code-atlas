import type { Chunk, Graph } from "./types";
import type { TokenLines } from "./syntax";

const base = new URL("data/", document.baseURI).toString();

export async function loadGraph(): Promise<Graph> {
  const r = await fetch(base + "graph.json", { cache: "no-cache" });
  if (!r.ok) throw new Error(`graph.json: ${r.status}`);
  return r.json();
}

// a packed explainer card (a full HTML page stored as a JSON string); null when it cannot be fetched
export function loadCard(id: string): Promise<string | null> {
  return fetch(`${base}explainers/${encodeURIComponent(id)}.json`, { cache: "no-cache" })
    .then((r) => (r.ok ? r.json() : null))
    .then((v: unknown) => (typeof v === "string" ? v : null))
    .catch(() => null);
}

const warned = new Set<string>();
const warn = (path: string) => {
  if (warned.has(path)) return;
  warned.add(path);
  console.error(`${path} failed to load; the next request will retry it`);
};

const EMPTY_CHUNK: Chunk = { symbols: [], edges: [] };
const chunkCache = new Map<number, Promise<Chunk>>();
/** Evict a failed fetch (so a later frame retries) but resolve an empty chunk: one bad file must not reject the
 *  whole batch in App's Promise.all and drop every other chunk that loaded. */
export function loadChunk(i: number): Promise<Chunk> {
  const cached = chunkCache.get(i);
  if (cached) return cached;
  const p = fetch(`${base}chunks/${i}.json`, { cache: "no-cache" }).then((r) => {
    if (!r.ok) throw new Error(`chunks/${i}.json: ${r.status}`);
    return r.json() as Promise<Chunk>;
  });
  chunkCache.set(i, p);
  p.catch(() => { chunkCache.delete(i); warn(`chunks/${i}.json`); });
  return p.catch(() => EMPTY_CHUNK);
}

/** Cache only a successful payload. A 404 is the legitimate "not included in this build" answer and stays cached;
 *  a transient failure is evicted so the next render retries instead of memoizing an empty result forever. */
function loadCached<T>(cache: Map<number, Promise<T>>, i: number, path: string, absent: () => T): Promise<T> {
  let p = cache.get(i);
  if (!p) {
    p = fetch(path, { cache: "no-cache" }).then((r) => {
      if (r.ok) return r.json() as Promise<T>;
      if (r.status === 404) return absent();
      throw new Error(`${path}: ${r.status}`);
    });
    cache.set(i, p);
    p.catch(() => cache.delete(i));
  }
  return p.catch(() => { warn(path); return absent(); });
}

const codeCache = new Map<number, Promise<Record<string, string>>>();
const syntaxCache = new Map<number, Promise<Record<string, TokenLines>>>();
export function loadSyntax(i: number): Promise<Record<string, TokenLines>> {
  return loadCached(syntaxCache, i, `${base}syntax/${i}.json`, () => ({}));
}
export function loadCode(i: number): Promise<Record<string, string>> {
  return loadCached(codeCache, i, `${base}code/${i}.json`, () => ({}));
}

// [name, kind, file index, id, x, y, w, h] for every symbol: only fetched once someone searches
export type SearchRow = [string, string, number, string, number, number, number, number];
const searchCache = new Map<number, Promise<SearchRow[]>>();
export function loadSearch(): Promise<SearchRow[]> {
  return loadCached(searchCache, 0, base + "search.json", () => []);
}
