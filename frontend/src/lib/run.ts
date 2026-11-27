/* Pure reducer: a list of run events → everything the run views display.
 * Live runs and replays use the same function, so a replay is faithful. */
import type { Iteration, Report, RunEvent } from "./api";

export const STAGES = [
  { key: "analyze", label: "Analyze", who: "Analyzer", icon: "🔍" },
  { key: "plan", label: "Plan", who: "Planner", icon: "📋" },
  { key: "write", label: "Write", who: "Writer", icon: "✍️" },
  { key: "execute", label: "Execute", who: "Executor · Fixer", icon: "▶️" },
  { key: "mutate", label: "Mutate", who: "Mutation Critic", icon: "🧬" },
  { key: "blindspot", label: "Blind spots", who: "Blind Spot Intelligence", icon: "🧠" },
  { key: "refine", label: "Refine", who: "Writer", icon: "🔁" },
] as const;
export type StageKey = (typeof STAGES)[number]["key"];

export const AGENT_ICON: Record<string, string> = {
  Analyzer: "🔍", Planner: "📋", Writer: "✍️", Executor: "▶️", Fixer: "🔧", "Mutation Critic": "🧬", Orchestrator: "🎯",
  "Blind Spot Intelligence": "🧠",
};

/** Blind Spot Intelligence events of a generate run (live or replayed). */
export interface RunBlindSpot {
  enabled: boolean; events: { kind: string; data: any; t: number; seq: number }[];
  discovered: number; resolved: number; corrective: { name: string; blind_spot_id: string; origin: string; code: string; resolved: boolean }[];
}

export interface FeedItem { id: number; t: number; icon: string; who: string; what: string; tone: "phase" | "agent" | "error" | "done" }

export interface RunView {
  stage: StageKey | null; reached: Set<StageKey>; finished: boolean; failed: boolean;
  feed: FeedItem[]; iterations: Iteration[]; report: Report | null; stopReason: string | null; bs: RunBlindSpot;
}

export function reduceRun(events: RunEvent[]): RunView {
  const v: RunView = { stage: null, reached: new Set(), finished: false, failed: false, feed: [], iterations: [], report: null, stopReason: null,
    bs: { enabled: false, events: [], discovered: 0, resolved: 0, corrective: [] } };
  events.forEach((e, i) => {
    const t = e.t ?? 0;
    if (e.kind === "phase") {
      if (e.data.phase === "done") {
        v.finished = true; v.stage = null; v.stopReason = String(e.data.detail || "").replace(/_/g, " ");
        STAGES.forEach((s) => v.reached.add(s.key));
        v.feed.push({ id: i, t, icon: "✅", who: "Done", what: `Stopped: ${v.stopReason}`, tone: "done" });
      } else {
        const key = e.data.phase as StageKey;
        if (v.stage) v.reached.add(v.stage);
        v.stage = key;
        v.feed.push({ id: i, t, icon: "▸", who: key === "blindspot" ? "Blind Spot Intelligence" : key[0].toUpperCase() + key.slice(1), what: e.data.detail || "started", tone: "phase" });
      }
    } else if (e.kind === "agent") {
      v.feed.push({ id: i, t, icon: e.data.emoji || AGENT_ICON[e.data.agent] || "•", who: e.data.agent, what: e.data.message, tone: "agent" });
    } else if (e.kind === "error") {
      v.failed = true;
      v.feed.push({ id: i, t, icon: "⚠", who: "Error", what: e.data.message || e.data.detail || "error", tone: "error" });
    } else if (e.kind === "iteration") {
      v.iterations = [...v.iterations, e.data as Iteration];
    } else if (e.kind === "blindspot") {
      const d = e.data;
      v.bs.enabled = true;
      v.bs.events.push({ kind: "blindspot", data: d, t, seq: i });
      if (d.type === "blind_spot_discovered") v.bs.discovered++;
      if (d.type === "blind_spot_resolved") {
        v.bs.resolved++;
        v.bs.corrective = v.bs.corrective.map((c) => (c.blind_spot_id === d.blind_spot_id ? { ...c, resolved: true } : c));
      }
      if (d.type === "corrective_test_generated") v.bs.corrective.push({ name: d.test_name, blind_spot_id: d.blind_spot_id, origin: d.origin, code: d.code, resolved: false });
      if (["blind_spot_discovered", "blind_spot_resolved", "counterexample_found", "analysis_started"].includes(d.type))
        v.feed.push({ id: i, t, icon: "🧠", who: "Blind Spot Intelligence", what: d.text, tone: "agent" });
    } else if (e.kind === "report") {
      v.report = e.data as Report;
      v.finished = true;
    }
  });
  return v;
}

export const fmtT = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
