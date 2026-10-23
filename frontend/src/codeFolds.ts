import type { DeclarationFold } from "./types";

export type CodeRow = { kind: "line"; n: number; text: string } | { kind: "fold"; fold: DeclarationFold };
export function foldedCodeRows(lines: string[], lo: number, folds: DeclarationFold[]): CodeRow[] {
  const rows: CodeRow[] = [];
  let covered = 0;
  for (const [k, text] of lines.entries()) {
    const n = lo + k;
    if (n <= covered) continue;
    const fold = folds.find(f => f.from <= n && f.to >= n);
    if (fold) { rows.push({ kind: "fold", fold }); covered = fold.to; }
    else rows.push({ kind: "line", n, text });
  }
  return rows;
}
