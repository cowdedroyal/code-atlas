import { Lexer, type Token } from "marked";

const cache = new Map<string, Token[]>();
let chars = 0;
export function parseMarkdown(text: string): Token[] {
  const cached = cache.get(text);
  if (cached) { cache.delete(text); cache.set(text, cached); return cached; }
  const tokens = Lexer.lex(text, { gfm: true, breaks: false });
  cache.set(text, tokens);
  chars += text.length;
  while (cache.size > 16 || chars > 1_000_000) {
    const oldest = cache.keys().next().value!;
    chars -= oldest.length;
    cache.delete(oldest);
  }
  return tokens;
}
