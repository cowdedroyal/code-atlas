import type { Chunk, Graph } from "./types";
import type { TokenLines } from "./syntax";

const base = new URL("data/", document.baseURI).toString();

export async function loadGraph(): Promise<Graph> {
  const r = await fetch(base + "graph.json");
  if (!r.ok) throw new Error(`graph.json: ${r.status}`);
  return r.json();
}

const chunkCache = new Map<number, Promise<Chunk>>();
export function loadChunk(i: number): Promise<Chunk> {
  if (!chunkCache.has(i)) chunkCache.set(i, fetch(`${base}chunks/${i}.json`).then((r) => r.json()));
  return chunkCache.get(i)!;
}

const codeCache = new Map<number, Promise<Record<string, string>>>();
const syntaxCache = new Map<number, Promise<Record<string, TokenLines>>>();
export function loadSyntax(i: number): Promise<Record<string, TokenLines>> {
  if (!syntaxCache.has(i)) syntaxCache.set(i, fetch(`${base}syntax/${i}.json`).then(r => r.ok ? r.json() : {}).catch(() => ({})));
  return syntaxCache.get(i)!;
}
export function loadCode(i: number): Promise<Record<string, string>> {
  if (!codeCache.has(i))
    codeCache.set(i, fetch(`${base}code/${i}.json`).then((r) => (r.ok ? r.json() : {})).catch(() => ({})));
  return codeCache.get(i)!;
}

// [name, kind, file index, id, x, y, w, h] for every symbol: only fetched once someone searches
export type SearchRow = [string, string, number, string, number, number, number, number];
let searchCache: Promise<SearchRow[]> | null = null;
export function loadSearch(): Promise<SearchRow[]> {
  if (!searchCache) searchCache = fetch(base + "search.json").then((r) => (r.ok ? r.json() : [])).catch(() => []);
  return searchCache;
}
