/* Animated agent network: Mutation Critic → Blind Spot Intelligence → Writer → Executor → Critic.
 * Node states and signal sweeps are derived from real engine events
 * (agentStates); nothing animates on a timer. */
import { AnimatePresence, m, useReducedMotion } from "framer-motion";
import clsx from "clsx";
import { memo } from "react";
import type { AgentState, Signal } from "../../lib/blindspot";
import { spring } from "../../motion/variants";
import { useDraw } from "../../motion/useDraw";

/** One signal: the edge draws itself (SVG pathLength), then fades. */
function Sweep({ d }: { d: string }) {
  const len = useDraw(1, { duration: 1.1, ease: [0.16, 1, 0.3, 1] });
  return <m.path d={d} fill="none" stroke="url(#bs-sig)" strokeWidth={3} strokeLinecap="round" style={{ pathLength: len }}
    initial={{ opacity: 0.95 }} animate={{ opacity: [0.95, 0.95, 0] }} exit={{ opacity: 0 }} transition={{ duration: 1.4, times: [0, 0.7, 1] }} />;
}

type Key = "critic" | "bsi" | "writer" | "executor";
const NODES: Record<Key, { x: number; y: number; label: string; icon: string; role: string }> = {
  critic: { x: 90, y: 130, label: "Mutation Critic", icon: "🧬", role: "plants faults" },
  bsi: { x: 300, y: 42, label: "Blind Spot Intelligence", icon: "🧠", role: "diagnoses misses" },
  writer: { x: 510, y: 130, label: "Writer", icon: "✍️", role: "corrective tests" },
  executor: { x: 300, y: 218, label: "Executor", icon: "▶️", role: "runs & verifies" },
};
const EDGES: { from: Key; to: Key; d: string }[] = [
  { from: "critic", to: "bsi", d: "M 120 112 C 160 60, 220 44, 250 44" },
  { from: "bsi", to: "writer", d: "M 350 44 C 380 44, 440 60, 480 112" },
  { from: "writer", to: "executor", d: "M 480 148 C 440 200, 380 216, 350 216" },
  { from: "executor", to: "critic", d: "M 250 216 C 220 216, 160 200, 120 148" },
  { from: "bsi", to: "executor", d: "M 296 64 C 288 110, 288 150, 296 196" },
  { from: "executor", to: "bsi", d: "M 304 196 C 312 150, 312 110, 304 64" },
];
const STATE_TEXT: Record<AgentState, string> = { idle: "idle", analyzing: "analyzing", refining: "refining", verifying: "verifying", completed: "completed", failed: "failed" };
const STATE_TONE: Record<AgentState, string> = {
  idle: "text-muted", analyzing: "text-intel", refining: "text-accent", verifying: "text-warn", completed: "text-ok", failed: "text-bad",
};

export const AgentNetwork = memo(function AgentNetwork({ states, signals, compact }: { states: Record<Key, AgentState>; signals: Signal[]; compact?: boolean }) {
  const reduce = useReducedMotion();
  return (
    <div className={clsx("relative w-full", compact ? "aspect-[600/250]" : "aspect-[600/262]")}>
      <svg viewBox="0 0 600 262" className="absolute inset-0 h-full w-full" aria-hidden="true">
        <defs>
          <linearGradient id="bs-sig" x1="0" x2="1"><stop offset="0" stopColor="rgb(var(--intel))" /><stop offset="1" stopColor="rgb(var(--accent))" /></linearGradient>
        </defs>
        {EDGES.map((e) => (
          <path key={`${e.from}-${e.to}`} d={e.d} fill="none" stroke="rgb(var(--line-2))" strokeWidth={1.5} strokeDasharray="4 6" opacity={0.7} />
        ))}
        <AnimatePresence>
          {signals.map((s) => {
            const edge = EDGES.find((e) => e.from === s.from && e.to === s.to);
            if (!edge) return null;
            return (
              <Sweep key={s.id} d={edge.d} />
            );
          })}
        </AnimatePresence>
      </svg>
      {(Object.keys(NODES) as Key[]).map((k) => {
        const n = NODES[k], st = states[k];
        const active = st === "analyzing" || st === "refining" || st === "verifying";
        const intel = k === "bsi";
        return (
          <div key={k} className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: `${(n.x / 600) * 100}%`, top: `${(n.y / 262) * 100}%` }}>
            <div className="relative flex flex-col items-center gap-1">
              <div className="relative">
                {active && !reduce && (
                  <m.span className={clsx("absolute inset-0 rounded-2xl border-2", intel ? "border-intel" : "border-accent")}
                    initial={{ scale: 1, opacity: 0.7 }} animate={{ scale: 1.45, opacity: 0 }} transition={{ repeat: Infinity, duration: 1.6, ease: "easeOut" }} />
                )}
                <m.div animate={{ scale: active ? 1.06 : 1, boxShadow: active ? `0 0 0 1px rgb(var(--${intel ? "intel" : "accent"}) / .5), 0 0 28px -4px rgb(var(--${intel ? "intel" : "accent"}) / .7)` : "0 0 0 1px rgb(var(--line) / 1)" }}
                  transition={spring.smooth}
                  className={clsx("grid place-items-center rounded-2xl text-[20px]", compact ? "h-10 w-10" : "h-12 w-12",
                    intel ? "bg-gradient-to-br from-intel/30 to-intel-2/20" : "bg-panel-2", st === "failed" && "ring-2 ring-bad")}>
                  {n.icon}
                </m.div>
              </div>
              <div className={clsx("whitespace-nowrap text-center font-semibold", compact ? "text-[10.5px]" : "text-[12px]", intel && "text-intel")}>{n.label}</div>
              <AnimatePresence mode="wait" initial={false}>
                <m.div key={st} initial={{ opacity: 0, y: -3 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 3 }}
                  className={clsx("text-[10.5px] font-medium uppercase tracking-wide", STATE_TONE[st])}>{STATE_TEXT[st]}</m.div>
              </AnimatePresence>
            </div>
          </div>
        );
      })}
    </div>
  );
});
