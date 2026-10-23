export interface Token { text: string; className?: string }
export type TokenLines = Token[][];
export const MAX_HIGHLIGHT_CHARS = 100_000;
export const MAX_CODE_LINES = 400;
import { languageAliases, grammarNames } from "./languageNames.ts";
const supported = new Set(grammarNames);
export const hasGrammar = (language: string) => supported.has(language);

const aliases: Record<string, string> = languageAliases;
const extensions: Record<string, string> = { py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript",
  mjs: "javascript", cjs: "javascript", go: "go", java: "java", rs: "rust", c: "c", h: "c", cpp: "cpp", hpp: "cpp",
  cs: "csharp", json: "json", yaml: "yaml", yml: "yaml", toml: "ini", md: "markdown", sql: "sql", sh: "bash" };
export function codeLanguage(lang: string | null | undefined, path: string): string {
  return (lang && (aliases[lang] ?? (lang === "toml" ? "ini" : lang))) || extensions[path.split(".").pop()!.toLowerCase()] || "";
}

// Rendering is bounded even for a huge class/function. Parse the prefix before
// the excerpt so multiline strings/comments keep their scope at its first line.
export function codeWindow(text: string, from?: number, to?: number, maxLines = MAX_CODE_LINES) {
  const lines = text.split("\n");
  const lo = Math.min(lines.length, Math.max(1, from ?? 1));
  const end = Math.min(lines.length, Math.max(lo, to ?? lines.length));
  const hi = Math.min(end, lo + maxLines - 1);
  return { lines: lines.slice(lo - 1, hi), lo, hi, remaining: end - hi,
    prefix: lines.slice(0, hi).join("\n") };
}

let worker: Worker | null = null;
let nextId = 0;
const pending = new Map<number, (rows: TokenLines | null) => void>();
export function highlightCode(prefix: string, language: string, lo: number): Promise<TokenLines | null> {
  if (!supported.has(language) || prefix.length > MAX_HIGHLIGHT_CHARS || typeof Worker === "undefined") return Promise.resolve(null);
  try {
    if (!worker) {
      worker = new Worker(new URL("./syntax.worker.ts", import.meta.url), { type: "module" });
      worker.onmessage = (event: MessageEvent<{ id: number; rows: TokenLines | null }>) => {
        pending.get(event.data.id)?.(event.data.rows);
        pending.delete(event.data.id);
      };
      worker.onerror = () => {
        worker?.terminate();
        worker = null;
        for (const resolve of pending.values()) resolve(null);
        pending.clear();
      };
    }
    return new Promise((resolve) => {
      const id = ++nextId;
      pending.set(id, resolve);
      try { worker!.postMessage({ id, prefix, language, lo }); }
      catch { pending.delete(id); resolve(null); }
    });
  } catch {
    return Promise.resolve(null);
  }
}
