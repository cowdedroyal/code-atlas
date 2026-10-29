import { useEffect, useRef, useState } from "react";
import type { Explainer, Graph, Selection } from "./types";
import { loadCard } from "./data";
import { parseCardMessage, selectionForId } from "./explainerLinks";
import { t } from "./i18n";

/** Short chip text: a region's name, or the file name (with `#symbol`) rather than the whole path. */
function anchorLabel(graph: Graph, anchor: string): string {
  if (anchor.startsWith("region:")) return graph.regions.find((r) => r.id === anchor)?.name ?? anchor;
  const [path, symbol] = anchor.split("#");
  return path.split("/").pop() + (symbol ? "#" + symbol : "");
}

export function ExplainerList({ cards, open }: { cards: Explainer[]; open: (card: Explainer) => void }) {
  if (!cards.length) return null;
  return (
    <section>
      <h4>{t("explanations")} <span className="muted">{cards.length}</span></h4>
      <ul className="list cards">
        {cards.map((c) => (
          <li key={c.id} onClick={() => open(c)} title={c.questions.join("\n")}>
            <div><strong>{c.title}</strong>
              {c.state && <span className="badge warn">{t(c.state === "orphan" ? "orphan" : "stale")}</span>}</div>
            <div className="muted small">{c.summary}</div>
          </li>
        ))}
      </ul>
    </section>
  );
}

interface ReaderProps {
  card: Explainer;
  graph: Graph;
  dark: boolean;
  close: () => void;
  navigate: (s: Selection) => void;
}

/** The reader's window in viewport pixels. `null` in state means the CSS default (centred, nearly full height). */
interface Box { x: number; y: number; w: number; h: number }
const MIN_W = 360, MIN_H = 240;
const HANDLES = ["n", "s", "e", "w", "ne", "nw", "se", "sw"] as const;
type Handle = (typeof HANDLES)[number];

/** The card's natural width (46rem column + 3rem padding): below this it stays at scale 1 and wraps. */
const BASE_W = 784, MIN_SCALE = 1, MAX_SCALE = 3;  // 3 is the kit's own ceiling for --ex-scale

/** Keep the card's line length while its type follows the window: wider reader, larger text. */
function scaleFor(width: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.round((width / BASE_W) * 100) / 100));
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Keeps a box inside the viewport and above the minimum size (or the viewport, if that is smaller). */
function fit(b: Box): Box {
  const w = Math.min(window.innerWidth, Math.max(MIN_W, b.w)), h = Math.min(window.innerHeight, Math.max(MIN_H, b.h));
  return { w, h, x: clamp(b.x, 0, window.innerWidth - w), y: clamp(b.y, 0, window.innerHeight - h) };
}

/** The box that results from dragging `handle` by (dx, dy): the opposite edge stays where it was. */
function resized(start: Box, handle: Handle, dx: number, dy: number): Box {
  let { x, y, w, h } = start;
  const right = start.x + start.w, bottom = start.y + start.h;
  if (handle.includes("e")) w = clamp(start.w + dx, MIN_W, window.innerWidth - x);
  if (handle.includes("w")) { w = clamp(start.w - dx, MIN_W, right); x = right - w; }
  if (handle.includes("s")) h = clamp(start.h + dy, MIN_H, window.innerHeight - y);
  if (handle.includes("n")) { h = clamp(start.h - dy, MIN_H, bottom); y = bottom - h; }
  return { x, y, w, h };
}

/**
 * Shows one card in a sandboxed iframe: scripts run, but the page has no same-origin access, no network (its own CSP)
 * and talks to the viewer only through the messages in explainerLinks.ts.
 */
