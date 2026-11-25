/* System Monitor: a live map of the running system built only from real
 * telemetry (/api/telemetry). Particles travel along a connection each time
 * a real request happens there; their speed follows the measured latency. */
import { AnimatePresence, m, useReducedMotion } from "framer-motion";
import clsx from "clsx";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ms, type TelemetryEvent, type TelemetrySnapshot } from "../lib/api";
import { useApp } from "../lib/store";
import { rise, spring, stagger } from "../motion/variants";
import { useDraw } from "../motion/useDraw";
import { PageHeader } from "../components/layout/Shell";
import { Badge, Button } from "../components/ui/controls";
import { AnimatedNumber, Card, CardHead, EmptyState, Skeleton, Tooltip } from "../components/ui/display";
import { Icon, type IconName } from "../components/ui/Icon";

const W = 920, H = 450;
const NODES: Record<string, { x: number; y: number; label: string; sub: string; icon: IconName }> = {
  browser: { x: 95, y: 215, label: "Browser", sub: "React dashboard", icon: "globe" },
  server: { x: 300, y: 215, label: "API server", sub: "FastAPI · Uvicorn", icon: "server" },
  orchestrator: { x: 505, y: 215, label: "Orchestrator", sub: "agent pipeline", icon: "cpu" },
  llm: { x: 760, y: 70, label: "AI model", sub: "LLM backend", icon: "sparkles" },
  pytest: { x: 790, y: 215, label: "Test sandbox", sub: "pytest + coverage", icon: "flask" },
  mutation: { x: 760, y: 360, label: "Mutation workers", sub: "parallel mutants", icon: "dna" },
  playwright: { x: 505, y: 380, label: "Playwright", sub: "headless Chromium", icon: "robot" },
};
const EDGES: [string, string][] = [["browser", "server"], ["server", "orchestrator"], ["orchestrator", "llm"], ["orchestrator", "pytest"],
  ["orchestrator", "mutation"], ["orchestrator", "playwright"], ["llm", "pytest"]];
const edgeKey = (a: string, b: string) => [a, b].sort().join("~");

const KIND: Record<string, { label: string; color: string }> = {
  http: { label: "HTTP request", color: "rgb(var(--info))" },
  run: { label: "Run lifecycle", color: "rgb(var(--accent-2))" },
  llm_call: { label: "LLM call", color: "rgb(var(--accent))" },
  pytest_run: { label: "pytest run", color: "rgb(var(--ok))" },
  mutant_run: { label: "Mutant run", color: "rgb(var(--warn))" },
  probe: { label: "Sandbox probe", color: "rgb(var(--ok))" },
  ui_scenario: { label: "UI scenario", color: "rgb(var(--info))" },
};

function EdgeLine({ x1, y1, x2, y2, delay }: { x1: number; y1: number; x2: number; y2: number; delay: number }) {
  const len = useDraw(1, { duration: 0.9, delay });
  return <m.line x1={x1} y1={y1} x2={x2} y2={y2} stroke="rgb(var(--line-2))" strokeWidth={1.5} style={{ pathLength: len }} />;
}

interface Particle { id: number; from: string; to: string; ok: boolean; dur: number; color: string }

