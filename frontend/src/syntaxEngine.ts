import hljs from "highlight.js/lib/core";
import type { LanguageFn } from "highlight.js";
import type { Token, TokenLines } from "./syntax";
import { grammars, dependencies } from "./languageGrammars.ts";

const loading = new Map<string, Promise<void>>();
async function loadLanguage(lang: string) {
  if (lang === "vue" || lang === "svelte") {
    await loadLanguage("xml");
    if (!hljs.getLanguage(lang)) {
      const component: LanguageFn = () => ({ name: lang, subLanguage: "xml", contains: [
        { begin: /<script\b(?=[^>]*\blang\s*=\s*["'](?:ts|typescript)["'])[^>]*>/i,
          end: /<\/script\s*>/i, excludeBegin: true, excludeEnd: true, subLanguage: "typescript" },
      ] });
      await loadLanguage("typescript");
      hljs.registerLanguage(lang, component);
    }
    return true;
  }
  if (!grammars[lang]) return false;
  if (!loading.has(lang)) loading.set(lang, grammars[lang]().then((m) => { hljs.registerLanguage(lang, m.default); }));
  await loading.get(lang);
  // Register before traversing dependencies: XML and JS embed each other.
  const seen = new Set([lang]);
  const loadDependencies = async (name: string): Promise<void> => {
    for (const dep of dependencies[name] ?? []) {
      if (seen.has(dep)) continue;
      seen.add(dep);
      if (!loading.has(dep)) loading.set(dep, grammars[dep]().then(m => { hljs.registerLanguage(dep, m.default); }));
      await loading.get(dep);
      await loadDependencies(dep);
    }
  };
  await loadDependencies(lang);
  return true;
}

// Convert the library's escaped output to React text tokens. Never insert source
// HTML into the page; nested and multiline scopes retain their classes per line.
export function htmlTokens(html: string): TokenLines {
  const lines: TokenLines = [[]];
  const scopes: string[] = [];
  const entities: Record<string, string> = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#x27;": "'" };
  for (const part of html.split(/(<span class="[\w .-]+">|<\/span>)/g)) {
    if (part.startsWith('<span class="')) { scopes.push(part.slice(13, -2)); continue; }
    if (part === "</span>") { scopes.pop(); continue; }
    const text = part.replace(/&(?:amp|lt|gt|quot|#x27);/g, (e) => entities[e]);
    const chunks = text.split("\n");
    chunks.forEach((text, i) => {
      if (i) lines.push([]);
      if (text) {
        const token: Token = { text };
        if (scopes.length) token.className = scopes.join(" ");
        lines[lines.length - 1].push(token);
      }
    });
  }
  return lines;
}

// Small LRU: reopening a symbol or resizing the panel does not reparse code.
const cache = new Map<string, TokenLines>();
let cachedChars = 0;
export async function tokenize(prefix: string, language: string, lo: number): Promise<TokenLines | null> {
  if (!await loadLanguage(language)) return null;
  const key = language + ":" + prefix;
  let lines = cache.get(key);
  if (lines) { cache.delete(key); cache.set(key, lines); }
  else {
    lines = htmlTokens(hljs.highlight(prefix, { language, ignoreIllegals: true }).value);
    cache.set(key, lines);
    cachedChars += key.length;
    while (cache.size > 8 || cachedChars > 500_000) {
      const oldest = cache.keys().next().value!;
      cachedChars -= oldest.length;
      cache.delete(oldest);
    }
  }
  return lines.slice(lo - 1);
}
