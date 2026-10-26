export type Box = [number, number, number, number]; // x, y, w, h

export type Level = 0 | 1 | 2;
export interface LevelLock { level: Level; zoom: number }

export interface Region {
  id: string; name: string; path: string; parent: number | null; role: Role; auto: boolean;
  depth: number; box: Box; files: number; chunk: number | null;
  responsibility?: string; note?: string; stale?: string;
}

export interface FileNode {
  id: string; region: number; kind: string; lang: string | null; box: Box; lines: number;
  rank: number; entry: boolean; chunk: number; in: number;
  doc?: string; note?: string; stale?: string; also?: number[];
  module_scope?: string;
}

export type NodeRef = { t: "region"; i: number } | { t: "file"; i: number } | { t: "symbol"; id: string; file: number };

export interface Relation {
  id: string; src: NodeRef; dst: NodeRef; family: string; label: string; rationale: string;
  evidence: string[]; stale?: string;
}

export interface TourStep {
  at: string; ref: NodeRef; text: string; box: Box; hops: [number, number][]; from?: number; via_relation?: string; state?: string;
}

export interface Tour { id: string; title: string; summary?: string; steps: TourStep[]; state?: string }

export interface Explainer {
  id: string; title: string; summary: string; questions: string[]; anchors: string[]; tags: string[];
  language: string | null; at: string | null; state?: "stale" | "orphan"; stale_nodes?: string[];
}

export interface Graph {
  version: string;
  meta: { repo: string; commit: string | null; scanned_at: string | null; files_total: number;
          files_mapped: number; symbols: number; edges: number; assets: Record<string, number>;
          semantic: boolean; language: string };
  regions: Region[];
  files: FileNode[];
  file_edges: [number, number, number, string[]][];
  region_edges: [number, number, number][];
  chunks: number;
  relations: Relation[];
  tours: Tour[];
  explainers?: Explainer[];
}

export interface SymbolNode {
  id: string; file: number; name: string; kind: string; parent: string | null; box: Box;
  sig: string | null; doc: string | null; start: number; end: number; in: number;
  note?: string; stale?: string;
  folds?: DeclarationFold[];
}

export interface DeclarationFold { from: number; to: number; name: string; id: string }

export type SymEdge = [string, string, string, number, number]; // src, dst, type, conf, count

export interface Chunk { symbols: SymbolNode[]; edges: SymEdge[] }

export type Role = "interface" | "orchestration" | "core" | "state" | "integration" | "presentation" |
  "foundation" | "tooling" | "unassigned";

export type Selection =
  | { type: "region"; index: number }
  | { type: "file"; index: number }
  | { type: "symbol"; id: string; file: number }
  | null;