const Diagram = memo(function Diagram({ particles, pulses, totals, onDone }: {
  particles: Particle[]; pulses: Record<string, { n: number; ok: boolean }>; totals: TelemetrySnapshot["totals"]; onDone: (id: number) => void;
}) {
  const reduce = useReducedMotion();
  const hot = new Set(particles.map((p) => edgeKey(p.from, p.to)));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Live system architecture">
      <defs>
        <linearGradient id="edge" x1="0" x2="1"><stop offset="0" style={{ stopColor: "rgb(var(--accent))" }} /><stop offset="1" style={{ stopColor: "rgb(var(--accent-2))" }} /></linearGradient>
        <filter id="soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" /></filter>
      </defs>
      {EDGES.map(([a, b], i) => {
        const A = NODES[a], B = NODES[b], k = edgeKey(a, b), on = hot.has(k);
        return (
          <g key={k}>
            <EdgeLine x1={A.x} y1={A.y} x2={B.x} y2={B.y} delay={0.2 + i * 0.08} />
            <m.line x1={A.x} y1={A.y} x2={B.x} y2={B.y} stroke="url(#edge)" strokeWidth={2.5} strokeLinecap="round"
              animate={{ opacity: on ? 0.9 : 0 }} transition={{ duration: 0.35 }} />
          </g>
        );
      })}
      {!reduce && particles.map((p) => {
        const A = NODES[p.from], B = NODES[p.to];
        return (
          <m.circle key={p.id} r={4.5} fill={p.ok ? p.color : "rgb(var(--bad))"}
            initial={{ cx: A.x, cy: A.y, opacity: 0 }}
            animate={{ cx: [A.x, B.x, A.x], cy: [A.y, B.y, A.y], opacity: [0, 1, 1, 0] }}
            transition={{ duration: p.dur, ease: "easeInOut", times: [0, 0.5, 1], opacity: { duration: p.dur, times: [0, 0.1, 0.85, 1] } }}
            onAnimationComplete={() => onDone(p.id)} style={{ filter: "drop-shadow(0 0 6px currentColor)" }} />
        );
      })}
      {Object.entries(NODES).map(([key, n], i) => {
        const t = totals[key], pulse = pulses[key];
        return (
          <m.g key={key} initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} transition={{ ...spring.smooth, delay: 0.1 + i * 0.06 }}
            style={{ transformOrigin: `${n.x}px ${n.y}px` }}>
            <AnimatePresence>
              {pulse && (
                <m.circle key={pulse.n} cx={n.x} cy={n.y} r={34} fill="none" stroke={pulse.ok ? "rgb(var(--accent))" : "rgb(var(--bad))"} strokeWidth={2}
                  initial={{ r: 34, opacity: 0.8 }} animate={{ r: 56, opacity: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.9 }} />
              )}
            </AnimatePresence>
            <circle cx={n.x} cy={n.y} r={34} fill="rgb(var(--accent))" opacity={0.08} filter="url(#soft)" />
            <rect x={n.x - 32} y={n.y - 32} width={64} height={64} rx={17} fill="rgb(var(--panel))" stroke={t?.errors ? "rgb(var(--bad) / .6)" : "rgb(var(--line-2))"} />
            <foreignObject x={n.x - 14} y={n.y - 14} width={28} height={28}>
              <div className="grid h-7 w-7 place-items-center text-accent"><Icon name={n.icon} size={24} /></div>
            </foreignObject>
            <text x={n.x} y={n.y + 52} textAnchor="middle" fontSize="15" fontWeight="650" fill="rgb(var(--ink))">{n.label}</text>
            <text x={n.x} y={n.y + 70} textAnchor="middle" fontSize="12.5" fill="rgb(var(--muted))">
              {t ? `${t.calls} call${t.calls === 1 ? "" : "s"}${t.avg_ms != null ? ` · ${ms(t.avg_ms)}` : ""}${t.errors ? ` · ${t.errors} err` : ""}` : n.sub}
            </text>
          </m.g>
        );
      })}
    </svg>
  );
});

function SparkPath({ d, color }: { d: string; color: string }) {
  const len = useDraw(1, { duration: 0.6 });
  return <m.path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" style={{ pathLength: len }} />;
}

