import type { Box, Graph, Level, LevelLock, NodeRef } from "./types";

export const FILE_BOX_PX = 70;
export const DOT_REGION_PX = 180;

export function intersects(a: Box, b: Box): boolean {
  return a[0] < b[0] + b[2] && a[0] + a[2] > b[0] && a[1] < b[1] + b[3] && a[1] + a[3] > b[1];
}

export function visibleFiles(graph: Graph, view: Box, scale: number, lock: LevelLock | null) {
  const dotFiles: number[] = [], boxFiles: number[] = [];
  graph.files.forEach((f, i) => {
    if (!intersects(f.box, view)) return;
    if (lock) {
      if (lock.level === 1) dotFiles.push(i);
      else if (lock.level === 2) boxFiles.push(i);
      return;
    }
    const r = graph.regions[f.region];
    const leafWidth = r.parent === null ? f.box[2] * 2.5 : r.box[2];
    if (f.box[2] * scale >= FILE_BOX_PX) boxFiles.push(i);
    else if (leafWidth * scale >= DOT_REGION_PX) dotFiles.push(i);
  });
  const level: Level = lock?.level ?? (boxFiles.length ? 2 : dotFiles.length ? 1 : 0);
  return { dotFiles, boxFiles, level };
}

export function refAtLevel(ref: NodeRef, level: Level): boolean {
  return ref.t === (["region", "file", "symbol"] as const)[level];
}

export function regionAtPoint(graph: Graph, point: number[]): number | null {
  let hit: number | null = null;
  graph.regions.forEach((r, i) => {
    const [x, y, w, h] = r.box;
    if (r.depth > 0 && point[0] >= x && point[0] <= x + w && point[1] >= y && point[1] <= y + h
        && (hit === null || r.depth > graph.regions[hit].depth)) hit = i;
  });
  return hit;
}

export function focusedRegionEdges(graph: Graph, visible: Set<number>, active: number | null) {
  const edges = graph.region_edges.filter(([a, b]) => graph.regions[a].parent === graph.regions[b].parent
    && (active === null ? visible.has(a) && visible.has(b) : a === active || b === active))
    .sort((a, b) => b[2] - a[2]);
  return active === null ? edges.slice(0, 40) : edges;
}

export function relationTouchesRegion(src: NodeRef, dst: NodeRef, active: number | null): boolean {
  return active === null || (src.t === "region" && src.i === active) || (dst.t === "region" && dst.i === active);
}
