import { useEffect, useMemo, useState } from "react";
import type { Graph, Selection } from "./types";
import { loadSearch, type SearchRow } from "./data";
import { t } from "./i18n";
import { symbolName } from "./symbolName";

interface Hit { label: string; sub: string; sel: Selection; score: number }

export default function Search({ graph, navigate }: { graph: Graph; navigate: (s: Selection) => void }) {
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const [rows, setRows] = useState<SearchRow[]>([]);
  const [wanted, setWanted] = useState(false);
  useEffect(() => { if (wanted) loadSearch().then(setRows); }, [wanted]);
  const hits = useMemo<Hit[]>(() => {
    const s = q.trim().toLowerCase();
    if (s.length < 2) return [];
    const out: Hit[] = [];
    graph.files.forEach((f, i) => {
      const p = f.id.toLowerCase();
      const name = p.slice(p.lastIndexOf("/") + 1);
      if (p.includes(s)) out.push({ label: f.id.split("/").pop()!, sub: f.id, sel: { type: "file", index: i },
        score: (name.startsWith(s) ? 3 : name.includes(s) ? 2 : 1) + f.rank });
    });
    for (const [name, kind, fi, id] of rows) {
      const label = symbolName({ kind, name }), n = label.toLowerCase();
      if (!n.includes(s) && !name.toLowerCase().includes(s)) continue;
      out.push({ label, sub: `${kind === "module_scope" ? t("moduleScope") : kind} · ${graph.files[fi].id}`, sel: { type: "symbol", id, file: fi },
        score: (n === s ? 4 : n.startsWith(s) ? 3 : 2) + (kind === "class" || kind === "function" ? 0.5 : 0) });
    }
    graph.regions.forEach((r, i) => {
      if (r.name.toLowerCase().includes(s)) out.push({ label: r.name, sub: r.path, sel: { type: "region", index: i }, score: 3.5 });
    });
    return out.sort((a, b) => b.score - a.score).slice(0, 12);
  }, [q, graph, rows]);

  const pick = (h: Hit) => { navigate(h.sel); setQ(""); };
  return (
    <div className="search">
      <input
        value={q} placeholder={t("search")} spellCheck={false}
        onFocus={() => setWanted(true)}
        onChange={(e) => { setQ(e.target.value); setCursor(0); setWanted(true); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setCursor((c) => Math.min(c + 1, hits.length - 1));
          else if (e.key === "ArrowUp") setCursor((c) => Math.max(c - 1, 0));
          else if (e.key === "Enter" && hits[cursor]) pick(hits[cursor]);
          else if (e.key === "Escape") setQ("");
        }}
      />
      {hits.length > 0 && (
        <ul className="results">
          {hits.map((h, i) => (
            <li key={i} className={i === cursor ? "on" : ""} onMouseDown={() => pick(h)}>
              <span className="mono">{h.label}</span><span className="muted">{h.sub}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
