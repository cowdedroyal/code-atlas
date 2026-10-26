import { useCallback, useEffect, useState } from "react";
import MapView, { fitView, type ViewState } from "./MapView";
import Panel from "./Panel";
import Search from "./Search";
import { TourBar, TourMenu } from "./Tour";
import { ExplainerReader } from "./Explainers";
import { loadChunk, loadGraph, loadSearch } from "./data";
import { EDGE_COLOR, ROLE_COLOR, ROLES } from "./palette";
import { t, type Key } from "./i18n";
import type { Chunk, Explainer, Graph, Level, LevelLock, Selection } from "./types";

function useWindowSize() {
  const [size, setSize] = useState({ w: window.innerWidth, h: window.innerHeight });
  useEffect(() => {
    const on = () => setSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return size;
}

export default function App() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chunks, setChunks] = useState<Map<number, Chunk>>(new Map());
  const [selection, setSelection] = useState<Selection>(null);
  const [level, setLevel] = useState<Level>(0);
  const [levelLock, setLevelLock] = useState<LevelLock | null>(null);
  const [panelWidth, setPanelWidth] = useState(400);
  const [reading, setReading] = useState<Explainer | null>(null);
  const [dark, setDark] = useState(() => localStorage.getItem("codeatlas-theme") === "dark" ||
    (!localStorage.getItem("codeatlas-theme") && window.matchMedia("(prefers-color-scheme: dark)").matches));
  useEffect(() => { document.documentElement.dataset.theme = dark ? "dark" : "light"; }, [dark]);
  const size = useWindowSize();
  const actualPanelWidth = Math.min(panelWidth, Math.max(240, size.w - 24));
  const [vs, setVs] = useState<ViewState>({ target: [0, 0, 0], zoom: 0 });

  useEffect(() => {
    loadGraph().then((g) => {
      setGraph(g);
      const root = g.regions.find((r) => r.parent === null)!;
      setVs({ ...fitView(root.box, size), transitionDuration: 0 });
    }).catch((e) => setError(String(e)));
  }, []);

  const requestChunks = useCallback((ids: number[]) => {
    Promise.all(ids.map((i) => loadChunk(i).then((c) => [i, c] as const))).then((loaded) => {
      setChunks((prev) => {
        const next = new Map(prev);
        for (const [i, c] of loaded) next.set(i, c);
        return next;
      });
    });
  }, []);

  const navigate = useCallback((s: Selection) => {
    if (!graph || !s) return setSelection(s);
    setSelection(s);
    const free = { top: 64, right: actualPanelWidth + 24, bottom: 100 };
    if (s.type === "region") setVs(fitView(graph.regions[s.index].box, size, 0.92, free));
    else if (s.type === "file") setVs(fitView(graph.files[s.index].box, size, 0.5, free));
    else {
      // first frame the whole file; once the symbol's box is known, zoom in if it would be too small to read
      const f = graph.files[s.file];
      requestChunks([f.chunk]);
      setVs(fitView(f.box, size, 0.6, free));
      loadSearch().then((rows) => {
        const hit = rows.find((x) => x[3] === s.id);
        if (!hit) return;
        const box = [hit[4], hit[5], hit[6], hit[7]] as [number, number, number, number];
        if (box[2] * Math.min((size.w - free.right) / f.box[2], size.h / f.box[3]) * 0.6 < 90) setVs(fitView(box, size, 0.3, free));
      });
    }
  }, [graph, size, requestChunks, actualPanelWidth]);

  // --- tours: one step at a time, framing the previous and current stop together when they are close ---
  const [tourAt, setTourAt] = useState<{ i: number; step: number } | null>(null);
  const goStep = useCallback((i: number, step: number) => {
    if (!graph) return;
    const tour = graph.tours[i], s = tour.steps[step];
    setTourAt({ i, step });
    const r = s.ref;
    const frame = (x: typeof s) => (x.ref.t === "symbol" ? graph.files[x.ref.file].box : x.box);
    if (r.t === "symbol") {
      setSelection({ type: "symbol", id: r.id, file: r.file });
      requestChunks([graph.files[r.file].chunk]);
    } else setSelection(r.t === "file" ? { type: "file", index: r.i } : { type: "region", index: r.i });
    const cur = frame(s);
    const prev = step > 0 ? frame(tour.steps[s.from ?? step - 1] ?? tour.steps[step - 1]) : null;
    const free = { top: 60, right: actualPanelWidth + 24, bottom: 200 };
    if (prev) {
      const x0 = Math.min(cur[0], prev[0]), y0 = Math.min(cur[1], prev[1]);
      const x1 = Math.max(cur[0] + cur[2], prev[0] + prev[2]), y1 = Math.max(cur[1] + cur[3], prev[1] + prev[3]);
      if (x1 - x0 <= cur[2] * 6 && y1 - y0 <= cur[3] * 6) return setVs(fitView([x0, y0, x1 - x0, y1 - y0], size, 0.85, free));
    }
    setVs(fitView(cur, size, 0.75, free));
  }, [graph, size, requestChunks, actualPanelWidth]);

  if (error) return <div className="splash">{error}</div>;
  if (!graph) return <div className="splash">{t("loading")}</div>;
  const root = graph.regions.find((r) => r.parent === null)!;
  const m = graph.meta;
  const activeTour = tourAt ? { tour: graph.tours[tourAt.i], step: tourAt.step } : null;

  return (
    <div className="app">
      <MapView graph={graph} chunks={chunks} requestChunks={requestChunks} viewState={vs} setViewState={setVs}
               size={size} selection={selection} select={setSelection} onLevel={setLevel} levelLock={levelLock} tour={activeTour} dark={dark} />
      <header className="top">
        <div className="brand"><strong>{m.repo}</strong><span className="muted mono">{m.commit?.slice(0, 7)}</span></div>
        <Search graph={graph} navigate={navigate} />
        <ol className="levels">
          {(["level0", "level1", "level2"] as Key[]).map((k, i) => (
            <li key={k} className={i === level ? "on" : ""}>
              <button aria-pressed={i === level} title={t("chooseLevelHint")}
                      onClick={() => setLevelLock({ level: i as Level, zoom: vs.zoom })}>L{i} {t(k)}</button>
            </li>
          ))}
        </ol>
        <button className={`fit level-lock${levelLock ? " locked" : ""}`} aria-pressed={!!levelLock}
                title={t(levelLock ? "unlockLevelHint" : "lockLevelHint")}
                onClick={() => setLevelLock((prev) => prev ? null : { level, zoom: vs.zoom })}>
          {levelLock ? `L${levelLock.level} ${t("locked")}` : t("lockLevel")}
        </button>
        <TourMenu graph={graph} start={(i) => goStep(i, 0)} />
        <button className="fit" onClick={() => { setDark(v => !v); localStorage.setItem("codeatlas-theme", dark ? "light" : "dark"); }}>
          {t(dark ? "lightMode" : "darkMode")}</button>
        <button className="fit" onClick={() => { setSelection(null); setVs(fitView(root.box, size)); }}>{t("fit")}</button>
      </header>
      {activeTour && <TourBar tour={activeTour.tour} step={activeTour.step} go={(s) => goStep(tourAt!.i, s)}
                              exit={() => setTourAt(null)} />}
      <footer className="legend" style={activeTour ? { display: "none" } : undefined}>
        <div className="legend-row">
          <span className="legend-title">{t("roles")}</span>
          {ROLES.map((r) => <span key={r} className="item"><span className="swatch" style={{ background: ROLE_COLOR[r] }} />{t(r as Key)}</span>)}
        </div>
        <div className="legend-row">
          <span className="legend-title">{t("edges")}</span>
          {["calls", "instantiates", "inherits", "references", "imports"].map((e) => (
            <span key={e} className="item"><span className="swatch line" style={{ background: EDGE_COLOR[e] }} />{e}</span>
          ))}
          {graph.relations.length > 0 && (
            <span className="item"><span className="swatch line" style={{ background: "#0b0b0b", height: 3 }} />{t("semantic")}</span>
          )}
          <span className="muted stats">
            {m.files_mapped} {t("files")} · {m.symbols} {t("symbols")} · {t("assets")}: {Object.values(m.assets).reduce((a, b) => a + b, 0)}
          </span>
        </div>
      </footer>
      {selection && <Panel graph={graph} chunks={chunks} selection={selection} navigate={navigate} close={() => setSelection(null)}
                           width={actualPanelWidth} setWidth={setPanelWidth} openCard={setReading} />}
      {reading && <ExplainerReader card={reading} graph={graph} dark={dark} close={() => setReading(null)} navigate={navigate} />}
    </div>
  );
}
