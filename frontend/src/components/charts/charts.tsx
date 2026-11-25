/* Lightweight SVG charts. Lines draw themselves (pathLength), points spring
 * into place when data changes, and every mark has a hover tooltip. */
import { AnimatePresence, m } from "framer-motion";
import { useMemo, useState } from "react";
import { pct } from "../../lib/api";
import { useDraw } from "../../motion/useDraw";

function DrawnPath({ d, color, width }: { d: string; color: string; width: number }) {
  const length = useDraw(1);
  return <m.path d={d} fill="none" stroke={color} strokeWidth={width} strokeLinejoin="round" strokeLinecap="round" style={{ pathLength: length }} />;
}

export interface Series { key: string; label: string; color: string; points: (number | null)[]; emphasis?: boolean }

export function LineChart({ series, xLabels, height = 250, yFormat = (v: number) => `${Math.round(v * 100)}%`, empty }: {
  series: Series[]; xLabels: string[]; height?: number; yFormat?: (v: number) => string; empty?: string;
}) {
  const W = 640, H = height, L = 46, R = 46, T = 16, B = 34;
  const n = Math.max(xLabels.length, 2);
  const x = (i: number) => L + ((W - L - R) * i) / (n - 1);
  const y = (v: number) => T + (H - T - B) * (1 - v);
  const [hover, setHover] = useState<number | null>(null);
  const paths = useMemo(() => series.map((s) => {
    const pts = s.points.map((v, i) => (v == null ? null : [x(i), y(v)] as const)).filter(Boolean) as [number, number][];
    return { ...s, pts, d: pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ") };
  }), [series, n]); // eslint-disable-line react-hooks/exhaustive-deps
  const hasData = series.some((s) => s.points.some((v) => v != null));
  const emph = paths.find((p) => p.emphasis);

  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Scores per round" onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" style={{ stopColor: "rgb(var(--accent))" }} stopOpacity=".28" /><stop offset="1" style={{ stopColor: "rgb(var(--accent))" }} stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="rgb(var(--line))" strokeDasharray={v ? "3 5" : undefined} />
            <text x={L - 8} y={y(v) + 4} textAnchor="end" fontSize="11" fill="rgb(var(--muted))">{yFormat(v)}</text>
          </g>
        ))}
        {xLabels.map((l, i) => <text key={i} x={x(i)} y={H - 10} textAnchor="middle" fontSize="11" fill="rgb(var(--muted))">{l}</text>)}
        {emph && emph.pts.length > 1 && (
          <m.path key={`area-${emph.pts.length}`} d={`${emph.d} L${emph.pts[emph.pts.length - 1][0]},${y(0)} L${emph.pts[0][0]},${y(0)} Z`}
            fill="url(#areaGrad)" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.8 }} />
        )}
        {paths.map((p) => p.pts.length > 1 && (
          <DrawnPath key={`${p.key}-${p.pts.length}`} d={p.d} color={p.color} width={p.emphasis ? 3 : 2} />
        ))}
        {paths.map((p) => p.pts.map(([cx, cy], i) => (
          <m.circle key={`${p.key}-${i}`} r={p.emphasis ? 5 : 4} fill={p.color} stroke="rgb(var(--panel))" strokeWidth="2"
            initial={{ cx, cy: y(0), opacity: 0 }} animate={{ cx, cy, opacity: 1 }} transition={{ type: "spring", stiffness: 160, damping: 18, delay: i * 0.04 }} />
        )))}
        {emph && emph.pts.length > 0 && (
          <m.text key={emph.pts.length} x={emph.pts[emph.pts.length - 1][0] + 9} y={emph.pts[emph.pts.length - 1][1] + 4} fontSize="12.5" fontWeight="700"
            fill="rgb(var(--ink))" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.5 }}>
            {yFormat(emph.points.filter((v) => v != null).slice(-1)[0] as number)}
          </m.text>
        )}
        {hasData && xLabels.map((_, i) => (
          <rect key={i} x={x(i) - (W - L - R) / (2 * (n - 1))} y={T} width={(W - L - R) / (n - 1)} height={H - T - B} fill="transparent" onMouseEnter={() => setHover(i)} />
        ))}
        {hover != null && <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} stroke="rgb(var(--line-2))" strokeDasharray="3 3" pointerEvents="none" />}
        {!hasData && <text x={W / 2} y={H / 2} textAnchor="middle" fontSize="12.5" fill="rgb(var(--muted))">{empty || "No data yet"}</text>}
      </svg>
      <AnimatePresence>
        {hover != null && hasData && (
          <m.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="pointer-events-none absolute top-2 rounded-lg border border-line-2 bg-panel/95 px-3 py-2 text-[12px] shadow-card backdrop-blur"
            style={{ left: `${Math.min(70, (x(hover) / W) * 100)}%` }}>
            <div className="mb-1 font-semibold">{xLabels[hover]}</div>
            {series.map((s) => (
              <div key={s.key} className="flex items-center gap-2 text-ink-2">
                <span className="h-2 w-2 rounded-full" style={{ background: s.color }} />{s.label}
                <span className="ml-auto pl-3 font-semibold tabular-nums text-ink">{s.points[hover] == null ? "–" : yFormat(s.points[hover] as number)}</span>
              </div>
            ))}
          </m.div>
        )}
      </AnimatePresence>
      <div className="mt-2 flex flex-wrap gap-4 text-[12px] text-ink-2">
        {series.map((s) => <span key={s.key} className="flex items-center gap-2"><i className="h-[3px] w-3.5 rounded-full" style={{ background: s.color }} />{s.label}</span>)}
      </div>
    </div>
  );
}

/** Horizontal bars, one group per row; widths spring in and on data change. */
export function BarChart({ rows, keys, format = pct }: {
  rows: { label: string; values: Record<string, number | null>; sub?: string }[];
  keys: { key: string; label: string; color: string }[]; format?: (v: number | null) => string;
}) {
  return (
    <div className="flex flex-col gap-3">
      {rows.map((r, ri) => (
        <div key={r.label} className="grid grid-cols-[minmax(90px,150px)_minmax(0,1fr)] items-center gap-3">
          <div className="min-w-0"><div className="truncate text-[12.5px] font-semibold" title={r.label}>{r.label}</div>
            {r.sub && <div className="truncate text-[11px] text-muted">{r.sub}</div>}</div>
          <div className="flex flex-col gap-1">
            {keys.map((k, ki) => {
              const v = r.values[k.key];
              return (
                <div key={k.key} className="flex items-center gap-2" title={`${k.label}: ${format(v)}`}>
                  <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-panel-2">
                    <m.div className="h-full rounded-full" style={{ background: k.color }}
                      initial={{ width: 0 }} animate={{ width: `${Math.max(0, Math.min(1, v ?? 0)) * 100}%` }}
                      transition={{ type: "spring", stiffness: 90, damping: 20, delay: ri * 0.03 + ki * 0.05 }} />
                  </div>
                  <span className="w-11 text-right text-[11.5px] font-semibold tabular-nums">{format(v)}</span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
      {keys.length > 1 && (
        <div className="mt-1 flex flex-wrap gap-4 text-[12px] text-ink-2">
          {keys.map((k) => <span key={k.key} className="flex items-center gap-2"><i className="h-2.5 w-2.5 rounded-sm" style={{ background: k.color }} />{k.label}</span>)}
        </div>
      )}
    </div>
  );
}

export const SERIES_COLORS = ["rgb(var(--accent))", "rgb(var(--ok))", "rgb(var(--warn))", "rgb(var(--accent-2))", "rgb(var(--info))", "rgb(var(--bad))"];