export function ExplainerReader({ card, graph, dark, close, navigate }: ReaderProps) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [html, setHtml] = useState<string | null | undefined>(undefined);
  const [height, setHeight] = useState(240);
  const [box, setBox] = useState<Box | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const scaleRef = useRef(scale);
  scaleRef.current = scale;
  const [cinema, setCinema] = useState(false);
  const drag = useRef<{ handle: Handle; x: number; y: number; start: Box } | null>(null);
  const language = card.language ?? graph.meta.language;

  // The card kit takes a scale (<html> rem size); track the reader width so resizing enlarges its text too.
  useEffect(() => {
    const el = body.current;
    if (!el) return;
    const apply = () => setScale(scaleFor(el.clientWidth));
    apply();
    // The first layout can happen before the reader has its final width; measure again once the card is in.
    const raf = requestAnimationFrame(apply);
    if (typeof ResizeObserver !== "function") {
      window.addEventListener("resize", apply);
      return () => window.removeEventListener("resize", apply);
    }
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);

  // A window resize must not leave a dragged box hanging outside the viewport.
  useEffect(() => {
    const on = () => setBox((b) => (b ? fit(b) : b));
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);

  useEffect(() => {
    let live = true;
    setHtml(undefined);
    loadCard(card.id).then((h) => live && setHtml(h));
    return () => { live = false; };
  }, [card.id]);

  const sendTheme = () =>
    frame.current?.contentWindow?.postMessage({ type: "atlas:theme", theme: dark ? "dark" : "light", lang: language, scale: scaleRef.current }, "*");
  useEffect(() => { sendTheme(); }, [dark, language, scale, html]);

  useEffect(() => {
    const on = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow) return;
      const msg = parseCardMessage(e.data);
      if (!msg) return;
      if (msg.type === "atlas:ready") sendTheme();
      else if (msg.type === "atlas:height") { if (!cinema) setHeight(msg.height); }
      else if (msg.type === "atlas:cinema") setCinema(msg.action === "enter");
      else if (msg.type === "atlas:playback") { /* the card has its own 放映 button */ }
      else {
        const s = selectionForId(graph, msg.target);
        if (s) { navigate(s); close(); }
      }
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, [graph, navigate, close, dark, language, cinema]);

  // The parent follows the card's screening mode: full viewport, no reader chrome, no scrolling.
  useEffect(() => {
    frame.current?.contentWindow?.postMessage({ type: "atlas:cinema", on: cinema }, "*");
  }, [cinema, html]);

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (cinema) {
        frame.current?.contentWindow?.postMessage({ type: "atlas:cinema", on: false }, "*");
        setCinema(false);
      } else close();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [close, cinema]);

  return (
    <div className={`reader-backdrop${cinema ? " cinema" : ""}`}
         onMouseDown={(e) => { if (e.target === e.currentTarget && !cinema) close(); }}>
      <div className={`reader${box && !cinema ? " sized" : ""}${cinema ? " cinema" : ""}`} ref={dialog} role="dialog"
           aria-modal="true" aria-label={card.title}
           style={cinema ? undefined : box ? { left: box.x, top: box.y, width: box.w, height: box.h } : undefined}>
        {!cinema && HANDLES.map((handle) => (
          <div key={handle} className={`reader-grip ${handle}`} title={t("resizeCard")}
               onPointerDown={(e) => {
                 if (e.button !== 0 || !dialog.current) return;
                 e.preventDefault();
                 const r = dialog.current.getBoundingClientRect();
                 drag.current = { handle, x: e.clientX, y: e.clientY, start: { x: r.left, y: r.top, w: r.width, h: r.height } };
                 e.currentTarget.setPointerCapture(e.pointerId);
               }}
               onPointerMove={(e) => {
                 const d = drag.current;
                 if (d && d.handle === handle) setBox(resized(d.start, handle, e.clientX - d.x, e.clientY - d.y));
               }}
               onPointerUp={(e) => { drag.current = null; e.currentTarget.releasePointerCapture(e.pointerId); }}
               onLostPointerCapture={() => { drag.current = null; }}
               onDoubleClick={() => setBox(null)} />
        ))}
        <header className="reader-head">
          <div>
            <div className="eyebrow">{t("explanations")}
              {card.state && <span className="badge warn">{t(card.state === "orphan" ? "orphan" : "stale")}</span>}</div>
            <h2>{card.title}</h2>
            <p className="muted">{card.summary}</p>
            <div className="reader-anchors">
              <span className="muted">{t("explains")}</span>
              {card.anchors.map((a) => {
                const s = selectionForId(graph, a);
                return <button key={a} className={`chip${card.stale_nodes?.includes(a) ? " warn" : ""}`} disabled={!s}
                               onClick={() => { if (s) { navigate(s); close(); } }} title={a}>{anchorLabel(graph, a)}</button>;
              })}
            </div>
          </div>
          <button className="close" onClick={close} aria-label={t("close")}>×</button>
        </header>
        <div className="reader-body" ref={body}>
          {html === undefined ? <div className="muted">…</div>
            : html === null ? <div className="muted">{t("cardUnavailable")}</div>
            : <iframe ref={frame} className="reader-frame" title={card.title} sandbox="allow-scripts" allow="autoplay" srcDoc={html}
                      style={{ height: cinema ? "100%" : height }}
                      onLoad={() => {
                        sendTheme();
                        frame.current?.contentWindow?.postMessage({ type: "atlas:cinema", on: cinema }, "*");
                      }} />}
        </div>
      </div>
    </div>
  );
}
