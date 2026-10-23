import type { Token } from "marked";

export const MARKDOWN_BATCH_CHARS = 100_000;
export const isMarkdown = (lang: string | null, path: string) => lang === "markdown" || /\.(md|mdx|markdown)$/i.test(path);
export const headingSlug = (text: string) => text.toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s/g, "-");
export function decodeMarkdownText(text: string): string {
  return text.replace(/&(?:#\d+|#x[\da-f]+|[a-z][\da-z]+);/gi, entity => {
    if (entity[1] === "#") {
      const hex = entity[2].toLowerCase() === "x";
      const n = Number.parseInt(entity.slice(hex ? 3 : 2, -1), hex ? 16 : 10);
      return n > 0 && n <= 0x10ffff && !(n >= 0xd800 && n <= 0xdfff) ? String.fromCodePoint(n) : "�";
    }
    const common: Record<string, string> = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&nbsp;": "\u00a0" };
    if (common[entity]) return common[entity];
    if (typeof document === "undefined") return entity;
    const area = document.createElement("textarea");
    area.innerHTML = entity; // a single matched entity, never source markup
    return area.value;
  });
}

export function safeMarkdownLink(href: string): string | null {
  const clean = decodeMarkdownText(href).trim();
  // Reject control characters and arbitrary URI schemes before rendering an anchor.
  if (/[\u0000-\u0020\u007f]/.test(clean)) return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(clean) && !/^(https?:|mailto:)/i.test(clean)) return null;
  if (/^[\\/]{2}/.test(clean)) return null;
  return clean;
}

export function repositoryLink(href: string, path: string | undefined): string | null {
  if (!path || !href || href.startsWith("#") || /^[a-z][a-z0-9+.-]*:/i.test(href)) return null;
  const parts = href.startsWith("/") ? [] : path.split("/").slice(0, -1);
  let target: string;
  try { target = decodeURIComponent(href.split(/[?#]/)[0]); } catch { return null; }
  for (const part of target.replace(/\\/g, "/").split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") { if (!parts.length) return null; parts.pop(); }
    else parts.push(part);
  }
  return parts.join("/");
}

let worker: Worker | null = null;
let id = 0;
const pending = new Map<number, (tokens: Token[] | null) => void>();
export function markdownTokens(text: string): Promise<Token[] | null> {
  if (typeof Worker === "undefined") return import("./markdownEngine").then(m => m.parseMarkdown(text)).catch(() => null);
  try {
    if (!worker) {
      worker = new Worker(new URL("./markdown.worker.ts", import.meta.url), { type: "module" });
      worker.onmessage = (e: MessageEvent<{ id: number; tokens: Token[] | null }>) => {
        pending.get(e.data.id)?.(e.data.tokens);
        pending.delete(e.data.id);
      };
      worker.onerror = () => {
        worker?.terminate(); worker = null;
        for (const resolve of pending.values()) resolve(null);
        pending.clear();
      };
    }
    return new Promise(resolve => {
      const requestId = ++id;
      pending.set(requestId, resolve);
      try { worker!.postMessage({ id: requestId, text }); }
      catch { pending.delete(requestId); resolve(null); }
    });
  } catch { return Promise.resolve(null); }
}
