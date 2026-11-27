/* Behavioural blind spot heatmap: functions × behavioural categories.
 * Cell colours come from the backend's cell status; colour changes are
 * animated, newly discovered/resolved cells get a one-shot radar ring. */
import { m, useReducedMotion } from "framer-motion";
import clsx from "clsx";
import { memo } from "react";
import { CELL_STYLE, type CellStatus, type HeatCell, type Heatmap as HM } from "../../lib/blindspot";
import { spring } from "../../motion/variants";
import { Tooltip } from "../ui/display";

export const SHORT: Record<string, string> = {
  boundary_conditions: "Boundary", conditional_logic: "Logic", arithmetic: "Arithmetic", return_value: "Return value",
  exception_handling: "Exceptions", input_validation: "Validation", state_transitions: "State", assertion_quality: "Assertions",
  unexplored_paths: "Unexplored",
};

export interface Pulse { key: string; id: number; kind: "discovered" | "resolved" }

const Cell = memo(function Cell({ cell, selected, dim, pulse, compact, onSelect }: {
  cell: HeatCell; selected: boolean; dim: boolean; pulse?: Pulse; compact?: boolean; onSelect?: (c: HeatCell) => void;
}) {
  const reduce = useReducedMotion();
  const st = CELL_STYLE[cell.status as CellStatus] ?? CELL_STYLE.not_evaluated;
  const n = cell.mutants.length;
  const text = `${cell.function} · ${SHORT[cell.category] ?? cell.category}: ${st.label}. ${n} mutant(s), ${cell.killed} killed, ${cell.survived} survived` +
    (cell.blind_spots.length ? `, blind spots ${cell.blind_spots.join(", ")}` : "") + (cell.resolved ? `, ${cell.resolved} resolved` : "");
  return (
    <Tooltip text={text}>
      <m.button type="button" aria-label={text} onClick={() => onSelect?.(cell)} disabled={!onSelect}
        layoutId={compact ? undefined : `cell-${cell.function}-${cell.category}`}
        animate={{ backgroundColor: st.color, opacity: dim ? 0.25 : 1, scale: selected ? 1.08 : 1 }}
        whileHover={onSelect ? { scale: 1.12, zIndex: 2 } : undefined} whileTap={onSelect ? { scale: 0.96 } : undefined}
        transition={{ ...spring.snappy, backgroundColor: { duration: 0.7 } }}
        className={clsx("relative grid place-items-center rounded-md font-bold text-white/95 outline-none focus-visible:ring-2 focus-visible:ring-accent",
          compact ? "h-5 w-full text-[0px]" : "h-9 w-full text-[11px]", selected && "ring-2 ring-white/80")}>
        {!compact && (cell.survived - cell.resolved > 0 ? cell.survived - cell.resolved : n)}
        {pulse && !reduce && (
          <m.span key={pulse.id} className="pointer-events-none absolute inset-0 rounded-md border-2"
            style={{ borderColor: pulse.kind === "resolved" ? "rgb(var(--ok))" : st.ring }}
            initial={{ scale: 1, opacity: 0.9 }} animate={{ scale: 2.1, opacity: 0 }} transition={{ duration: 1.1, ease: "easeOut" }} />
        )}
      </m.button>
    </Tooltip>
  );
});

export function Heatmap({ data, selected, onSelect, pulses, compact, match }: {
  data: HM | null | undefined; selected?: string | null; onSelect?: (c: HeatCell) => void; pulses?: Record<string, Pulse>;
  compact?: boolean; match?: (c: HeatCell) => boolean;
}) {
  if (!data || data.rows.length === 0) return <div className="py-6 text-center text-[13px] text-muted">No mutants analysed yet.</div>;
  const cols = data.columns;
  const byKey = new Map(data.cells.map((c) => [`${c.function}|${c.category}`, c]));
  return (
    <div className="overflow-x-auto">
      <div className="grid min-w-max gap-1" style={{ gridTemplateColumns: `${compact ? "minmax(70px,110px)" : "minmax(96px,150px)"} repeat(${cols.length}, minmax(${compact ? 20 : 40}px, 1fr))` }}>
        <div />
        {cols.map((c) => (
          <div key={c.key} title={c.label} className={clsx("truncate text-center font-semibold text-muted", compact ? "text-[0px]" : "pb-1 text-[10px]")}>{SHORT[c.key] ?? c.label}</div>
        ))}
        {data.rows.map((row) => (
          <div key={row} className="contents">
            <div className={clsx("truncate pr-2 font-mono text-ink-2", compact ? "text-[10px] leading-5" : "text-[11.5px] leading-9")} title={row}>{row}</div>
            {cols.map((c) => {
              const cell = byKey.get(`${row}|${c.key}`);
              const key = `${row}|${c.key}`;
              return cell ? (
                <Cell key={key} cell={cell} compact={compact} selected={selected === key} onSelect={onSelect}
                  dim={match ? !match(cell) : false} pulse={pulses?.[key]} />
              ) : (
                <div key={key} className={clsx("rounded-md border border-dashed border-line/70", compact ? "h-5" : "h-9")} />
              );
            })}
          </div>
        ))}
      </div>
      {!compact && (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[11.5px] text-muted">
          {(Object.keys(CELL_STYLE) as CellStatus[]).map((k) => (
            <span key={k} className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-sm" style={{ background: CELL_STYLE[k].color }} />{CELL_STYLE[k].label}</span>
          ))}
        </div>
      )}
    </div>
  );
}
