import { m } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, followRun, pct, type Example, type HistoryRow, type RunEvent } from "../lib/api";
import { reduceRun } from "../lib/run";
import { storage, useApp, useElapsed } from "../lib/store";
import { stagger } from "../motion/variants";
import { PageHeader } from "../components/layout/Shell";
import { ActivityFeed, KpiGrid, Pipeline, ResultsTabs } from "../components/run/RunPanels";
import { CodeEditor } from "../components/ui/code";
import { Badge, Button, RangeField, Segmented, Switch } from "../components/ui/controls";
import { Card, CardHead, Skeleton } from "../components/ui/display";

type Backend = "mock" | "groq" | "ollama" | "claude";
const MODEL_HINT: Record<Backend, string> = { mock: "not used", groq: "openai/gpt-oss-120b", ollama: "qwen2.5-coder:3b", claude: "claude-opus-5-5" };

export default function Generate() {
  const { status, toast } = useApp();
  const [examples, setExamples] = useState<Example[] | null>(null);
  const [example, setExample] = useState("");
  const [source, setSource] = useState("");
  const [moduleName, setModuleName] = useState("module_under_test");
  const [backend, setBackend] = useState<Backend>(() => storage.get("backend", "mock"));
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState<"low" | "medium" | "high">("medium");
  const [rounds, setRounds] = useState(3);
  const [target, setTarget] = useState(90);
  const [planner, setPlanner] = useState(true);
  const [fixer, setFixer] = useState(true);
  const [feedback, setFeedback] = useState(true);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [times, setTimes] = useState<{ start: number | null; end: number | null }>({ start: null, end: null });
  const [recent, setRecent] = useState<HistoryRow[] | null>(null);
  const stop = useRef<(() => void) | null>(null);

  const view = useMemo(() => reduceRun(events), [events]);
  const elapsed = useElapsed(times.start, times.end);
  const name = moduleName.trim().replace(/\.py$/i, "") || "module";

  const loadRecent = useCallback(() => api.history().then((h) => setRecent(h.filter((r) => r.kind === "generate").slice(0, 5))).catch(() => setRecent([])), []);
  useEffect(() => {
    api.examples().then((ex) => {
      setExamples(ex);
      if (ex.length) { setExample(ex[0].name); setSource(ex[0].source); setModuleName(ex[0].name); }
    }).catch(() => setExamples([]));
    loadRecent();
    return () => stop.current?.();
  }, [loadRecent]);
  useEffect(() => storage.set("backend", backend), [backend]);

  const run = useCallback(async () => {
    if (running) return;
    if (!source.trim()) { toast("Paste some Python code first.", "bad"); return; }
    const st = status?.[backend];
    if (st && !st.ready) toast(`${backend}: ${st.note}. The run may fail.`, "bad");
    try {
      const { run_id } = await api.startRun({
        source, module_name: name, llm: backend, model: model.trim() || null, effort,
        max_refine_iterations: rounds, target_mutation_score: target / 100,
        use_planner: planner, use_fixer: fixer, use_mutation_feedback: feedback,
      });
      setModuleName(name); setEvents([]); setRunning(true); setTimes({ start: Date.now(), end: null });
      stop.current = followRun(run_id, (e) => setEvents((es) => [...es, e]), (ok) => {
        setRunning(false); setTimes((t) => ({ ...t, end: Date.now() })); loadRecent();
        if (!ok) toast("Lost connection to the run.", "bad");
      });
    } catch (e) {
      toast((e as Error).message, "bad");
    }
  }, [running, source, status, backend, toast, name, model, effort, rounds, target, planner, fixer, feedback, loadRecent]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); run(); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run]);

  const state = running ? { t: "Running", tone: "accent" as const } : view.failed ? { t: "Error", tone: "bad" as const }
    : view.finished ? { t: "Finished", tone: "ok" as const } : { t: "Idle", tone: "muted" as const };
  const st = status?.[backend];

  return (
    <m.div variants={stagger(0.06)} initial="hidden" animate="show">
      <PageHeader eyebrow="Unit testing" title="Generate unit tests"
        subtitle="AI agents write pytest tests; mutation testing then proves whether they catch real bugs."
        right={<><Badge tone={state.tone}>{running && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}{state.t}</Badge><Badge className="font-mono">{elapsed}</Badge></>} />

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
        <m.div variants={stagger(0.07)} className="flex min-w-0 flex-col gap-5">
          <Card>
            <CardHead step={1} title="Code under test" right={
              <select aria-label="Load an example" value={example} className="input w-auto py-1.5"
                onChange={(e) => { const ex = examples?.find((x) => x.name === e.target.value); setExample(e.target.value);
                  if (ex) { setSource(ex.source); setModuleName(ex.name); } }}>
                <option value="">Paste your own…</option>
                {examples?.map((x) => <option key={x.name} value={x.name}>Example: {x.name}</option>)}
              </select>} />
            <div className="flex flex-col gap-3 p-4">
              <label className="flex flex-col gap-1.5"><span className="label">Module name</span>
                <input className="input" value={moduleName} spellCheck={false} onChange={(e) => setModuleName(e.target.value)} /></label>
              {examples == null ? <Skeleton className="h-[420px]" /> :
                <CodeEditor value={source} onChange={setSource} onRun={run} fileName={`${name}.py`} />}
            </div>
          </Card>

          <Card>
            <CardHead step={2} title="Configuration" />
            <div className="flex flex-col gap-5 p-4">
              <div className="flex flex-col gap-1.5">
                <span className="label">AI backend</span>
                <Segmented label="AI backend" value={backend} onChange={setBackend} options={(["mock", "groq", "ollama", "claude"] as Backend[]).map((b) => ({
                  value: b, label: b === "mock" ? "Mock" : b[0].toUpperCase() + b.slice(1), dot: status ? (status[b].ready ? "on" : "off") : undefined }))} />
                <m.span key={backend} initial={{ opacity: 0, y: -3 }} animate={{ opacity: 1, y: 0 }} className={st && !st.ready ? "text-[12px] text-warn" : "text-[12px] text-muted"}>
                  {st ? (st.ready ? `✓ Ready: ${st.note}` : `⚠ ${st.note}`) : " "}
                </m.span>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="flex flex-col gap-1.5"><span className="label">Model</span>
                  <input className="input" value={model} disabled={backend === "mock"} placeholder={MODEL_HINT[backend]} spellCheck={false} onChange={(e) => setModel(e.target.value)} /></label>
                <div className="flex flex-col gap-1.5"><span className="label">Reasoning effort</span>
                  <Segmented label="Reasoning effort" value={effort} onChange={setEffort} disabled={backend === "mock" || backend === "ollama"}
                    options={[{ value: "low", label: "Low" }, { value: "medium", label: "Medium" }, { value: "high", label: "High" }]} /></div>
              </div>
              <div className="grid gap-5 sm:grid-cols-2">
                <RangeField label="Refinement rounds" value={rounds} display={String(rounds)} min={0} max={5} onChange={setRounds} />
                <RangeField label="Target mutation score" value={target} display={`${target}%`} min={50} max={100} step={5} onChange={setTarget} />
              </div>
              <div>
                <Switch checked={planner} onChange={setPlanner} label="Planner agent" hint="Design test cases before writing" />
                <Switch checked={fixer} onChange={setFixer} label="Fixer agent" hint="Repair failing tests automatically" />
                <Switch checked={feedback} onChange={setFeedback} label="Mutation feedback" hint="Send surviving mutants back to the Writer" />
              </div>
              <Button variant="primary" size="lg" magnetic icon="play" loading={running} onClick={run}>
                {running ? "Agents at work…" : "Generate tests"} {!running && <span className="kbd">Ctrl ↵</span>}
              </Button>
            </div>
          </Card>

          <Card>
            <CardHead title="Recent runs" sub="saved on this server" right={<Link to="/runs" className="text-[12.5px] font-semibold text-accent hover:underline">All runs →</Link>} />
            <div className="px-4 py-2">
              {recent == null ? <div className="flex flex-col gap-2 py-2"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
                : recent.length === 0 ? <p className="py-3 text-[13px] text-muted">No runs yet.</p>
                : recent.map((r) => (
                  <Link key={r.id} to={`/runs?id=${r.id}`} className="flex items-center gap-3 border-b border-line py-2.5 last:border-0 hover:opacity-90">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-semibold">{r.summary.module}</div>
                      <div className="truncate text-[12px] text-muted">{r.summary.backend}{r.summary.model && r.summary.backend !== "mock" ? ` · ${r.summary.model}` : ""} · {r.summary.tests ?? 0} tests · {r.summary.elapsed_s ?? "–"} s</div>
                    </div>
                    <Badge tone={(r.summary.score ?? 0) >= 0.9 ? "ok" : (r.summary.score ?? 0) >= 0.6 ? "warn" : "bad"}>{pct(r.summary.score)}</Badge>
                  </Link>
                ))}
            </div>
          </Card>
        </m.div>

        <m.div variants={stagger(0.07, 0.1)} className="flex min-w-0 flex-col gap-5 xl:sticky xl:top-6 xl:-m-1 xl:max-h-[calc(100vh-3rem)] xl:overflow-y-auto xl:p-1">
          <Pipeline view={view} step={3} />
          <KpiGrid view={view} loading={running} />
          <ActivityFeed view={view} running={running} />
          <ResultsTabs view={view} moduleName={name} />
        </m.div>
      </div>
    </m.div>
  );
}
