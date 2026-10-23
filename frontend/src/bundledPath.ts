import type { Box } from "./types";
const center = (box: Box): [number, number] => [box[0] + box[2] / 2, box[1] + box[3] / 2];

// Shared region hubs bundle overview traffic. Keep selected edges direct so their
// direction and endpoints remain easy to follow.
export function bundledFilePath(a: Box, b: Box, regionA: Box, regionB: Box): [number, number][] {
  const start = center(a), end = center(b), hubA = center(regionA), hubB = center(regionB);
  const mix = (p: number[], q: number[]): [number, number] => [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
  return [start, mix(start, hubA), hubA, hubB, mix(end, hubB), end];
}
