import { useEffect, useRef, useState } from "react";
import type { Graph, Tour } from "./types";
import { t, type Key } from "./i18n";
import Markdown from "./Markdown";

const STATE_KEY: Record<string, Key> = { stale: "stale", orphan: "orphan", broken: "broken" };

export function TourMenu({ graph, start }: { graph: Graph; start: (i: number) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const off = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", off);
    return () => document.removeEventListener("mousedown", off);
  }, []);
  if (!graph.tours.length) return null;
  return (
    <div className="tour-menu" ref={ref}>
      <button className="fit" onClick={() => setOpen(!open)}>{t("tours")} {graph.tours.length}</button>
      {open && (
        <ul className="results">
          {graph.tours.map((tour, i) => (
            <li key={tour.id} onMouseDown={() => { setOpen(false); start(i); }}>
              <span>{tour.title}{tour.state && <span className="badge warn">{t(STATE_KEY[tour.state])}</span>}</span>
              <span className="muted">{tour.steps.length} {t("steps")}{tour.summary ? ` · ${tour.summary}` : ""}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function TourBar({ tour, step, go, exit }: { tour: Tour; step: number; go: (s: number) => void; exit: () => void }) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "ArrowRight" && step < tour.steps.length - 1) go(step + 1);
      else if (e.key === "ArrowLeft" && step > 0) go(step - 1);
      else if (e.key === "Escape") exit();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [tour, step]);
  const s = tour.steps[step];
  const where = s.at.includes("#") ? s.at.split("#")[1] : s.at.split("/").pop();
  return (
    <div className="tour-bar">
      <div className="tour-head">
        <strong>{tour.title}</strong>
        <span className="muted mono">{step + 1} / {tour.steps.length}</span>
        <button className="close" onClick={exit} aria-label={t("exitTour")}>×</button>
      </div>
      <div className="tour-where mono">{where}{s.state && <span className="badge warn">{t(STATE_KEY[s.state] ?? "stale")}</span>}</div>
      {s.text && <div className="tour-text"><Markdown text={s.text} /></div>}
      <div className="tour-nav">
        <button className="fit" disabled={step === 0} onClick={() => go(step - 1)}>← {t("prev")}</button>
        <div className="tour-dots">
          {tour.steps.map((_, i) => <span key={i} className={i === step ? "on" : i < step ? "done" : ""} onClick={() => go(i)} />)}
        </div>
        <button className="fit" disabled={step === tour.steps.length - 1} onClick={() => go(step + 1)}>{t("next")} →</button>
      </div>
    </div>
  );
}
