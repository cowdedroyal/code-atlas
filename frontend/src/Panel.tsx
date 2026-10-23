import type React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Chunk, DeclarationFold, Graph, NodeRef, Relation, Selection, SymbolNode } from "./types";
import { loadChunk, loadCode, loadSyntax } from "./data";
import { EDGE_COLOR, ROLE_COLOR } from "./palette";
import { t, type Key } from "./i18n";
import { codeLanguage, codeWindow, hasGrammar, highlightCode, type TokenLines } from "./syntax";
import Markdown from "./Markdown";
import { isMarkdown } from "./markdownClient";
import { symbolName } from "./symbolName";
import { foldedCodeRows } from "./codeFolds";

interface Props {
  graph: Graph;
  chunks: Map<number, Chunk>;
  selection: Exclude<Selection, null>;
  navigate: (s: Selection) => void;
  close: () => void;
  width: number;
  setWidth: (width: number) => void;
}

const fileOf = (id: string) => id.split("#")[0];

function Note({ text, stale, path, openFile }: { text?: string; stale?: string; path?: string; openFile?: (path: string, anchor?: string) => boolean }) {
  if (!text) return null;
  return (
    <div className="note">
      <Markdown text={text} path={path} openFile={openFile} />
      {stale && <span className="badge warn">{t(stale === "orphan" ? "orphan" : "stale")}</span>}
    </div>
  );
}

const sameRef = (a: NodeRef, b: NodeRef) =>
  a.t === b.t && (a.t === "symbol" ? a.id === (b as any).id : a.i === (b as any).i);

function refName(graph: Graph, r: NodeRef): string {
  if (r.t === "region") return graph.regions[r.i].name;
  if (r.t === "file") return graph.files[r.i].id.split("/").pop()!;
  return r.id.split("#")[1];
}

function refSelection(r: NodeRef): Selection {
  return r.t === "region" ? { type: "region", index: r.i } : r.t === "file" ? { type: "file", index: r.i }
    : { type: "symbol", id: r.id, file: r.file };
}

