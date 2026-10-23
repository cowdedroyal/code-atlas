import { createElement, Fragment, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import type { Token, Tokens } from "marked";
import { codeLanguage, highlightCode, type TokenLines } from "./syntax";
import { decodeMarkdownText, headingSlug, MARKDOWN_BATCH_CHARS, markdownTokens, repositoryLink, safeMarkdownLink } from "./markdownClient";
import { t } from "./i18n";

export interface Props { text: string; path?: string; openFile?: (path: string, anchor?: string) => boolean; anchor?: string }

function Fence({ text, lang }: { text: string; lang?: string }) {
  const language = codeLanguage(lang?.split(/\s/)[0], "");
  const [result, setResult] = useState<{ text: string; rows: TokenLines | null } | null>(null);
  useEffect(() => {
    let live = true;
    highlightCode(text, language, 1).then(rows => { if (live) setResult({ text, rows }); });
    return () => { live = false; };
  }, [text, language]);
  const rows = result?.text === text ? result.rows : null;
  return <pre className="code markdown-code"><code>{rows ? rows.map((row, i) => <Fragment key={i}>
    {i > 0 && "\n"}{row.map((token, j) => <span key={j} className={token.className}>{token.text}</span>)}
  </Fragment>) : text}</code></pre>;
}

export default function MarkdownView({ text, path, openFile, anchor }: Props) {
  const root = useRef<HTMLDivElement>(null);
  const prefix = useId();
  const [limit, setLimit] = useState<{ text: string; chars: number; blocks: number } | null>(null);
  const charLimit = limit?.text === text ? limit.chars : MARKDOWN_BATCH_CHARS;
  const blockLimit = limit?.text === text ? limit.blocks : 100;
  const source = useMemo(() => {
    if (text.length <= charLimit) return text;
    const end = text.lastIndexOf("\n", charLimit);
    return text.slice(0, end > 0 ? end : charLimit);
  }, [text, charLimit]);
  const [parsed, setParsed] = useState<{ source: string; tokens: Token[] | null } | null>(null);
  useEffect(() => {
    let live = true;
    markdownTokens(source).then(tokens => { if (live) setParsed({ source, tokens }); });
    return () => { live = false; };
  }, [source]);
  const tokens = parsed?.source === source ? parsed.tokens : null;
  const [jump, setJump] = useState<{ text: string; slug: string } | null>(null);
  const currentAnchor = jump?.text === text ? jump.slug : anchor;
  const inlineText = (items: Token[]): string => items.map(item => {
    const token = item as Tokens.Generic;
    return token.tokens ? inlineText(token.tokens) : decodeMarkdownText(token.text ?? "");
  }).join("");
  let visibleBlocks = blockLimit;
  if (currentAnchor && tokens) {
    const counts = new Map<string, number>();
    tokens.forEach((token, i) => {
      if (token.type !== "heading") return;
      const h = token as Tokens.Heading, base = headingSlug(inlineText(h.tokens)), n = counts.get(base) ?? 0;
      counts.set(base, n + 1);
      if (base + (n ? `-${n}` : "") === currentAnchor) visibleBlocks = Math.max(visibleBlocks, i + 20);
    });
  }
  useEffect(() => {
    if (!currentAnchor || !tokens) return;
    const heading = [...root.current!.querySelectorAll<HTMLElement>("[data-md-anchor]")].find(el => el.dataset.mdAnchor === currentAnchor);
    heading?.scrollIntoView({ block: "nearest" });
  }, [currentAnchor, tokens]);
  const seen = new Map<string, number>();
  const render = (items: Token[]): ReactNode[] => items.map((item, index) => {
    const token = item as Tokens.Generic;
    const children = () => render(token.tokens ?? []);
    let node: ReactNode;
    switch (token.type) {
      case "space": case "def": case "checkbox": return null;
      case "heading": {
        const h = token as Tokens.Heading, base = headingSlug(inlineText(h.tokens)), n = seen.get(base) ?? 0;
        seen.set(base, n + 1);
        const slug = base + (n ? `-${n}` : "");
        node = createElement(`h${h.depth}`, { id: prefix + slug, "data-md-anchor": slug }, children()); break;
      }
      case "paragraph": node = <p>{children()}</p>; break;
      case "text": node = token.tokens ? children() : decodeMarkdownText(token.text); break;
      case "escape": node = decodeMarkdownText(token.text); break;
      case "strong": node = <strong>{children()}</strong>; break;
      case "em": node = <em>{children()}</em>; break;
      case "del": node = <del>{children()}</del>; break;
      case "codespan": node = <code>{token.text}</code>; break;
      case "br": node = <br />; break;
      case "hr": node = <hr />; break;
      case "blockquote": node = <blockquote>{children()}</blockquote>; break;
      case "code": node = <Fence text={token.text} lang={token.lang} />; break;
      case "list": {
        const list = token as Tokens.List;
        const entries = list.items.map((item, i) => <li key={i}>{item.task && <input type="checkbox" checked={!!item.checked} disabled />}{render(item.tokens)}</li>);
        node = list.ordered ? <ol start={Number(list.start) || 1}>{entries}</ol> : <ul>{entries}</ul>; break;
      }
      case "table": {
        const table = token as Tokens.Table;
        node = <div className="markdown-table"><table><thead><tr>{table.header.map((c, i) => <th key={i} style={{ textAlign: table.align[i] ?? undefined }}>{render(c.tokens)}</th>)}</tr></thead>
          <tbody>{table.rows.map((row, i) => <tr key={i}>{row.map((c, j) => <td key={j} style={{ textAlign: table.align[j] ?? undefined }}>{render(c.tokens)}</td>)}</tr>)}</tbody></table></div>; break;
      }
      case "link": case "image": {
        const link = token as Tokens.Link;
        const href = safeMarkdownLink(link.href), external = href && /^(https?:|mailto:)/i.test(href);
        const label = token.type === "image" ? decodeMarkdownText(token.text) : children();
        node = href ? <a href={href} title={link.title ?? undefined} target={external ? "_blank" : undefined} rel={external ? "noopener noreferrer" : undefined}
          onClick={e => {
            e.stopPropagation();
            if (href.startsWith("#")) {
              e.preventDefault();
              let slug: string; try { slug = decodeURIComponent(href.slice(1)); } catch { return; }
              setJump({ text, slug });
              [...root.current!.querySelectorAll<HTMLElement>("[data-md-anchor]")].find(el => el.dataset.mdAnchor === slug)?.scrollIntoView({ block: "nearest" });
            } else if (!external) {
              const target = repositoryLink(href, path);
              let anchor: string | undefined = href.split("#")[1];
              try { if (anchor) anchor = decodeURIComponent(anchor); } catch { anchor = undefined; }
              e.preventDefault(); // repository files are not static site URLs
              if (target) openFile?.(target, anchor);
            }
          }}>{label}</a> : <span>{label}</span>; break;
      }
      // Source HTML is displayed literally through React, never injected.
      case "html": node = <span className="markdown-plain">{token.text}</span>; break;
      default: node = token.tokens ? children() : token.raw;
    }
    return <Fragment key={index}>{node}</Fragment>;
  });
  const more = source.length < text.length || (tokens?.length ?? 0) > visibleBlocks;
  return <div className="markdown" ref={root}>
    {tokens ? render(tokens.slice(0, visibleBlocks)) : <div className="markdown-plain">{source}</div>}
    {more && <button className="fit markdown-more" onClick={() => setLimit({ text, chars: charLimit + MARKDOWN_BATCH_CHARS, blocks: visibleBlocks + 100 })}>{t("showMoreCode")}</button>}
  </div>;
}
