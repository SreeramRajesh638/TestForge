/* Typed client for the FastAPI backend. Field names mirror the Python models. */

export interface Iteration {
  iteration: number; phase: string; tests_total: number; tests_passing: number;
  line_coverage: number; branch_coverage: number; mutation_score: number | null;
  mutants_total: number; mutants_killed: number; mutants_survived: number; mutants_equivalent: number;
  fix_rounds: number; pruned_tests: number; input_tokens: number; output_tokens: number; elapsed_s: number;
}

export interface Survivor { id: number; operator: string; lineno: number; original: string; mutated: string; function: string | null; status: string }

export interface Report {
  module: { module: string; functions: number; classes: number; loc: number; total_complexity: number };
  config: { llm: string; model: string; [k: string]: unknown };
  iterations: Iteration[]; final_tests: string; stop_reason: string;
  total_input_tokens: number; total_output_tokens: number; cost_usd: number; elapsed_s: number;
  survivors: Survivor[];
}

export interface RunEvent { kind: string; data: any; t?: number }

export interface BackendStatus { ready: boolean; note: string; models?: string[] }
export type StatusMap = Record<"mock" | "groq" | "ollama" | "claude", BackendStatus>;

export interface Example { name: string; source: string }

export interface Scenario { name: string; tags: string[]; steps: string[] }
export interface Bug { id: string; description: string }

export interface StepResult { index: number; action: string; description: string; status: "passed" | "failed" | "skipped"; duration_ms: number; message: string }
export interface ScenarioResult {
  name: string; tags: string[]; status: "passed" | "failed" | "error"; duration_ms: number;
  steps: StepResult[]; message: string; screenshot_png_b64: string | null; had_screenshot?: boolean;
}

export interface TelemetryEvent {
  seq: number; t: number; kind: string; source: string; target: string; duration_ms: number | null;
  ok: boolean; detail: string; run_id: string | null; [k: string]: unknown;
}
export interface TelemetrySnapshot {
  started: number; uptime_s: number; events: TelemetryEvent[];
  totals: Record<string, { calls: number; errors: number; avg_ms: number | null }>;
  active_runs: { id: string; status: string }[]; max_runs: number;
}

export interface HistoryRow { id: string; kind: "generate" | "ui"; started: number; finished: number; summary: Record<string, any> }
export interface HistoryRecord extends HistoryRow { request: Record<string, any>; report: any; status: string; events: RunEvent[] }

export interface ExperimentInfo { name: string; runs: number; ok: number; modules: number; configs: string[]; updated: number }
export interface Experiment {
  name: string; configs: string[];
  summary: { config: string; runs: number; failed_runs: number; mutation_score_final: number | null; mutation_score_initial: number | null;
    line_coverage: number | null; branch_coverage: number | null; tests: number | null; elapsed_s: number | null; cost_usd: number | null }[];
  modules: { module: string; configs: Record<string, { final: number | null; initial: number | null; errors: number }> }[];
  curves: Record<string, { iteration: number; mutation_score: number | null; n: number }[]>;
  errors: { module: string; config: string; error: string }[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });
  if (!res.ok) {
    let msg = await res.text();
    try { const d = JSON.parse(msg).detail; msg = typeof d === "string" ? d : JSON.stringify(d); } catch { /* plain text */ }
    throw new ApiError(res.status, msg || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  status: () => request<StatusMap>("/api/status"),
  examples: () => request<Example[]>("/api/examples"),
  startRun: (body: Record<string, unknown>) => request<{ run_id: string }>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  scenarios: () => request<Scenario[]>("/api/ui/scenarios"),
  bugs: () => request<Bug[]>("/api/ui/bugs"),
  startUiRun: (body: Record<string, unknown>) => request<{ run_id: string; scenarios: number }>("/api/ui/runs", { method: "POST", body: JSON.stringify(body) }),
  telemetry: (limit = 200) => request<TelemetrySnapshot>(`/api/telemetry/snapshot?limit=${limit}`),
  history: () => request<HistoryRow[]>("/api/history"),
  historyRecord: (id: string) => request<HistoryRecord>(`/api/history/${id}`),
  deleteHistory: (id: string) => request<{ deleted: string }>(`/api/history/${id}`, { method: "DELETE" }),
  experiments: () => request<ExperimentInfo[]>("/api/experiments"),
  experiment: (name: string) => request<Experiment>(`/api/experiments/${encodeURIComponent(name)}`),
};

/** Subscribe to a run's Server-Sent Events until the server sends "end". */
export function followRun(runId: string, onEvent: (e: RunEvent) => void, onEnd: (ok: boolean) => void): () => void {
  const es = new EventSource(`/api/runs/${runId}/events`);
  let ended = false;
  es.onmessage = (ev) => onEvent(JSON.parse(ev.data));
  es.addEventListener("end", () => { ended = true; es.close(); onEnd(true); });
  es.onerror = () => { if (!ended) { es.close(); onEnd(false); } };
  return () => es.close();
}

export const pct = (v: number | null | undefined, digits = 0) => (v == null ? "–" : `${(v * 100).toFixed(digits)}%`);
export const ms = (v: number | null | undefined) => (v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);
