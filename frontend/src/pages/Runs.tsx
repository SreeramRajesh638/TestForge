/* Runs: saved history, side-by-side comparison and frame-accurate replay.
 * Replay feeds the recorded events (with their real timestamps) through the
 * same reducer and panels as the live Generate page. */
import { LayoutGroup, m } from "framer-motion";
import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, pct, type HistoryRecord, type HistoryRow } from "../lib/api";
import { fmtT, reduceRun } from "../lib/run";
import { useApp } from "../lib/store";
import { rise, spring, stagger } from "../motion/variants";
import { PageHeader } from "../components/layout/Shell";
import { LineChart } from "../components/charts/charts";
import { ActivityFeed, KpiGrid, Pipeline, ResultsTabs } from "../components/run/RunPanels";
import { Badge, Button, Segmented } from "../components/ui/controls";
import { Card, CardHead, EmptyState, Skeleton } from "../components/ui/display";
import { Icon } from "../components/ui/Icon";

const SPEEDS = ["1", "4", "16", "64"] as const;

function Replay({ rec }: { rec: HistoryRecord }) {
  const end = Math.max(0.1, ...rec.events.map((e) => e.t ?? 0));
  const [t, setT] = useState(end);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>("16");
  const raf = useRef(0), last = useRef(0);

  useEffect(() => { setT(end); setPlaying(false); }, [rec.id, end]);
  useEffect(() => {
    if (!playing) return;
    last.current = performance.now();
    const tick = (now: number) => {
      const dt = (now - last.current) / 1000; last.current = now;
      setT((cur) => {
        const next = cur + dt * Number(speed);
        if (next >= end) { setPlaying(false); return end; }
        return next;
      });
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, speed, end]);

  const events = useMemo(() => rec.events.filter((e) => (e.t ?? 0) <= t + 1e-6), [rec.events, t]);
  const view = useMemo(() => reduceRun(events), [events]);
  const moduleName = rec.summary.module || "module";

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <div className="flex flex-wrap items-center gap-3 p-4">
          <Button variant="primary" icon={playing ? "pause" : "play"} onClick={() => { if (!playing && t >= end) setT(0); setPlaying((p) => !p); }}>
            {playing ? "Pause" : t >= end ? "Replay from start" : "Play"}</Button>
          <div className="w-48"><Segmented label="Replay speed" value={speed} onChange={setSpeed} options={SPEEDS.map((s) => ({ value: s, label: `${s}×` }))} /></div>
          <input type="range" aria-label="Replay position" min={0} max={end} step={0.05} value={t}
            onChange={(e) => { setPlaying(false); setT(Number(e.target.value)); }} className="min-w-[160px] flex-1 accent-[rgb(var(--accent))]" />
          <span className="font-mono text-[12.5px] tabular-nums text-ink-2">{fmtT(t)} / {fmtT(end)}</span>
        </div>
      </Card>
      <Pipeline view={view} />
      <KpiGrid view={view} />
      <div className="grid items-start gap-5 2xl:grid-cols-2">
        <ActivityFeed view={view} running={playing} />
        <ResultsTabs view={view} moduleName={moduleName} />
      </div>
    </div>
  );
}

function UiRunDetail({ rec }: { rec: HistoryRecord }) {
  const r = rec.report || {};
  if (rec.request?.mode === "bughunt") {
    const bugs = Object.entries((r.bugs || {}) as Record<string, string[]>);
    return (
      <Card><CardHead title="Bug hunt" sub={`${r.detected}/${r.total} bugs caught`} />
        <div className="grid gap-2 p-4 sm:grid-cols-2">
          {bugs.map(([b, by]) => (
            <div key={b} className="flex items-center gap-2 rounded-lg border border-line bg-panel-2 px-3 py-2 text-[13px]">
              <b className="font-mono">{b}</b><span className="truncate text-muted">{by.join(", ") || "not caught"}</span>
              <span className="ml-auto">{by.length ? <Badge tone="ok">✓ {by.length}</Badge> : <Badge tone="warn">MISSED</Badge>}</span></div>
          ))}
        </div></Card>
    );
  }
  return (
    <Card><CardHead title="Scenario results" sub={`${r.passed ?? 0} passed · ${r.failed ?? 0} failed${(rec.request?.bugs || []).length ? ` · bugs: ${rec.request.bugs.join(", ")}` : ""}`} />
      <div className="flex flex-col gap-1.5 p-4">
        {(r.results || []).map((s: { name: string; status: string; duration_ms: number; message: string }) => (
          <div key={s.name} className="rounded-lg border border-line bg-panel-2 px-3 py-2 text-[13px]">
            <div className="flex items-center gap-2"><Badge tone={s.status === "passed" ? "ok" : "bad"}>{s.status === "passed" ? "PASS" : "FAIL"}</Badge>
              <span className="font-semibold">{s.name}</span><span className="ml-auto font-mono text-[12px] text-muted">{(s.duration_ms / 1000).toFixed(2)}s</span></div>
            {s.message && <div className="mt-1 truncate font-mono text-[12px] text-bad" title={s.message}>{s.message}</div>}
          </div>
        ))}
      </div></Card>
  );
}

