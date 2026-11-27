/* Blind Spot Intelligence: typed API client and the live-session hook.
 * The server state is the source of truth; SSE events drive animations and
 * trigger (debounced) state refreshes, so nothing on screen is simulated. */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export type Primary = "reachability" | "infection" | "propagation" | "revealability" | "inconclusive";
export type CellStatus = "verified" | "blind_spot" | "investigating" | "analyzing" | "insufficient" | "not_evaluated";

export interface ObservedCall { test: string | null; call: string; original: string; mutant: string; original_state?: string | null; mutant_state?: string | null }
export interface Outcome { kind: "value" | "exception"; repr: string; literal?: string | null; state?: string | null; state_literal?: string | null }
export interface Counterexample { function: string; call: string; args: string[]; init: string[]; original: Outcome; mutant: Outcome; source: string; tried: number; alternatives: number }
export interface Evidence {
  reached: boolean | null; reaching_tests: string[]; executions: number; infection_supported: boolean; infection_point: string;
  infection_note: string; infected_evaluations: number; infection_examples: { original: string; mutant: string; infected: boolean }[];
  propagated_calls: ObservedCall[]; noisy_calls: number; diverged_tests: string[]; assertions: Record<string, { asserts: number; weak: string[]; checks_exception: boolean; line: number }>;
  unsupported: string[];
}
export interface Diagnosis {
  blind_spot_id: string; session_id: string; mutant_id: number; mutant_key: string; round: number; function_name: string | null;
  source_line: number; mutation_operator: string; original_code: string; mutated_code: string; behaviour_category: string;
  primary_category: Primary; secondary_labels: string[]; diagnosis: string; evidence: Evidence;
  evidence_strength: "strong" | "moderate" | "weak"; evidence_notes: string[]; finding_status: "confirmed" | "hypothesis" | "inconclusive";
  suggested_input: string | null; observed_original_output: string | null; observed_mutant_output: string | null;
  counterexample: Counterexample | null; counterexample_searched: boolean; corrective_strategy: string;
  proposed_test: string | null; proposed_test_name: string | null; test_origin: "counterexample" | "llm" | "writer" | null; llm_explanation: string | null;
  verification: { passes_on_original: boolean | null; kills_mutant: boolean | null; suite_regression_free: boolean | null; killed_by: string | null; detail: string };
  verification_status: "not_started" | "running" | "verified" | "failed";
  resolution_status: "unresolved" | "investigating" | "resolved" | "invalid_test" | "not_attempted";
  priority: number; priority_factors: Record<string, number>; attempts: number; duration_s: number;
}
export interface Summary {
  analyzed_mutants: number; surviving_mutants: number; discovered: number; resolved: number; unresolved: number; high_priority: number;
  verified_corrective_tests: number; score_before: number | null; score_after: number | null; resolution_rate: number | null;
  confirmed: number; hypotheses: number; inconclusive: number; by_primary: Record<Primary, number>; potential_equivalent: number; status: string;
}
export interface HeatCell { function: string; category: string; mutants: number[]; killed: number; survived: number; blind_spots: string[]; resolved: number; operators: string[]; lines: number[]; status: CellStatus }
export interface Heatmap { rows: string[]; columns: { key: string; label: string }[]; cells: HeatCell[] }
export interface TimelineItem { t: number; type: string; text: string; round: number; blind_spot_id?: string; mutant_id?: number; [k: string]: unknown }
export interface MutantRow { id: number; operator: string; lineno: number; original: string; mutated: string; function: string | null; status: string; killed_by: string | null }
export interface Version { label: string; tests: string; test_count: number; score: number | null; t: number }
export interface SessionState {
  id: string; session_id: string; module: string; module_name: string; source: string; tests: string; original_tests: string;
  summary: Summary; heatmap: Heatmap; scores: { round: number; score: number; killed: number; total: number; label: string }[];
  diagnoses: Diagnosis[]; timeline: TimelineItem[]; investigating: number[]; versions: Version[]; status: "idle" | "running" | "cancelled" | "failed";
  action: string | null; error: string | null; origin: string; from_run: string | null; llm: string; model: string; live: boolean; mutants: MutantRow[]; events_count?: number;
}
export interface SessionRow { id: string; created: number; module: string; origin: string; status: string; live: boolean; discovered: number; resolved: number; unresolved: number; score_before: number | null; score_after: number | null }
export interface Overview { source: "session" | "run" | null; id?: string; module?: string; summary?: Summary; heatmap?: Heatmap; status?: string }
export interface BsEvent { kind: string; data: any; t: number; seq: number }
export interface TryResult { function: string; call: string; args: string[]; original: Outcome; mutant: Outcome; differs: boolean }

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
  if (!res.ok) {
    let msg = await res.text();
    try { const d = JSON.parse(msg).detail; msg = typeof d === "string" ? d : JSON.stringify(d); } catch { /* plain */ }
    throw new ApiError(res.status, msg || res.statusText);
  }
  return res.json() as Promise<T>;
}
const post = <T,>(path: string, body?: unknown) => req<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
const S = (id: string) => `/api/blindspot/sessions/${id}`;

