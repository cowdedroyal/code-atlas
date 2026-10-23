import { useEffect, useMemo, useState } from "react";
import DeckGL from "@deck.gl/react";
import { LinearInterpolator, OrthographicView } from "@deck.gl/core";
import { PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { Box, Chunk, FileNode, Graph, Level, LevelLock, NodeRef, Region, Relation, Selection, SymbolNode, SymEdge, Tour } from "./types";
import { DARK_INK, EDGE_COLOR, INK as LIGHT_INK, ROLE_COLOR, rgba, type RGBA } from "./palette";
import { focusedRegionEdges, intersects, refAtLevel, regionAtPoint, relationTouchesRegion, visibleFiles } from "./visibility";
import { MAP_FONT } from "./mapFont";
import { symbolName } from "./symbolName";
import { bundledFilePath } from "./bundledPath";

// Semantic zoom thresholds, in screen pixels.
const SYMBOL_PX = 10;
const SYMBOL_LABEL_PX = 84;
const REGION_LABEL_PX = 70;
const FONT = "Inter, 'Segoe UI', 'Noto Sans SC', 'Microsoft YaHei', 'PingFang SC', system-ui, sans-serif";

export interface ViewState {
  target: [number, number, number];
  zoom: number;
  transitionDuration?: number;
  transitionInterpolator?: LinearInterpolator;
}

export interface Insets { top?: number; right?: number; bottom?: number; left?: number }

/** Fit `box` into the part of the screen not covered by overlays (`insets`, in pixels). */
export function fitView(box: Box, size: { w: number; h: number }, fraction = 0.9, insets: Insets = {}): ViewState {
  const { top = 0, right = 0, bottom = 0, left = 0 } = insets;
  const aw = Math.max(120, size.w - left - right), ah = Math.max(120, size.h - top - bottom);
  const scale = Math.min(aw / box[2], ah / box[3]) * fraction;
  // the target is what sits at the screen centre; shift it so the box sits at the centre of the free area
  const dx = (left + aw / 2 - size.w / 2) / scale, dy = (top + ah / 2 - size.h / 2) / scale;
  return {
    target: [box[0] + box[2] / 2 - dx, box[1] + box[3] / 2 - dy, 0], zoom: Math.log2(scale),
    transitionDuration: 650, transitionInterpolator: new LinearInterpolator(["target", "zoom"]),
  };
}

type Hover =
  | { type: "region"; index: number }
  | { type: "file"; index: number }
  | { type: "symbol"; id: string; file: number }
  | null;

interface Props {
  graph: Graph;
  chunks: Map<number, Chunk>;
  requestChunks: (ids: number[]) => void;
  viewState: ViewState;
  setViewState: (vs: ViewState) => void;
  size: { w: number; h: number };
  selection: Selection;
  select: (s: Selection) => void;
  onLevel: (level: Level) => void;
  levelLock: LevelLock | null;
  tour: { tour: Tour; step: number } | null;
  dark: boolean;
}

const center = (b: Box): [number, number] => [b[0] + b[2] / 2, b[1] + b[3] / 2];
const rect = (b: Box) => [[b[0], b[1]], [b[0] + b[2], b[1]], [b[0] + b[2], b[1] + b[3]], [b[0], b[1] + b[3]]];
const base = (p: string) => p.slice(p.lastIndexOf("/") + 1);
const KIND_MARK: Record<string, string> = { class: "◆ ", interface: "◇ ", function: "ƒ ", method: "ƒ ", type: "τ ",
  enum: "ε ", section: "§ ", table: "▦ ", module_scope: "▷ " };
const SYMBOL_FILL: Record<string, string> = { class: "#f1f0eb", interface: "#f1f0eb", namespace: "#f1f0eb",
  section: "#f6f5f1", function: "#ffffff", method: "#ffffff", module_scope: "#edf3f8" };

function curve(a: [number, number], b: [number, number], bend = 0.18): [number, number][] {
  const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const c: [number, number] = [mx - dy * bend, my + dx * bend];  // always bend to the same side → direction cue
  const pts: [number, number][] = [];
  for (let i = 0; i <= 14; i++) {
    const t = i / 14, u = 1 - t;
    pts.push([u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]]);
  }
  return pts;
}