function Compare({ a, b }: { a: HistoryRecord; b: HistoryRecord }) {
  const rows: [string, (r: HistoryRecord) => string][] = [
    ["Module", (r) => r.summary.module], ["Backend", (r) => `${r.summary.backend}${r.summary.model && r.summary.backend !== "mock" ? ` · ${r.summary.model}` : ""}`],
    ["Mutation score", (r) => pct(r.summary.score)], ["First round", (r) => pct(r.summary.initial_score)],
    ["Tests", (r) => String(r.summary.tests ?? "–")], ["Line coverage", (r) => pct(r.summary.line)], ["Branch coverage", (r) => pct(r.summary.branch)],
    ["Refinement rounds", (r) => String(r.summary.rounds ?? "–")], ["Time", (r) => `${r.summary.elapsed_s ?? "–"} s`],
    ["Tokens in / out", (r) => `${r.summary.tokens_in ?? 0} / ${r.summary.tokens_out ?? 0}`], ["Stopped because", (r) => (r.summary.stop || "–").replace(/_/g, " ")],
  ];
  const its = [a, b].map((r) => (r.report?.iterations || []) as { mutation_score: number | null }[]);
  const n = Math.max(1, ...its.map((x) => x.length));
  const better = (i: number) => {
    const va = a.summary.score ?? -1, vb = b.summary.score ?? -1;
    return rows[i][0] === "Mutation score" && va !== vb ? (va > vb ? 0 : 1) : null;
  };
  return (
    <div className="grid items-start gap-5 xl:grid-cols-2">
      <Card><CardHead title="Side by side" />
        <table className="w-full text-[13px]"><thead><tr className="border-b border-line text-left text-[12px] text-muted">
          <th className="px-4 py-2 font-semibold">Metric</th><th className="px-4 py-2 font-semibold"><Badge tone="accent">A</Badge> {a.id}</th><th className="px-4 py-2 font-semibold"><Badge tone="info">B</Badge> {b.id}</th></tr></thead>
          <tbody>{rows.map(([k, f], i) => (
            <tr key={k} className="border-b border-line last:border-0"><th className="px-4 py-2 text-left text-[12px] font-semibold text-muted">{k}</th>
              {[a, b].map((r, j) => <td key={j} className={clsx("px-4 py-2", better(i) === j && "font-bold text-ok")}>{f(r)}</td>)}</tr>
          ))}</tbody></table>
      </Card>
      <Card><CardHead title="Mutation score per round" />
        <div className="p-4"><LineChart xLabels={Array.from({ length: n }, (_, i) => (i ? `round ${i}` : "initial"))} series={[
          { key: "a", label: `A · ${a.summary.backend}`, color: "rgb(var(--accent))", emphasis: true, points: Array.from({ length: n }, (_, i) => its[0][i]?.mutation_score ?? null) },
          { key: "b", label: `B · ${b.summary.backend}`, color: "rgb(var(--info))", points: Array.from({ length: n }, (_, i) => its[1][i]?.mutation_score ?? null) },
        ]} /></div></Card>
    </div>
  );
}