export const bsApi = {
  overview: () => req<Overview>("/api/blindspot/overview"),
  sessions: () => req<SessionRow[]>("/api/blindspot/sessions"),
  create: (body: Record<string, unknown>) => post<{ id: string; state: SessionState }>("/api/blindspot/sessions", body),
  state: (id: string) => req<SessionState>(S(id)),
  mutant: (id: string, mid: number) => req<{ mutant: MutantRow; original_source: string; mutated_source: string }>(`${S(id)}/mutants/${mid}`),
  analyze: (id: string) => post(`${S(id)}/analyze`),
  investigate: (id: string, mutant_id: number) => post(`${S(id)}/investigate`, { mutant_id }),
  corrective: (id: string, bs: string) => post(`${S(id)}/blindspots/${bs}/corrective`),
  execute: (id: string, bs: string) => post(`${S(id)}/blindspots/${bs}/execute`),
  verify: (id: string, bs: string) => post(`${S(id)}/blindspots/${bs}/verify`),
  tryInputs: (id: string, bs: string, args?: string[]) => post<TryResult>(`${S(id)}/blindspots/${bs}/counterexample`, { args: args ?? null }),
  resolveAll: (id: string) => post(`${S(id)}/resolve_all`),
  cancel: (id: string) => post<{ cancelled: boolean }>(`${S(id)}/cancel`),
  exportUrl: (id: string, format: "json" | "csv" | "html") => `${S(id)}/export?format=${format}`,
};

export const PRIMARY: Record<Primary, { label: string; short: string; tone: "bad" | "warn" | "intel" | "info" | "muted"; help: string }> = {
  reachability: { label: "Reachability failure", short: "Reach", tone: "bad", help: "No test executes the mutated code." },
  infection: { label: "Infection failure", short: "Infect", tone: "warn", help: "The code runs, but the inputs never make the mutated expression compute something different." },
  propagation: { label: "Propagation failure", short: "Propag.", tone: "intel", help: "The internal value differs, but it is masked before reaching any output or state." },
  revealability: { label: "Revealability failure", short: "Reveal", tone: "info", help: "An output already differs, but no assertion checks it." },
  inconclusive: { label: "Inconclusive", short: "?", tone: "muted", help: "The measurements do not establish why the mutant survives." },
};

export const CELL_STYLE: Record<CellStatus, { label: string; color: string; ring: string }> = {
  verified: { label: "Verified fault detection", color: "rgb(var(--ok) / .78)", ring: "rgb(var(--ok))" },
  blind_spot: { label: "Confirmed blind spot", color: "rgb(var(--bad) / .82)", ring: "rgb(var(--bad))" },
  investigating: { label: "Under investigation (not yet diagnosed)", color: "rgb(var(--warn) / .78)", ring: "rgb(var(--warn))" },
  analyzing: { label: "AI analysing or refining", color: "rgb(var(--intel) / .85)", ring: "rgb(var(--intel))" },
  insufficient: { label: "Insufficient evidence", color: "rgb(var(--muted) / .55)", ring: "rgb(var(--muted))" },
  not_evaluated: { label: "Not evaluated", color: "rgb(var(--line-2) / .6)", ring: "rgb(var(--line-2))" },
};

/** Agent states derived from real engine events (never from timers). */
export type AgentState = "idle" | "analyzing" | "refining" | "verifying" | "completed" | "failed";
export interface Signal { id: number; from: "critic" | "bsi" | "writer" | "executor"; to: "critic" | "bsi" | "writer" | "executor"; label: string }

