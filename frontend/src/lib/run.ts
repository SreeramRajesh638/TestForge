/* Pure reducer: a list of run events → everything the run views display.
 * Live runs and replays use the same function, so a replay is faithful. */
import type { Iteration, Report, RunEvent } from "./api";

export const STAGES = [
  { key: "analyze", label: "Analyze", who: "Analyzer", icon: "🔍" },
  { key: "plan", label: "Plan", who: "Planner", icon: "📋" },
  { key: "write", label: "Write", who: "Writer", icon: "✍️" },
  { key: "execute", label: "Execute", who: "Executor · Fixer", icon: "▶️" },
  { key: "mutate", label: "Mutate", who: "Mutation Critic", icon: "🧬" },
  { key: "refine", label: "Refine", who: "Writer", icon: "🔁" },
] as const;
export type StageKey = (typeof STAGES)[number]["key"];

export const AGENT_ICON: Record<string, string> = {
  Analyzer: "🔍", Planner: "📋", Writer: "✍️", Executor: "▶️", Fixer: "🔧", "Mutation Critic": "🧬", Orchestrator: "🎯",
};

export interface FeedItem { id: number; t: number; icon: string; who: string; what: string; tone: "phase" | "agent" | "error" | "done" }

export interface RunView {
  stage: StageKey | null; reached: Set<StageKey>; finished: boolean; failed: boolean;
  feed: FeedItem[]; iterations: Iteration[]; report: Report | null; stopReason: string | null;
}

export function reduceRun(events: RunEvent[]): RunView {
  const v: RunView = { stage: null, reached: new Set(), finished: false, failed: false, feed: [], iterations: [], report: null, stopReason: null };
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
        v.feed.push({ id: i, t, icon: "▸", who: key[0].toUpperCase() + key.slice(1), what: e.data.detail || "started", tone: "phase" });
      }
    } else if (e.kind === "agent") {
      v.feed.push({ id: i, t, icon: e.data.emoji || AGENT_ICON[e.data.agent] || "•", who: e.data.agent, what: e.data.message, tone: "agent" });
    } else if (e.kind === "error") {
      v.failed = true;
      v.feed.push({ id: i, t, icon: "⚠", who: "Error", what: e.data.message || e.data.detail || "error", tone: "error" });
    } else if (e.kind === "iteration") {
      v.iterations = [...v.iterations, e.data as Iteration];
    } else if (e.kind === "report") {
      v.report = e.data as Report;
      v.finished = true;
    }
  });
  return v;
}

export const fmtT = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