export default function Runs() {
  const { toast } = useApp();
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<HistoryRow[] | null>(null);
  const [kind, setKind] = useState<"all" | "generate" | "ui">("all");
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [records, setRecords] = useState<Record<string, HistoryRecord>>({});
  const selected = params.get("id");

  const load = useCallback(() => api.history().then(setRows).catch(() => setRows([])), []);
  useEffect(() => { load(); }, [load]);
  const need = selected ? [selected, ...compareIds] : compareIds;
  useEffect(() => {
    need.filter((id) => !records[id]).forEach((id) => api.historyRecord(id).then((r) => setRecords((s) => ({ ...s, [id]: r }))).catch(() => toast(`Run ${id} not found`, "bad")));
  }, [need.join(","), records]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = (rows || []).filter((r) => kind === "all" || r.kind === kind);
  const rec = selected ? records[selected] : null;
  const comparing = compareIds.length === 2 && records[compareIds[0]] && records[compareIds[1]];

  async function remove(id: string) {
    try { await api.deleteHistory(id); setRows((r) => (r || []).filter((x) => x.id !== id)); setCompareIds((c) => c.filter((x) => x !== id));
      if (selected === id) setParams({}); toast("Run deleted", "ok"); } catch (e) { toast((e as Error).message, "bad"); }
  }

  return (
    <m.div variants={stagger(0.06)} initial="hidden" animate="show">
      <PageHeader eyebrow="History" title="Runs" subtitle="Every run saved on this server. Open one to replay it step by step, or tick two generate runs to compare them." />
      <div className="grid items-start gap-5 xl:grid-cols-[380px_minmax(0,1fr)]">
        <Card className="xl:sticky xl:top-6">
          <CardHead title="Saved runs" right={<span className="text-[12px] text-muted">{rows?.length ?? 0}</span>} />
          <div className="border-b border-line p-3"><Segmented label="Run type" value={kind} onChange={setKind}
            options={[{ value: "all", label: "All" }, { value: "generate", label: "Unit tests" }, { value: "ui", label: "UI" }]} /></div>
          <div className="max-h-[calc(100vh-15rem)] overflow-auto p-2">
            {rows == null ? <div className="flex flex-col gap-2 p-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-14" />)}</div>
              : shown.length === 0 ? <EmptyState icon="history" title="No runs yet">Runs from the Generate and UI automation pages appear here.</EmptyState> : (
              <LayoutGroup id="runs">
                <m.div variants={stagger(0.03)} initial="hidden" animate="show">
                  {shown.map((r) => {
                    const on = selected === r.id, cmp = compareIds.includes(r.id);
                    return (
                      <m.div key={r.id} variants={rise} layout className="relative mb-1 rounded-xl">
                        {on && <m.span layoutId="run-sel" transition={spring.snappy} className="absolute inset-0 rounded-xl border border-accent/40 bg-accent/10" />}
                        <div className="relative flex items-center gap-2 px-2 py-2">
                          {r.kind === "generate" && (
                            <input type="checkbox" aria-label="Compare" title="Compare (pick two)" checked={cmp} className="accent-[rgb(var(--accent))]"
                              onChange={(e) => setCompareIds((c) => (e.target.checked ? [...c.slice(-1), r.id] : c.filter((x) => x !== r.id)))} />
                          )}
                          <button onClick={() => setParams({ id: r.id })} className="min-w-0 flex-1 text-left">
                            <div className="flex items-center gap-2"><span className="truncate text-[13px] font-semibold">{r.summary.module}</span>
                              <Badge tone={r.kind === "ui" ? "info" : "muted"} className="!px-1.5 !py-0 !text-[10px]">{r.kind === "ui" ? "UI" : r.summary.backend}</Badge></div>
                            <div className="truncate text-[11.5px] text-muted">{new Date(r.started * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })} · {r.id}</div>
                          </button>
                          <Badge tone={(r.summary.score ?? 0) >= 0.9 ? "ok" : (r.summary.score ?? 0) >= 0.6 ? "warn" : "bad"}>{pct(r.summary.score)}</Badge>
                          <button onClick={() => remove(r.id)} aria-label={`Delete run ${r.id}`} className="rounded-md p-1 text-muted hover:bg-bad/10 hover:text-bad"><Icon name="trash" size={14} /></button>
                        </div>
                      </m.div>
                    );
                  })}
                </m.div>
              </LayoutGroup>
            )}
          </div>
        </Card>

        <div className="min-w-0">
          {/* Views switch immediately (entrance animation only): a "wait" exit could stall
              behind the replay's continuously animating children. */}
          <>
            {comparing ? (
              <m.div key="cmp" variants={stagger(0.06)} initial="hidden" animate="show" className="flex flex-col gap-3">
                <div className="flex items-center gap-2"><Badge tone="accent">Comparing 2 runs</Badge><Button size="sm" onClick={() => setCompareIds([])}>Clear comparison</Button></div>
                <Compare a={records[compareIds[0]]} b={records[compareIds[1]]} />
              </m.div>
            ) : rec ? (
              <m.div key={rec.id} variants={stagger(0.06)} initial="hidden" animate="show" className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-[17px] font-bold">{rec.summary.module}</h2><Badge>{rec.id}</Badge>
                  <Badge tone={rec.status === "finished" ? "ok" : "bad"}>{rec.status}</Badge>
                  <span className="text-[12.5px] text-muted">{new Date(rec.started * 1000).toLocaleString()}</span>
                </div>
                {rec.kind === "generate" ? <Replay rec={rec} /> : <UiRunDetail rec={rec} />}
              </m.div>
            ) : selected ? (
              <m.div key="loading" className="flex flex-col gap-3"><Skeleton className="h-16" /><Skeleton className="h-40" /><Skeleton className="h-64" /></m.div>
            ) : (
              <m.div key="empty" variants={stagger(0.06)} initial="hidden" animate="show">
                <Card><EmptyState icon="play" title="Pick a run">Open a run on the left to replay it exactly as it happened, or tick two unit-test runs to compare them.{compareIds.length === 1 && " (Tick one more to compare.)"}</EmptyState></Card>
              </m.div>
            )}
          </>
        </div>
      </div>
    </m.div>
  );
}