export function agentStates(events: BsEvent[], running: boolean): { states: Record<"critic" | "bsi" | "writer" | "executor", AgentState>; signals: Signal[] } {
  const st: Record<"critic" | "bsi" | "writer" | "executor", AgentState> = { critic: "idle", bsi: "idle", writer: "idle", executor: "idle" };
  const signals: Signal[] = [];
  const done = (k: keyof typeof st) => { if (st[k] !== "failed") st[k] = "completed"; };
  for (const e of events) {
    const type = e.kind === "blindspot" ? e.data?.type : e.kind === "phase" ? `phase:${e.data?.phase}` : e.kind === "action" ? `action:${e.data?.state}` : e.kind;
    switch (type) {
      case "mutation_started": case "phase:mutate": st.critic = "analyzing"; break;
      case "survivors_detected": done("critic"); signals.push({ id: e.seq, from: "critic", to: "bsi", label: "survivors" }); break;
      case "analysis_started": case "phase:blindspot": st.bsi = "analyzing"; break;
      case "mutant_under_investigation": st.bsi = "analyzing"; st.executor = "analyzing"; signals.push({ id: e.seq, from: "bsi", to: "executor", label: "evidence run" }); break;
      case "evidence_collected": if (e.data?.mutant_id != null) signals.push({ id: e.seq, from: "executor", to: "bsi", label: "trace" }); break;
      case "corrective_test_generated": st.bsi = "refining"; st.writer = "refining"; signals.push({ id: e.seq, from: "bsi", to: "writer", label: "corrective test" }); break;
      case "phase:refine": st.writer = "refining"; break;
      case "verification_started": case "phase:execute": st.executor = "verifying"; signals.push({ id: e.seq, from: "writer", to: "executor", label: "verify" }); break;
      case "blind_spot_resolved": done("executor"); done("writer"); signals.push({ id: e.seq, from: "executor", to: "critic", label: "killed" }); break;
      case "analysis_completed": done("bsi"); if (st.executor === "analyzing") done("executor"); break;
      case "analysis_failed": st.bsi = "failed"; break;
      case "analysis_cancelled": st.bsi = "idle"; break;
    }
  }
  if (!running) (Object.keys(st) as (keyof typeof st)[]).forEach((k) => { if (st[k] === "analyzing" || st[k] === "refining" || st[k] === "verifying") st[k] = "completed"; });
  return { states: st, signals: signals.slice(-6) };
}

/** Live session: initial state + SSE stream with resume (?after=seq) on reconnect. */
export function useBlindSpotSession(id: string | null) {
  const [state, setState] = useState<SessionState | null>(null);
  const [events, setEvents] = useState<BsEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const lastSeq = useRef(-1);
  const refreshTimer = useRef<number | null>(null);

  const refresh = useCallback(() => {
    if (!id) return;
    bsApi.state(id).then((s) => { setState(s); setError(null); }).catch((e) => setError((e as Error).message));
  }, [id]);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current != null) return;
    refreshTimer.current = window.setTimeout(() => { refreshTimer.current = null; refresh(); }, 250);
  }, [refresh]);

  useEffect(() => {
    if (!id) { setState(null); setEvents([]); return; }
    setEvents([]); lastSeq.current = -1; refresh();
    let es: EventSource | null = null, closed = false, retry = 0, timer: number | undefined;
    const open = () => {
      es = new EventSource(`/api/blindspot/sessions/${id}/events?after=${lastSeq.current}`);
      es.onopen = () => { setConnected(true); retry = 0; };
      es.onmessage = (ev) => {
        const item = JSON.parse(ev.data) as BsEvent;
        if (item.seq <= lastSeq.current) return;
        lastSeq.current = item.seq;
        setEvents((xs) => (xs.length > 1500 ? [...xs.slice(-1200), item] : [...xs, item]));
        scheduleRefresh();
      };
      es.addEventListener("end", () => { es?.close(); setConnected(false); });
      es.onerror = () => {
        es?.close(); setConnected(false);
        if (closed) return;
        retry = Math.min(retry + 1, 6);
        timer = window.setTimeout(open, 500 * 2 ** retry);  // reconnect with backoff, resuming after lastSeq
      };
    };
    open();
    return () => { closed = true; es?.close(); window.clearTimeout(timer); if (refreshTimer.current) window.clearTimeout(refreshTimer.current); refreshTimer.current = null; };
  }, [id, refresh, scheduleRefresh]);

  return { state, events, error, connected, refresh };
}

export const fmtPct = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}%`);
