import type { Role } from "./types";

// Categorical slots in the reference palette's fixed, validated order — one per first-principles role.
// Light-surface contrast of aqua/yellow/magenta is below 3:1, so every region carries a visible text label.
export const ROLES: Role[] = ["interface", "orchestration", "core", "state", "integration", "presentation",
  "foundation", "tooling"];

export const ROLE_COLOR: Record<Role, string> = {
  interface: "#2a78d6",
  orchestration: "#eb6834",
  core: "#1baf7a",
  state: "#eda100",
  integration: "#e87ba4",
  presentation: "#008300",
  foundation: "#6250d6",
  tooling: "#e34948",
  unassigned: "#a8a79f",  // not a role: files no agent has placed yet
};

export const EDGE_COLOR: Record<string, string> = {
  calls: "#2a78d6",
  instantiates: "#6250d6",
  inherits: "#eb6834",
  implements: "#eb6834",
  references: "#1baf7a",
  imports: "#8a8984",
  tests: "#8a8984",
};

export const RELATION_INK = "#0b0b0b";

export const INK = {
  surface: "#fcfcfb",
  primary: "#0b0b0b",
  secondary: "#52514e",
  muted: "#8a8984",
  line: "#d6d5ce",
  box: "#ffffff",
};

export const DARK_INK = { surface: "#191d23", primary: "#eef0f4", secondary: "#b9bec8",
  muted: "#8f98a6", line: "#48515e", box: "#252b34" };

export type RGBA = [number, number, number, number];

export function rgba(hex: string, alpha = 255): RGBA {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255, alpha];
}