function Sparkline({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return <div className="h-9 text-[11px] text-muted">waiting for data…</div>;
  const max = Math.max(...values), w = 160, h = 36;
  const d = values.map((v, i) => `${i ? "L" : "M"}${(i / (values.length - 1)) * w},${h - (v / (max || 1)) * (h - 4) - 2}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-9 w-full" preserveAspectRatio="none">
      <SparkPath key={values.length} d={d} color={color} />
    </svg>
  );
}

export default function Monitor() {
  const { toast } = useApp();
  const [snap, setSnap] = useState<TelemetrySnapshot | null>(null);
  const [events, setEvents] = useState<TelemetryEvent[]>([]);
  const [totals, setTotals] = useState<TelemetrySnapshot["totals"]>({});
  const [connected, setConnected] = useState(false);
  const [particles, setParticles] = useState<Particle[]>([]);
  const [pulses, setPulses] = useState<Record<string, { n: number; ok: boolean }>>({});
  const [filter, setFilter] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const pid = useRef(1);
  const perEdge = useRef<Record<string, number>>({});

  const removeParticle = useCallback((id: number) => {
    setParticles((ps) => {
      const p = ps.find((x) => x.id === id);
      if (p) perEdge.current[edgeKey(p.from, p.to)] = Math.max(0, (perEdge.current[edgeKey(p.from, p.to)] || 1) - 1);
      return ps.filter((x) => x.id !== id);
    });
  }, []);

  const ingest = useCallback((e: TelemetryEvent) => {
    setEvents((es) => [e, ...es].slice(0, 300));
    setTotals((t) => {
      const cur = t[e.target] || { calls: 0, errors: 0, avg_ms: null };
      const timed = e.duration_ms != null;
      const avg = timed ? (cur.avg_ms == null ? e.duration_ms! : (cur.avg_ms * cur.calls + e.duration_ms!) / (cur.calls + 1)) : cur.avg_ms;
      return { ...t, [e.target]: { calls: cur.calls + 1, errors: cur.errors + (e.ok ? 0 : 1), avg_ms: avg == null ? null : Math.round(avg * 10) / 10 } };
    });
    setPulses((p) => ({ ...p, [e.target]: { n: e.seq, ok: e.ok } }));
    if (NODES[e.source] && NODES[e.target] && e.source !== e.target) {
      const k = edgeKey(e.source, e.target);
      if ((perEdge.current[k] || 0) < 6) {   // cap bursts (dozens of mutants) to keep 60 fps
        perEdge.current[k] = (perEdge.current[k] || 0) + 1;
        const dur = Math.min(2.4, Math.max(0.7, ((e.duration_ms ?? 300) / 1000) * 0.6 + 0.5));
        setParticles((ps) => [...ps, { id: pid.current++, from: e.source, to: e.target, ok: e.ok, dur, color: KIND[e.kind]?.color || "rgb(var(--accent))" }]);
      }
    }
  }, []);

  useEffect(() => {
    let es: EventSource | null = null, closed = false;
    api.telemetry(200).then((s) => {
      if (closed) return;
      setSnap(s); setTotals(s.totals); setEvents([...s.events].reverse());
      es = new EventSource("/api/telemetry/stream");
      es.addEventListener("hello", () => setConnected(true));
      es.onmessage = (ev) => ingest(JSON.parse(ev.data));
      es.onerror = () => setConnected(false);
    }).catch(() => setSnap(null));
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { closed = true; es?.close(); window.clearInterval(t); };
  }, [ingest]);

  const lastMinute = events.filter((e) => now / 1000 - e.t <= 60);
  const llm = events.filter((e) => e.kind === "llm_call" && e.duration_ms != null).slice(0, 30).reverse();
  const tests = events.filter((e) => (e.kind === "pytest_run" || e.kind === "mutant_run") && e.duration_ms != null).slice(0, 40).reverse();
  const errors = Object.values(totals).reduce((a, t) => a + t.errors, 0);
  const shown = useMemo(() => (filter ? events.filter((e) => e.kind === filter) : events).slice(0, 80), [events, filter]);
  const kinds = useMemo(() => [...new Set(events.map((e) => e.kind))], [events]);

  async function ping() {
    const t0 = performance.now();
    try { await api.status(); toast(`Server answered in ${Math.round(performance.now() - t0)} ms`, "ok"); }
    catch { toast("Server did not answer", "bad"); }
  }
  async function quickRun() {
    try {
      const ex = (await api.examples()).find((e) => e.name === "calculator");
      if (!ex) { toast("Example 'calculator' not found", "bad"); return; }
      await api.startRun({ source: ex.source, module_name: "calculator", llm: "mock", max_refine_iterations: 1 });
      toast("Started a mock run on the calculator example: watch the traffic.", "ok");
    } catch (e) { toast((e as Error).message, "bad"); }
  }

  const stats: { k: string; v: number | null; fmt?: (v: number) => string; d: string; tip: string }[] = [
    { k: "Events / minute", v: lastMinute.length, d: "all components", tip: "Real telemetry events recorded in the last 60 seconds." },
    { k: "LLM calls", v: totals.llm?.calls ?? 0, d: totals.llm?.avg_ms != null ? `avg ${ms(totals.llm.avg_ms)}` : "none yet", tip: "Calls from the agents to the AI backend since the server started." },
    { k: "Test & mutant runs", v: (totals.pytest?.calls ?? 0) + (totals.mutation?.calls ?? 0), d: `${totals.mutation?.calls ?? 0} mutants`, tip: "pytest executions in isolated sandboxes, including one per mutant." },
    { k: "Errors", v: errors, d: errors ? "see red events" : "all healthy", tip: "Failed requests or calls (HTTP ≥ 400, exceptions, LLM errors)." },
  ];

  return (
    <m.div variants={stagger(0.06)} initial="hidden" animate="show">
      <PageHeader eyebrow="Observability" title="System monitor"
        subtitle="A live map of what TestForge is doing right now, built only from real telemetry: every request, AI call, test run and mutant."
        right={<>
          <Badge tone={connected ? "ok" : "bad"}><span className={clsx("h-1.5 w-1.5 rounded-full bg-current", connected && "animate-pulse")} />{connected ? "Live" : "Disconnected"}</Badge>
          {snap && <Badge className="font-mono">up {Math.max(0, Math.floor((now / 1000 - snap.started) / 60))} min</Badge>}
        </>} />

      <m.div variants={stagger(0.05)} className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {stats.map((s) => (
          <m.div key={s.k} variants={rise} whileHover={{ y: -2 }} className="card p-4">
            <div className="flex items-center gap-1.5 text-[12px] font-medium text-muted">{s.k}
              <Tooltip text={s.tip}><span className="cursor-help rounded-full border border-line px-1 text-[10px]">?</span></Tooltip></div>
            <div className={clsx("text-[26px] font-bold tracking-tight", s.k === "Errors" && (s.v ?? 0) > 0 && "text-bad")}>
              {snap ? <AnimatedNumber value={s.v} /> : <Skeleton className="my-1 h-7 w-14" />}</div>
            <div className="text-[12px] text-muted">{s.d}</div>
          </m.div>
        ))}
      </m.div>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-5">
          <Card>
            <CardHead title="Live architecture" sub="particles = real requests" right={<>
              <Button size="sm" icon="zap" onClick={ping}>Ping server</Button>
              <Button size="sm" icon="play" variant="primary" onClick={quickRun}>Quick mock run</Button></>} />
            <div className="p-3">{snap ? <Diagram particles={particles} pulses={pulses} totals={totals} onDone={removeParticle} /> : <Skeleton className="h-[380px]" />}</div>
            <div className="flex flex-wrap gap-3 border-t border-line px-4 py-3 text-[12px] text-ink-2">
              {Object.entries(KIND).map(([k, v]) => <span key={k} className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full" style={{ background: v.color }} />{v.label}</span>)}
              <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-bad" />error</span>
            </div>
          </Card>
          <div className="grid gap-5 md:grid-cols-2">
            <Card><CardHead title="LLM latency" sub="last 30 calls" />
              <div className="p-4"><Sparkline values={llm.map((e) => e.duration_ms!)} color="rgb(var(--accent))" />
                <div className="mt-1 text-[12px] text-muted">{llm.length ? `latest ${ms(llm[llm.length - 1].duration_ms)} · max ${ms(Math.max(...llm.map((e) => e.duration_ms!)))}` : "Run with an AI backend to see this."}</div></div></Card>
            <Card><CardHead title="Test execution time" sub="last 40 runs" />
              <div className="p-4"><Sparkline values={tests.map((e) => e.duration_ms!)} color="rgb(var(--ok))" />
                <div className="mt-1 text-[12px] text-muted">{tests.length ? `latest ${ms(tests[tests.length - 1].duration_ms)} · max ${ms(Math.max(...tests.map((e) => e.duration_ms!)))}` : "Start any run to see this."}</div></div></Card>
          </div>
        </div>

        <Card className="xl:sticky xl:top-6">
          <CardHead title="Event stream" right={<span className="text-[12px] text-muted">{events.length} recent</span>} />
          <div className="flex flex-wrap gap-1.5 border-b border-line px-4 py-2.5">
            {[null, ...kinds].map((k) => (
              <button key={k ?? "all"} onClick={() => setFilter(k)}
                className={clsx("rounded-full border px-2.5 py-0.5 text-[11.5px] font-medium transition", filter === k ? "border-accent/60 bg-accent/15 text-ink" : "border-line text-ink-2 hover:text-ink")}>
                {k ? KIND[k]?.label || k : "All"}</button>
            ))}
          </div>
          <div className="max-h-[560px] overflow-auto px-3 py-1">
            {snap == null ? <div className="flex flex-col gap-2 p-2">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-12" />)}</div>
              : shown.length === 0 ? <EmptyState icon="activity" title="Quiet right now">Start a run, or press <b>Quick mock run</b>, to see live traffic.</EmptyState> : (
              <AnimatePresence initial={false}>
                {shown.map((e) => (
                  <m.div key={e.seq} layout initial={{ opacity: 0, x: 16, height: 0 }} animate={{ opacity: 1, x: 0, height: "auto" }} exit={{ opacity: 0 }} transition={spring.smooth} className="overflow-hidden">
                    <div className="grid grid-cols-[8px_minmax(0,1fr)_auto] items-start gap-2.5 border-b border-line/70 py-2">
                      <i className="mt-1.5 h-2 w-2 rounded-full" style={{ background: e.ok ? KIND[e.kind]?.color || "rgb(var(--accent))" : "rgb(var(--bad))" }} />
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5 text-[12px]">
                          <b className="font-semibold">{KIND[e.kind]?.label || e.kind}</b>
                          <span className="text-muted">{e.source} → {e.target}</span>
                        </div>
                        <div className={clsx("truncate text-[12px]", e.ok ? "text-ink-2" : "text-bad")} title={e.detail}>{e.detail || "–"}</div>
                      </div>
                      <div className="text-right">
                        <div className="font-mono text-[11.5px] tabular-nums">{ms(e.duration_ms)}</div>
                        <div className="font-mono text-[10.5px] text-muted">{new Date(e.t * 1000).toLocaleTimeString([], { hour12: false })}</div>
                      </div>
                    </div>
                  </m.div>
                ))}
              </AnimatePresence>
            )}
          </div>
        </Card>
      </div>
    </m.div>
  );
}
