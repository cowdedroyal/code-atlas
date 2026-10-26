import type { Explainer, Graph, Selection } from "./types";

/**
 * Cards that explain the selected node. A file also gets the cards on its symbols; a symbol gets the cards on itself,
 * on its members and on the class or function that encloses it.
 */
export function explainersFor(graph: Pick<Graph, "regions" | "files" | "explainers">, sel: Selection): Explainer[] {
  const cards = graph.explainers ?? [];
  if (!sel || !cards.length) return [];
  let match: (anchor: string) => boolean;
  if (sel.type === "region") {
    const id = graph.regions[sel.index].id;
    match = (a) => a === id;
  } else if (sel.type === "file") {
    const id = graph.files[sel.index].id;
    match = (a) => a === id || a.startsWith(id + "#");
  } else {
    match = (a) => a === sel.id || a.startsWith(sel.id + ".") || sel.id.startsWith(a + ".");
  }
  return cards.filter((c) => c.anchors.some(match));
}

/** The selection for an anchor or an `atlas://` target: `region:<id>`, a file path or a symbol id `path#Qual.name`. */
export function selectionForId(graph: Pick<Graph, "regions" | "files">, id: string): Selection {
  if (id.startsWith("region:")) {
    const index = graph.regions.findIndex((r) => r.id === id);
    return index < 0 ? null : { type: "region", index };
  }
  const path = id.split("#")[0];
  const file = graph.files.findIndex((f) => f.id === path);
  if (file < 0) return null;
  return id.includes("#") ? { type: "symbol", id, file } : { type: "file", index: file };
}

/** Messages a card may send; anything else is ignored. */
export type CardMessage =
  | { type: "atlas:ready"; kit: number }
  | { type: "atlas:height"; height: number }
  | { type: "atlas:open"; target: string };

export function parseCardMessage(data: unknown): CardMessage | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (d.type === "atlas:ready" && typeof d.kit === "number") return { type: d.type, kit: d.kit };
  if (d.type === "atlas:height" && typeof d.height === "number" && Number.isFinite(d.height))
    return { type: d.type, height: Math.max(0, Math.min(20000, Math.round(d.height))) };
  if (d.type === "atlas:open" && typeof d.target === "string" && d.target.length < 400) return { type: d.type, target: d.target };
  return null;
}