function Relations({ graph, me, navigate, path, openFile }: { graph: Graph; me: NodeRef; navigate: (s: Selection) => void; path?: string; openFile?: (path: string, anchor?: string) => boolean }) {
  const mine = graph.relations.filter((r) => sameRef(r.src, me) || sameRef(r.dst, me));
  if (!mine.length) return null;
  return (
    <section>
      <h4>{t("relations")} <span className="muted">{mine.length}</span></h4>
      <ul className="rels">
        {mine.map((r: Relation) => {
          const out = sameRef(r.src, me);
          const other = out ? r.dst : r.src;
          return (
            <li key={r.id} onClick={() => navigate(refSelection(other))} title={r.rationale}>
              <div>
                {out ? <><span className="rel-label">{r.label}</span> → <strong>{refName(graph, other)}</strong></>
                     : <><strong>{refName(graph, other)}</strong> <span className="rel-label">{r.label}</span> →</>}
                <span className="kind"> {r.family}</span>
                {r.stale && <span className="badge warn">{t(r.stale === "orphan" ? "orphan" : "stale")}</span>}
              </div>
              <div className="muted small"><Markdown text={r.rationale} path={path} openFile={openFile} /></div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Code({ chunk, path, lang, from, to, folds }: { chunk: number; path: string; lang: string | null; from?: number; to?: number; folds?: DeclarationFold[] }) {
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const [text, setText] = useState<string | null | undefined>(undefined);
  const [expanded, setExpanded] = useState<{ from?: number; to?: number; lines: number } | null>(null);
  const lineLimit = expanded?.from === from && expanded?.to === to ? expanded?.lines ?? 400 : 400;
  const excerpt = useMemo(() => text == null ? null : codeWindow(text, from, to, folds ? Infinity : lineLimit), [text, from, to, lineLimit, folds]);
  const rows = useMemo(() => excerpt ? foldedCodeRows(excerpt.lines, excerpt.lo, (folds ?? []).filter(f => !opened.has(f.id))) : [], [excerpt, folds, opened]);
  const [highlight, setHighlight] = useState<{ excerpt: typeof excerpt; rows: TokenLines | null } | null>(null);
  const tokens = highlight?.excerpt === excerpt ? highlight?.rows : null;
  const language = codeLanguage(lang, path);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let live = true;
    setHighlight(null);
    if (excerpt) {
      const result = hasGrammar(language) ? highlightCode(excerpt.prefix, language, excerpt.lo)
        : loadSyntax(chunk).then(c => c[path]?.slice(excerpt.lo - 1, excerpt.hi) ?? null);
      result.then((rows) => { if (live) setHighlight({ excerpt, rows }); }).catch(() => {});
    }
    return () => { live = false; };
  }, [excerpt, language, chunk, path]);
  useEffect(() => {
    let live = true;
    setText(undefined);
    loadCode(chunk).then((c) => live && setText(c[path] ?? null));
    return () => { live = false; };
  }, [chunk, path]);
  useEffect(() => {
    const scroller = ref.current;
    if (!scroller) return;
    const highlighted = scroller.querySelector<HTMLElement>(".hl");
    scroller.scrollTop = highlighted ? highlighted.offsetTop - 6 : 0;
    scroller.scrollLeft = 0;
  }, [text, from, to]);
  if (text === undefined) return <div className="muted">…</div>;
  if (text === null) return <div className="muted">{t("noCode")}</div>;
  const { lo } = excerpt!;
  const remaining = folds ? Math.max(0, rows.length - lineLimit) : excerpt!.remaining;
  return (
    <div className="code" ref={ref}>
      <div className="code-lines">
      {rows.slice(0, lineLimit).map(row => {
        if (row.kind === "fold") return <div className="code-fold" key={row.fold.id}>
          <span className="ln">{row.fold.from}–{row.fold.to}</span><button onClick={() => setOpened(prev => new Set([...prev, row.fold.id]))}>
            ▸ {row.fold.name} · {t("foldedDeclaration")} ({row.fold.to - row.fold.from + 1} {t("lines")})</button></div>;
        const { n, text: l } = row, k = n - lo;
        return (
          <div key={n} className={from && n >= from && n <= (to ?? from) ? "hl" : ""}>
            <span className="ln">{n}</span><span className="source-line">{tokens?.[k]?.length
              ? tokens[k].map((token, i) => <span key={i} className={token.className}>{token.text}</span>) : l || " "}</span>
          </div>
        );
      })}
      {remaining > 0 && <div className="code-more"><button className="fit" onClick={() => setExpanded({ from, to, lines: lineLimit + 400 })}>
        {t("showMoreCode")} · {remaining} {t("lines")}</button></div>}
      </div>
    </div>
  );
}

function Source({ chunk, path, lang, from, to, openFile, anchor, moduleScope }: { chunk: number; path: string; lang: string | null; from?: number; to?: number; openFile: (path: string, anchor?: string) => boolean; anchor?: string; moduleScope?: SymbolNode }) {
  const [fullFile, setFullFile] = useState(false);
  const markdown = isMarkdown(lang, path);
  const [mode, setMode] = useState<"preview" | "source">("preview");
  const [source, setSource] = useState<string | null | undefined>();
  useEffect(() => {
    if (!markdown) return;
    let live = true;
    loadCode(chunk).then(c => { if (live) setSource(c[path] ?? null); });
    return () => { live = false; };
  }, [chunk, path, markdown]);
  const text = useMemo(() => source?.split("\n").slice((from ?? 1) - 1, to).join("\n"), [source, from, to]);
  return <section className="source-section">
    <div className="source-heading"><h4>{t(markdown ? "document" : "code")}</h4>
      {moduleScope && <button className="fit scope-view" aria-pressed={fullFile} onClick={() => setFullFile(v => !v)}>
        {t(fullFile ? "moduleCodeOnly" : "viewFullFile")}</button>}
      {markdown && <div className="source-modes" role="group" aria-label={t("documentView")}>
        <button aria-pressed={mode === "preview"} onClick={() => setMode("preview")}>{t("preview")}</button>
        <button aria-pressed={mode === "source"} onClick={() => setMode("source")}>{t("source")}</button>
      </div>}
    </div>
    {!markdown || mode === "source" ? <Code key={fullFile ? "full" : "scope"} chunk={chunk} path={path} lang={lang}
      from={moduleScope ? undefined : from} to={moduleScope ? undefined : to} folds={moduleScope && !fullFile ? moduleScope.folds : undefined} />
      : source === null ? <div className="muted">{t("noCode")}</div>
      : text === undefined ? <div className="muted">…</div>
      : <div className="markdown-preview"><Markdown text={text} path={path} openFile={openFile} anchor={anchor} /></div>}
  </section>;
}

function EdgeList({ title, items, navigate, nameOf }: { title: string; items: { id: string; type: string; count: number }[]; navigate: (id: string) => void; nameOf: (id: string) => string }) {
  if (!items.length) return null;
  return (
    <section>
      <h4>{title} <span className="muted">{items.length}</span></h4>
      <ul className="list">
        {items.slice(0, 30).map((e) => (
          <li key={e.id + e.type} onClick={() => navigate(e.id)} title={e.id}>
            <span className="swatch line" style={{ background: EDGE_COLOR[e.type] ?? "#8a8984" }} />
            <span className="mono">{nameOf(e.id)}</span>
            <span className="muted"> {e.type}{e.count > 1 ? ` ×${e.count}` : ""} · {(e.id.split("#")[0]).split("/").slice(-2).join("/")}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function Panel({ graph, chunks, selection, navigate, close, width, setWidth }: Props) {
  const panelRef = useRef<HTMLElement>(null);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const [textZoom, setTextZoom] = useState(1);
  const changeZoom = (delta: number) => setTextZoom((z) => Math.max(0.75, Math.min(2, Math.round((z + delta) * 100) / 100)));
  useEffect(() => {
    const panel = panelRef.current;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      e.stopPropagation();
      if (e.deltaY) changeZoom(e.deltaY < 0 ? 0.1 : -0.1);
    };
    panel?.addEventListener("wheel", onWheel, { passive: false });
    return () => panel?.removeEventListener("wheel", onWheel);
  }, []);
  const fileIndex = useMemo(() => new Map(graph.files.map((f, i) => [f.id, i])), [graph]);
  const scopeIds = useMemo(() => new Set(graph.files.map(f => f.module_scope).filter(Boolean)), [graph]);
  const nameOf = (id: string) => scopeIds.has(id) ? t("moduleScope") : id.includes("#") ? id.split("#")[1] : id.split("/").pop()!;
  const [chunk, setChunk] = useState<Chunk | null>(null);
  const fileIdx = selection.type === "file" ? selection.index : selection.type === "symbol" ? selection.file : null;
  const file = fileIdx !== null ? graph.files[fileIdx] : null;
  const [documentAnchor, setDocumentAnchor] = useState<{ path: string; anchor?: string } | null>(null);
  const openFile = (path: string, anchor?: string) => {
    const index = fileIndex.get(path);
    if (index === undefined) return false;
    setDocumentAnchor({ path, anchor });
    navigate({ type: "file", index });
    return true;
  };

  useEffect(() => {
    let live = true;
    setChunk(null);
    if (file) loadChunk(file.chunk).then((c) => { if (live) setChunk(c); });
    return () => { live = false; };
  }, [file?.chunk]);

  const goId = (id: string) => {
    const fi = fileIndex.get(fileOf(id));
    if (fi === undefined) return;
    navigate(id.includes("#") ? { type: "symbol", id, file: fi } : { type: "file", index: fi });
  };

  let body: React.ReactElement;
  if (selection.type === "region") {
    const r = graph.regions[selection.index];
    const kids = graph.regions.map((x, i) => ({ x, i })).filter(({ x }) => x.parent === selection.index);
    const files = graph.files.map((f, i) => ({ f, i })).filter(({ f }) => f.region === selection.index)
      .sort((a, b) => b.f.rank - a.f.rank);
    body = (
      <>
        <div className="eyebrow"><span className="swatch" style={{ background: ROLE_COLOR[r.role] }} />{t(r.role as Key)}{r.auto && <span className="badge">{t("draft")}</span>}</div>
        <h2>{r.name}</h2>
        {r.responsibility && <div className="resp"><Markdown text={r.responsibility} openFile={openFile} /></div>}
        <Note text={r.note} stale={r.stale} openFile={openFile} />
        <div className="mono muted">{r.path || "/"}</div>
        <Relations graph={graph} me={{ t: "region", i: selection.index }} navigate={navigate} openFile={openFile} />
        {kids.length > 0 && (
          <section>
            <h4>{t("regions")} <span className="muted">{kids.length}</span></h4>
            <ul className="list">
              {kids.map(({ x, i }) => (
                <li key={i} onClick={() => navigate({ type: "region", index: i })}>
                  <span className="swatch" style={{ background: ROLE_COLOR[x.role] }} />{x.name}<span className="muted"> {x.files}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {files.length > 0 && (
          <section>
            <h4>{t("files")} <span className="muted">{files.length}</span></h4>
            <ul className="list">
              {files.slice(0, 40).map(({ f, i }) => (
                <li key={i} onClick={() => navigate({ type: "file", index: i })}>
                  <span className="mono">{f.id.slice(r.path ? r.path.length + 1 : 0)}</span>
                  {f.entry && <span className="badge">{t("entry")}</span>}
                </li>
              ))}
            </ul>
          </section>
        )}
      </>
    );
  } else if (selection.type === "file" && file) {
    const out: { id: string; type: string; count: number }[] = [];
    const inc: { id: string; type: string; count: number }[] = [];
    for (const [a, b, w, types] of graph.file_edges) {
      if (a === fileIdx) out.push({ id: graph.files[b].id, type: types[0], count: Math.round(w) });
      if (b === fileIdx) inc.push({ id: graph.files[a].id, type: types[0], count: Math.round(w) });
    }
    out.sort((x, y) => y.count - x.count); inc.sort((x, y) => y.count - x.count);
    const syms = (chunk?.symbols ?? []).filter((s) => s.file === fileIdx && !s.parent);
    const r = graph.regions[file.region];
    body = (
      <>
        <div className="eyebrow" onClick={() => navigate({ type: "region", index: file.region })} style={{ cursor: "pointer" }}>
          <span className="swatch" style={{ background: ROLE_COLOR[r.role] }} />{r.name}
        </div>
        <h2 className="mono">{file.id.split("/").pop()}</h2>
        <div className="mono muted">{file.id}</div>
        <div className="meta">{file.kind} · {file.lang ?? "—"} · {file.lines} {t("lines")}{file.entry && <span className="badge">{t("entry")}</span>}</div>
        {file.doc && <div className="doc"><Markdown text={file.doc} path={file.id} openFile={openFile} /></div>}
        <Note text={file.note} stale={file.stale} path={file.id} openFile={openFile} />
        {file.also && file.also.length > 0 && (
          <div className="meta">{t("alsoIn")}: {file.also.map((ri) => (
            <span key={ri} className="link" onClick={() => navigate({ type: "region", index: ri })}>{graph.regions[ri].name} </span>
          ))}</div>
        )}
        <Relations graph={graph} me={{ t: "file", i: fileIdx! }} navigate={navigate} path={file.id} openFile={openFile} />
        {syms.length > 0 && (
          <section>
            <h4>{t("symbols")} <span className="muted">{syms.length}</span></h4>
            <ul className="list">
              {syms.map((s) => (
                <li key={s.id} onClick={() => navigate({ type: "symbol", id: s.id, file: fileIdx! })}>
                  <span className="kind">{s.kind === "module_scope" ? "▷" : s.kind}</span><span className="mono">{symbolName(s)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        <EdgeList title={t("outgoing")} items={out} navigate={goId} nameOf={nameOf} />
        <EdgeList title={t("incoming")} items={inc} navigate={goId} nameOf={nameOf} />
        <Source key={file.id} chunk={file.chunk} path={file.id} lang={file.lang} openFile={openFile}
                anchor={documentAnchor?.path === file.id ? documentAnchor.anchor : undefined} />
      </>
    );
  } else if (selection.type === "symbol" && file) {
    const all = new Map<string, SymbolNode>();
    for (const c of [chunk, ...chunks.values()]) for (const s of c?.symbols ?? []) all.set(s.id, s);
    const s = all.get(selection.id);
    const out = new Map<string, { id: string; type: string; count: number }>();
    const inc = new Map<string, { id: string; type: string; count: number }>();
    const mine = (id: string) => id === selection.id || id.startsWith(selection.id + ".");
    for (const c of [chunk, ...chunks.values()])
      for (const [a, b, type, , count] of c?.edges ?? []) {
        if (mine(a) && !mine(b)) out.set(b + type, { id: b, type, count });
        if (mine(b) && !mine(a)) inc.set(a + type, { id: a, type, count });
      }
    const kids = [...all.values()].filter((x) => x.parent === selection.id);
    body = s ? (
      <>
        <div className="eyebrow" onClick={() => navigate({ type: "file", index: fileIdx! })} style={{ cursor: "pointer" }}>
          <span className="mono">{file.id}</span>
        </div>
        <h2><span className="kind">{s.kind === "module_scope" ? "▷" : s.kind}</span> <span className="mono">{symbolName(s)}</span></h2>
        {s.kind === "module_scope" && <p className="module-scope-doc">{t("moduleScopeDoc")}</p>}
        {s.sig && <div className="sig mono">{s.sig}</div>}
        {s.doc && <div className="doc"><Markdown text={s.doc} path={file.id} openFile={openFile} /></div>}
        <Note text={s.note} stale={s.stale} path={file.id} openFile={openFile} />
        <Relations graph={graph} me={{ t: "symbol", id: s.id, file: fileIdx! }} navigate={navigate} path={file.id} openFile={openFile} />
        <div className="meta mono">{file.id}:{s.start}–{s.end}</div>
        {kids.length > 0 && (
          <section>
            <h4>{t("symbols")} <span className="muted">{kids.length}</span></h4>
            <ul className="list">
              {kids.map((k) => (
                <li key={k.id} onClick={() => navigate({ type: "symbol", id: k.id, file: fileIdx! })}>
                  <span className="kind">{k.kind}</span><span className="mono">{k.name}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        <EdgeList title={t("outgoing")} items={[...out.values()]} navigate={goId} nameOf={nameOf} />
        <EdgeList title={t("incoming")} items={[...inc.values()]} navigate={goId} nameOf={nameOf} />
        <Source key={s.id} chunk={file.chunk} path={file.id} lang={file.lang} from={s.start} to={s.end} openFile={openFile}
                moduleScope={s.kind === "module_scope" ? s : undefined} />
      </>
    ) : <div className="muted">…</div>;
  } else {
    body = <div className="muted">…</div>;
  }

  return (
    <aside className="panel" ref={panelRef} style={{ width, "--panel-scale": textZoom } as React.CSSProperties}>
      <div className="panel-resize" role="separator" aria-orientation="vertical" aria-label={t("resizePanel")}
           aria-valuemin={240} aria-valuemax={window.innerWidth - 24} aria-valuenow={Math.round(width)} tabIndex={0}
           title={t("resizePanel")}
           onPointerDown={(e) => {
             if (e.button !== 0) return;
             e.preventDefault();
             drag.current = { x: e.clientX, width };
             e.currentTarget.setPointerCapture(e.pointerId);
           }}
           onPointerMove={(e) => {
             if (drag.current) setWidth(Math.max(240, Math.min(window.innerWidth - 24, drag.current.width + drag.current.x - e.clientX)));
           }}
           onPointerUp={(e) => { drag.current = null; e.currentTarget.releasePointerCapture(e.pointerId); }}
           onLostPointerCapture={() => { drag.current = null; }}
           onKeyDown={(e) => {
             if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
             e.preventDefault();
             setWidth(Math.max(240, Math.min(window.innerWidth - 24, width + (e.key === "ArrowLeft" ? 24 : -24))));
           }} />
      <div className="panel-toolbar">
        <span className="muted">{t("detail")}</span>
        <div className="panel-zoom" title={t("panelZoomHint")}>
          <button onClick={() => changeZoom(-0.1)} disabled={textZoom <= 0.75} aria-label={t("zoomOut")}>−</button>
          <button onClick={() => setTextZoom(1)} aria-label={t("resetZoom")}>{Math.round(textZoom * 100)}%</button>
          <button onClick={() => changeZoom(0.1)} disabled={textZoom >= 2} aria-label={t("zoomIn")}>＋</button>
        </div>
        <button className="close" onClick={close} aria-label={t("close")}>×</button>
      </div>
      <div className="panel-content">{body}</div>
    </aside>
  );
}