export default function MapView(p: Props) {
  const INK = p.dark ? DARK_INK : LIGHT_INK;
  const RELATION_INK = INK.primary;
  const { graph, chunks, viewState: vs, size, selection } = p;
  const [hover, setHover] = useState<Hover>(null);

  const symbolsByFile = useMemo(() => {
    const m = new Map<number, SymbolNode[]>();
    const byId = new Map<string, SymbolNode>();
    for (const c of chunks.values())
      for (const s of c.symbols) {
        if (byId.has(s.id)) continue;
        byId.set(s.id, s);
        (m.get(s.file) ?? m.set(s.file, []).get(s.file)!).push(s);
      }
    return { m, byId };
  }, [chunks]);

  const fileIndex = useMemo(() => new Map(graph.files.map((f, i) => [f.id, i])), [graph]);
  // A file's dot appears once its region is wide enough; files at the repo root use their own box instead.
  const leafPx = useMemo(() => graph.files.map((f) => {
    const r = graph.regions[f.region];
    return r.parent === null ? f.box[2] * 2.5 : r.box[2];
  }), [graph]);

  const scale = 2 ** vs.zoom;
  const [cx, cy] = vs.target;
  const hw = size.w / 2 / scale, hh = size.h / 2 / scale;
  const view: Box = [cx - hw, cy - hh, 2 * hw, 2 * hh];
  const inView = (b: Box) => intersects(b, view);
  const labelScale = p.levelLock ? Math.max(0.75, Math.min(3, 2 ** ((vs.zoom - p.levelLock.zoom) / 2))) : 1;

  const frame = useMemo(() => visibleFiles(graph, view, scale, p.levelLock), [graph, vs, size, p.levelLock]);

  useEffect(() => { p.onLevel(frame.level); }, [frame.level]);
  useEffect(() => {
    const need = [...new Set(frame.boxFiles.map((i) => graph.files[i].chunk))].filter((c) => !chunks.has(c));
    if (need.length) p.requestChunks(need);
  }, [frame.boxFiles, chunks]);

  // Keep a clicked region focused while L0 is locked; unlocked clicks still zoom.
  const atLevel = (s: Hover | Selection) => s && (frame.level === 0 ? s.type === "region" : s.type === "file" || s.type === "symbol");
  const active = p.levelLock?.level === 0 && selection?.type === "region" ? selection
    : (atLevel(hover) ? hover : null) ?? (atLevel(selection) ? selection : null);
  const activeRegion = frame.level === 0 && active?.type === "region" ? active.index : null;
  const activeFile = active?.type === "file" ? active.index : active?.type === "symbol" ? active.file : null;

  const layers = useMemo(() => {
    const L: any[] = [];
    const { dotFiles, boxFiles, level } = frame;
    const expanded = new Set(boxFiles);
    const pos = (id: string): [number, number] | null => {
      const s = symbolsByFile.byId.get(id);
      return s && expanded.has(s.file) && s.box[2] * scale >= SYMBOL_PX ? center(s.box) : null;
    };

    // --- regions --------------------------------------------------------------------------------
    const regions = graph.regions.map((r, i) => ({ r, i })).filter(({ r }) => r.depth > 0 && inView(r.box) && r.box[2] * scale >= 6);
    const fillAlpha = level === 0 ? 30 : level === 1 ? 18 : 8;
    L.push(new PolygonLayer({
      id: "regions", data: regions, pickable: true, stroked: true, filled: true,
      getPolygon: (d: any) => rect(d.r.box),
      getFillColor: (d: any) => rgba(ROLE_COLOR[d.r.role as keyof typeof ROLE_COLOR], fillAlpha),
      getLineColor: (d: any) => (selection?.type === "region" && selection.index === d.i) || (hover?.type === "region" && hover.index === d.i)
        ? rgba(INK.primary) : rgba(ROLE_COLOR[d.r.role as keyof typeof ROLE_COLOR], d.r.depth === 1 ? 200 : 130),
      getLineWidth: (d: any) => (selection?.type === "region" && selection.index === d.i ? 2.5 : d.r.depth === 1 ? 1.5 : 1),
      lineWidthUnits: "pixels",
      updateTriggers: { getFillColor: fillAlpha, getLineColor: [selection, hover], getLineWidth: selection },
    }));

    // --- region edges (L0) -----------------------------------------------------------------------
    const focus = new Set(regions.filter(({ r }) => r.box[2] * scale >= REGION_LABEL_PX).map((d) => d.i));
    if (level === 0 && !p.tour) {
      const redges = focusedRegionEdges(graph, focus, activeRegion)
        .map(([a, b, w]) => ({ a, b, path: curve(center(graph.regions[a].box), center(graph.regions[b].box)), w }));
      L.push(new PathLayer({
        id: "region-edges", data: redges, getPath: (d: any) => d.path, widthUnits: "pixels",
        getWidth: (d: any) => 0.8 + Math.log2(1 + d.w) * 0.55, getColor: rgba(INK.secondary, activeRegion === null ? 90 : 210), capRounded: true,
      }));
    }

    // Semantic relations obey the same level as structural edges, including their labels and endpoints.
    const refBox = (r: NodeRef): Box | null => {
      if (r.t === "region") return graph.regions[r.i].box;
      if (r.t === "file") return graph.files[r.i].box;
      const s = symbolsByFile.byId.get(r.id);
      return s && expanded.has(s.file) ? s.box : null;
    };
    const readable = (r: NodeRef, b: Box) => r.t === "region"
      ? b[2] * scale >= 40 : r.t === "file" ? dotFiles.includes(r.i) : b[2] * scale >= SYMBOL_PX;
    const rels = graph.relations.map((rel) => {
      if (p.tour || !refAtLevel(rel.src, level) || !refAtLevel(rel.dst, level)) return null;
      if (level === 0 && !relationTouchesRegion(rel.src, rel.dst, activeRegion)) return null;
      const a = refBox(rel.src), b = refBox(rel.dst);
      if (!a || !b || (activeRegion === null && (!readable(rel.src, a) || !readable(rel.dst, b))) || (!inView(a) && !inView(b))) return null;
      const path = curve(center(a), center(b), 0.12);
      return { rel, path, mid: path[7], end: path[path.length - 1] };
    }).filter(Boolean) as { rel: Relation; path: [number, number][]; mid: [number, number]; end: [number, number] }[];
    const dim = activeFile !== null ? 90 : 210;
    L.push(new PathLayer({
      id: "relations", data: rels, pickable: true, getPath: (d: any) => d.path, widthUnits: "pixels",
      getWidth: 2.2, getColor: (d: any) => rgba(RELATION_INK, d.rel.stale ? 90 : dim), capRounded: true,
      updateTriggers: { getColor: dim },
    }));
    L.push(new ScatterplotLayer({
      id: "relation-ends", data: rels, getPosition: (d: any) => d.end, getFillColor: rgba(RELATION_INK, dim),
      getRadius: 3.2, radiusUnits: "pixels", updateTriggers: { getFillColor: dim },
    }));

    // --- file boxes and symbols (L2) ------------------------------------------------------------
    L.push(new PolygonLayer({
      id: "file-boxes", data: boxFiles, pickable: true, stroked: true, filled: true,
      getPolygon: (i: number) => rect(graph.files[i].box), getFillColor: rgba(INK.box, 235),
      getLineColor: (i: number) => (activeFile === i ? rgba(INK.primary) : rgba("#c9c8c1")),
      getLineWidth: (i: number) => (activeFile === i ? 2 : 1), lineWidthUnits: "pixels",
      updateTriggers: { getLineColor: activeFile, getLineWidth: activeFile },
    }));
    const syms: SymbolNode[] = [];
    for (const fi of boxFiles) for (const s of symbolsByFile.m.get(fi) ?? []) if (s.box[2] * scale >= SYMBOL_PX && inView(s.box)) syms.push(s);
    const activeSym = active?.type === "symbol" ? active.id : null;
    L.push(new PolygonLayer({
      id: "symbols", data: syms, pickable: true, stroked: true, filled: true,
      getPolygon: (s: SymbolNode) => rect(s.box),
      getFillColor: (s: SymbolNode) => rgba(p.dark ? (s.kind === "module_scope" ? "#283748" : INK.box) : SYMBOL_FILL[s.kind] ?? "#f8f7f4"),
      getLineColor: (s: SymbolNode) => (s.id === activeSym ? rgba(INK.primary) : rgba(INK.line)),
      getLineWidth: (s: SymbolNode) => (s.id === activeSym ? 2 : 1), lineWidthUnits: "pixels",
      updateTriggers: { getLineColor: activeSym, getLineWidth: activeSym },
    }));

    // --- symbol edges at L2; aggregated file edges at L1 -------------------------------------------
    // (during a tour the route is the only line that matters, so these stay hidden)
    const edgeData: { path: [number, number][]; color: RGBA; w: number; end: [number, number] }[] = [];
    if (p.tour) {
      // nothing
    } else if (level === 2 && activeFile !== null) {
      const seen = new Set<string>();
      for (const c of chunks.values())
        for (const e of c.edges as SymEdge[]) {
          const [src, dst, type] = e;
          const hit = active?.type === "symbol"
            ? src === active.id || src.startsWith(active.id + ".") || dst === active.id || dst.startsWith(active.id + ".")
            : fileIndex.get(src.split("#")[0]) === activeFile || fileIndex.get(dst.split("#")[0]) === activeFile;
          const key = src + "|" + dst + "|" + type;
          if (!hit || seen.has(key)) continue;
          seen.add(key);
          const a = pos(src), b = pos(dst);
          if (a && b && (a[0] !== b[0] || a[1] !== b[1]))
            edgeData.push({ path: curve(a, b), color: rgba(EDGE_COLOR[type] ?? INK.muted, 220), w: 1.6, end: b });
        }
    } else if (level === 1 && activeFile !== null) {
      const vis = new Set(dotFiles);
      for (const [a, b, w, types] of graph.file_edges) {
        if (a !== activeFile && b !== activeFile) continue;
        if (!vis.has(a) || !vis.has(b)) continue;
        const pa = center(graph.files[a].box), pb = center(graph.files[b].box);
        const type = types.includes("calls") ? "calls" : types.includes("inherits") ? "inherits" : types[0];
        edgeData.push({ path: curve(pa, pb), color: rgba(EDGE_COLOR[type] ?? INK.muted, 200), w: 1 + Math.min(3, Math.log2(1 + w) * 0.5), end: pb });
      }
    } else if (level === 1) {
      const vis = new Set(dotFiles);
      graph.file_edges.filter(([a, b]) => vis.has(a) && vis.has(b)).sort((x, y) => y[2] - x[2]).slice(0, 150)
        .forEach(([a, b, w]) => {
          const pb = center(graph.files[b].box);
          edgeData.push({ path: bundledFilePath(graph.files[a].box, graph.files[b].box,
            graph.regions[graph.files[a].region].box, graph.regions[graph.files[b].region].box),
            color: rgba(INK.secondary, 70), w: 0.8 + Math.min(2, Math.log2(1 + w) * 0.4), end: pb });
        });
    }
    L.push(new PathLayer({
      id: "edges", data: edgeData, getPath: (d: any) => d.path, getColor: (d: any) => d.color,
      getWidth: (d: any) => d.w, widthUnits: "pixels", capRounded: true, jointRounded: true,
    }));
    L.push(new ScatterplotLayer({
      id: "edge-ends", data: edgeData, getPosition: (d: any) => d.end, getFillColor: (d: any) => d.color,
      getRadius: 2.6, radiusUnits: "pixels",
    }));

    // --- file dots (L1) -------------------------------------------------------------------------
    L.push(new ScatterplotLayer({
      id: "file-dots", data: dotFiles, pickable: true, stroked: true, filled: true,
      getPosition: (i: number) => center(graph.files[i].box),
      getRadius: (i: number) => (2.5 + 6 * Math.sqrt(graph.files[i].rank)) * labelScale, radiusUnits: "pixels",
      getFillColor: (i: number) => {
        const k = graph.files[i].kind;
        return rgba(k === "source" ? INK.secondary : k === "test" ? INK.muted : "#b5b4ad");
      },
      getLineColor: (i: number) => (activeFile === i ? rgba(INK.primary) : graph.files[i].entry ? rgba(INK.primary) : rgba(INK.surface)),
      getLineWidth: (i: number) => (activeFile === i ? 3 : graph.files[i].entry ? 2 : 1), lineWidthUnits: "pixels",
      radiusMinPixels: 2.5,
      updateTriggers: { getRadius: labelScale, getLineColor: activeFile, getLineWidth: activeFile },
    }));

    // --- labels: greedy placement in screen space, most important first --------------------------
    // (deck.gl's CollisionFilterExtension is not used: in some WebGL2 browsers its uniform block fails to
    //  reflect and the whole frame stops drawing)
    const toScreen = (x: number, y: number): [number, number] => [(x - view[0]) * scale, (y - view[1]) * scale];
    const placed: [number, number, number, number][] = [];
    const fits = (r: [number, number, number, number]) => {
      for (const q of placed) if (r[0] < q[0] + q[2] && r[0] + r[2] > q[0] && r[1] < q[1] + q[3] && r[1] + r[3] > q[1]) return false;
      placed.push(r);
      return true;
    };
    const textW = (s: string, size: number) => { let w = 0; for (const ch of s) w += ch.charCodeAt(0) > 255 ? size : size * 0.6; return w; };
    const regionLabels = regions.filter(({ r }) => r.box[2] * scale >= REGION_LABEL_PX)
      .sort((a, b) => a.r.depth - b.r.depth || b.r.box[2] - a.r.box[2])
      .map((d) => {
        const size = (d.r.depth === 1 ? 15 : 12.5) * labelScale;
        const x = Math.max(d.r.box[0], view[0]) + 6 / scale, y = Math.max(d.r.box[1], view[1]) + 6 / scale;
        const [sx, sy] = toScreen(x, y);
        const room = toScreen(d.r.box[0] + d.r.box[2], 0)[0] - sx - 10;
        let text = d.r.name;
        while (text.length > 3 && textW(text, size) > room) text = text.slice(0, -2) + "…";
        return { ...d, x, y, size, text, rect: [sx - 4, sy - 2, textW(text, size) + 8, size + 6] as [number, number, number, number] };
      })
      .filter((d) => fits(d.rect));
    L.push(new TextLayer({
      ...MAP_FONT, id: "region-labels", data: regionLabels, characterSet: "auto", fontFamily: FONT, fontWeight: 600,
      getText: (d: any) => d.text, getPosition: (d: any) => [d.x, d.y], getSize: (d: any) => d.size,
      getColor: rgba(INK.primary), getTextAnchor: "start", getAlignmentBaseline: "top",
      background: true, backgroundPadding: [4, 2], getBackgroundColor: rgba(INK.surface, 230),
    }));
    const fileLabels = [
      ...boxFiles.map((i) => ({ i, box: true })),
      ...dotFiles.filter((i) => p.levelLock || leafPx[i] * scale >= 260).sort((a, b) => graph.files[b].rank - graph.files[a].rank).map((i) => ({ i, box: false })),
    ].filter((d) => {
      const b = graph.files[d.i].box, name = base(graph.files[d.i].id);
      if (d.box) {
        const [sx, sy] = toScreen(b[0] + 8, b[1] + 6);
        return fits([sx, sy, Math.min(textW(name, 12 * labelScale), b[2] * scale - 10), 13 * labelScale]);
      }
      const [sx, sy] = toScreen(b[0] + b[2] / 2, b[1] + b[3] / 2);
      const w = textW(name, 11 * labelScale);
      return fits([sx - w / 2, sy + 7 * labelScale, w, 13 * labelScale]);
    });
    L.push(new TextLayer({
      ...MAP_FONT, id: "file-labels", data: fileLabels, characterSet: "auto", fontFamily: FONT,
      getText: (d: any) => base(graph.files[d.i].id),
      getPosition: (d: any) => {
        const b = graph.files[d.i].box;
        return d.box ? [b[0] + 8, b[1] + 12] : [b[0] + b[2] / 2, b[1] + b[3] / 2];
      },
      getSize: (d: any) => (d.box ? 12 : 11) * labelScale, getColor: (d: any) => rgba(d.box ? INK.primary : INK.secondary),
      getTextAnchor: (d: any) => (d.box ? "start" : "middle"), getAlignmentBaseline: (d: any) => (d.box ? "center" : "top"),
      getPixelOffset: (d: any) => (d.box ? [0, 0] : [0, 8 * labelScale]), fontWeight: 500,
    }));
    const relLabels = rels.filter((d) => {
      const [sx, sy] = toScreen(d.mid[0], d.mid[1]);
      const w = textW(d.rel.label, 11 * labelScale) + 8;
      return fits([sx - w / 2, sy - 9 * labelScale, w, 18 * labelScale]);
    });
    L.push(new TextLayer({
      ...MAP_FONT, id: "relation-labels", data: relLabels, characterSet: "auto", fontFamily: FONT, fontWeight: 500,
      getText: (d: any) => d.rel.label, getPosition: (d: any) => d.mid, getSize: 11 * labelScale, getColor: rgba(INK.primary),
      getTextAnchor: "middle", getAlignmentBaseline: "center", background: true, backgroundPadding: [4, 2],
      getBackgroundColor: rgba(INK.surface, 240), getBorderColor: rgba(RELATION_INK, 160), getBorderWidth: 1,
    }));
    const symLabels = syms.filter((s) => s.box[2] * scale >= SYMBOL_LABEL_PX * labelScale && s.box[3] * scale >= 11 * labelScale);
    L.push(new TextLayer({
      ...MAP_FONT, id: "symbol-labels", data: symLabels, characterSet: "auto", fontFamily: FONT,
      getText: (s: SymbolNode) => {
        const max = Math.max(3, Math.floor((s.box[2] * scale - 12) / (6.6 * labelScale)));
        const t = (KIND_MARK[s.kind] ?? "") + symbolName(s);
        return t.length > max ? t.slice(0, max - 1) + "…" : t;
      },
      getPosition: (s: SymbolNode) => [s.box[0] + 5, s.box[1] + Math.min(s.box[3] / 2, 11)],
      getSize: 11 * labelScale, getColor: (s: SymbolNode) => rgba(s.kind === "class" || s.kind === "interface" ? INK.primary : INK.secondary),
      getTextAnchor: "start", getAlignmentBaseline: "center",
      updateTriggers: { getText: [vs.zoom, labelScale] },
    }));

    // --- the active tour: the whole route through the real call chain, numbered stops, current leg bold ---
    if (p.tour) {
      const { tour, step } = p.tour;
      const legs = tour.steps.map((s, i) => {
        if (i === 0) return null;
        const from = tour.steps[s.from ?? i - 1] ?? tour.steps[i - 1];
        const pts = [center(from.box), ...s.hops, center(s.box)];
        return { i, path: pts.length > 2 ? pts : curve(pts[0], pts[1], 0.15) };
      }).filter(Boolean) as { i: number; path: [number, number][] }[];
      L.push(new PathLayer({
        id: "tour-route", data: legs, getPath: (d: any) => d.path, widthUnits: "pixels", capRounded: true, jointRounded: true,
        getWidth: (d: any) => (d.i === step ? 4 : 2),
        getColor: (d: any) => rgba(INK.primary, d.i === step ? 235 : d.i < step ? 120 : 60),
        updateTriggers: { getWidth: step, getColor: step },
      }));
      const hops = tour.steps[step].hops.map((pt) => ({ pt }));  // intermediate calls on the current leg
      L.push(new ScatterplotLayer({
        id: "tour-hops", data: hops, getPosition: (d: any) => d.pt,
        getRadius: 3.5, radiusUnits: "pixels", getFillColor: rgba(INK.surface), getLineColor: rgba(INK.primary),
        stroked: true, getLineWidth: 1.5, lineWidthUnits: "pixels",
      }));
      const stops = tour.steps.map((s, i) => ({ i, pt: center(s.box) }));
      L.push(new ScatterplotLayer({
        id: "tour-stops", data: stops, getPosition: (d: any) => d.pt, radiusUnits: "pixels", stroked: true,
        getRadius: (d: any) => (d.i === step ? 13 : 10), lineWidthUnits: "pixels", getLineWidth: 2,
        getFillColor: (d: any) => rgba(d.i === step ? INK.primary : INK.surface),
        getLineColor: (d: any) => rgba(INK.primary, d.i <= step ? 255 : 140),
        updateTriggers: { getRadius: step, getFillColor: step, getLineColor: step },
      }));
      L.push(new TextLayer({
        ...MAP_FONT, id: "tour-numbers", data: stops, getPosition: (d: any) => d.pt, getText: (d: any) => String(d.i + 1),
        getSize: 11, fontWeight: 700, fontFamily: FONT, getTextAnchor: "middle", getAlignmentBaseline: "center",
        getColor: (d: any) => rgba(d.i === step ? INK.surface : INK.primary), updateTriggers: { getColor: step },
      }));
    }
    return L;
  }, [frame, symbolsByFile, chunks, hover, selection, vs, size, p.tour, p.levelLock, p.dark]);

  return (
    <DeckGL
      views={new OrthographicView({ id: "map" })}
      viewState={vs as any}
      controller={{ scrollZoom: { speed: 0.012, smooth: true }, inertia: 250, doubleClickZoom: true }}
      onViewStateChange={({ viewState }: any) => p.setViewState({ target: viewState.target, zoom: viewState.zoom })}
      layers={layers}
      getCursor={({ isHovering, isDragging }: any) => (isDragging ? "grabbing" : isHovering ? "pointer" : "grab")}
      onHover={(info: any) => {
        const o = info.object, id = info.layer?.id;
        if (frame.level === 0 && info.coordinate) {
          const index = regionAtPoint(graph, info.coordinate);
          return setHover(index === null ? null : { type: "region", index });
        }
        if (o === undefined || o === null) return setHover(null);
        if (id === "regions") setHover({ type: "region", index: o.i });
        else if (id === "file-dots" || id === "file-boxes") setHover({ type: "file", index: o });
        else if (id === "symbols") setHover({ type: "symbol", id: o.id, file: o.file });
        else setHover(null);
      }}
      onClick={(info: any) => {
        const o = info.object, id = info.layer?.id;
        if (frame.level === 0) {
          const index = info.coordinate ? regionAtPoint(graph, info.coordinate) : id === "regions" ? o?.i ?? null : null;
          p.select(index === null ? null : { type: "region", index });
          if (index !== null && !p.levelLock) p.setViewState(fitView(graph.regions[index].box, size, 0.92));
          return;
        }
        if (o === undefined || o === null) return p.select(null);
        if (id === "symbols") p.select({ type: "symbol", id: o.id, file: o.file });
        else if (id === "file-boxes") p.select({ type: "file", index: o });
        else if (id === "file-dots") {
          p.select({ type: "file", index: o });
          p.setViewState(fitView(graph.files[o].box, size, 0.45));
        } else if (id === "regions") {
          p.select({ type: "region", index: o.i });
          p.setViewState(fitView((o.r as Region).box, size, 0.92));
        }
      }}
      getTooltip={({ object, layer }: any) => {
        if (object === undefined || object === null || !layer) return null;
        const style = { background: INK.surface, color: INK.primary, fontFamily: FONT, fontSize: "12px",
          border: "1px solid #d6d5ce", borderRadius: "4px", padding: "6px 8px", maxWidth: "420px" };
        if (layer.id === "regions") {
          const r = object.r as Region;
          return { text: `${r.name}\n${r.responsibility ? r.responsibility + "\n" : ""}${r.role} · ${r.files} files${r.auto ? " · draft" : ""}`, style };
        }
        if (layer.id === "relations") {
          const rel = object.rel as Relation;
          return { text: `${rel.label} (${rel.family})\n${rel.rationale}${rel.stale ? "\n⚠ " + rel.stale : ""}`, style };
        }
        if (layer.id === "file-dots" || layer.id === "file-boxes") {
          const f = graph.files[object] as FileNode;
          return { text: `${f.id}\n${f.kind} · ${f.lang ?? "—"} · ${f.lines} lines${f.entry ? " · entry" : ""}`, style };
        }
        if (layer.id === "symbols") {
          const s = object as SymbolNode;
          return { text: `${s.kind} ${symbolName(s)}\n${s.sig ?? ""}${s.doc ? "\n" + s.doc : ""}`, style };
        }
        return null;
      }}
    />
  );
}
