import { AnimatePresence, LayoutGroup, m } from "framer-motion";
import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, followRun, type Bug, type Scenario, type ScenarioResult } from "../lib/api";
import { useApp, useElapsed } from "../lib/store";
import { rise, spring, stagger } from "../motion/variants";
import { PageHeader } from "../components/layout/Shell";
import { Badge, Button, RangeField, Switch, Tabs } from "../components/ui/controls";
import { AnimatedNumber, Card, CardHead, Collapse, EmptyState, Gauge, Modal, Skeleton, TiltCard } from "../components/ui/display";
import { Icon } from "../components/ui/Icon";

type Hunt = { bug: string; caught_by: string[] };

function ResultRow({ r, onShot }: { r: ScenarioResult; onShot: (src: string) => void }) {
  const ok = r.status === "passed";
  const [open, setOpen] = useState(!ok);
  return (
    <m.div layout variants={rise} className="overflow-hidden rounded-xl border border-line bg-panel-2/60">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="grid w-full grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-3 px-3.5 py-2.5 text-left">
        <Badge tone={ok ? "ok" : "bad"}>{ok ? "PASS" : "FAIL"}</Badge>
        <span className="truncate text-[13px] font-semibold">{r.name}</span>
        <span className="font-mono text-[12px] text-muted">{(r.duration_ms / 1000).toFixed(2)}s</span>
        <m.span animate={{ rotate: open ? 90 : 0 }} transition={spring.snappy} className="text-muted"><Icon name="chevron" size={14} /></m.span>
      </button>
      <Collapse open={open}>
        <div className="grid gap-4 border-t border-line p-3.5 md:grid-cols-[minmax(0,1fr)_auto]">
          <div className="min-w-0">
            {r.message && <pre className="mb-3 whitespace-pre-wrap rounded-lg bg-bad/10 p-3 font-mono text-[12px] text-bad">{r.message}</pre>}
            <ol className="font-mono text-[12px]">
              {r.steps.map((s) => (
                <li key={s.index} className={clsx("grid grid-cols-[18px_minmax(0,1fr)_auto] gap-2 py-0.5",
                  s.status === "failed" && "text-bad", s.status === "skipped" && "text-muted")}>
                  <span className={clsx("font-bold", s.status === "passed" && "text-ok")}>{s.status === "passed" ? "✓" : s.status === "failed" ? "✕" : "·"}</span>
                  <span className="break-words">{s.description}</span>
                  <span className="text-[11px] text-muted">{s.status === "skipped" ? "skipped" : `${s.duration_ms} ms`}</span>
                </li>
              ))}
            </ol>
          </div>
          {r.screenshot_png_b64 && (
            <m.button whileHover={{ scale: 1.03 }} onClick={() => onShot(`data:image/png;base64,${r.screenshot_png_b64}`)} className="self-start text-left">
              <img alt="Page at the failing step" src={`data:image/png;base64,${r.screenshot_png_b64}`} className="w-56 rounded-lg border border-line" />
              <span className="mt-1 block text-[11.5px] text-muted">Page at the failing step · click to enlarge</span>
            </m.button>
          )}
        </div>
      </Collapse>
    </m.div>
  );
}

export default function Automation() {
  const { toast } = useApp();
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [bugs, setBugs] = useState<Bug[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [tag, setTag] = useState<string | null>(null);
  const [active, setActive] = useState<Set<string>>(new Set());
  const [timeout, setTimeoutMs] = useState(3000);
  const [mode, setMode] = useState<"run" | "bughunt" | null>(null);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<ScenarioResult[]>([]);
  const [hunt, setHunt] = useState<Hunt[]>([]);
  const [huntReport, setHuntReport] = useState<{ detected: number; total: number; detection_rate: number; clean_failures: string[] } | null>(null);
  const [lastStatus, setLastStatus] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<"results" | "matrix">("results");
  const [times, setTimes] = useState<{ start: number | null; end: number | null }>({ start: null, end: null });
  const [shot, setShot] = useState<string | null>(null);
  const [current, setCurrent] = useState<string | null>(null);
  const stop = useRef<(() => void) | null>(null);
  const elapsed = useElapsed(times.start, times.end);

  useEffect(() => {
    api.scenarios().then((s) => { setScenarios(s); setSelected(new Set(s.map((x) => x.name))); }).catch(() => setScenarios([]));
    api.bugs().then(setBugs).catch(() => setBugs([]));
    return () => stop.current?.();
  }, []);

  const tags = useMemo(() => [...new Set((scenarios || []).flatMap((s) => s.tags))].sort(), [scenarios]);
  const visible = useMemo(() => (scenarios || []).filter((s) => {
    const hay = (s.name + " " + s.tags.join(" ")).toLowerCase();
    return (!query || hay.includes(query.toLowerCase())) && (!tag || s.tags.includes(tag));
  }), [scenarios, query, tag]);
  const names = (scenarios || []).filter((s) => selected.has(s.name)).map((s) => s.name);

  async function start(m_: "run" | "bughunt") {
    if (running) return;
    if (!names.length) { toast("Select at least one scenario.", "bad"); return; }
    try {
      const { run_id } = await api.startUiRun({ mode: m_, names, bugs: [...active], timeout_ms: timeout });
      setMode(m_); setRunning(true); setTimes({ start: Date.now(), end: null });
      if (m_ === "run") { setResults([]); setTab("results"); } else { setHunt([]); setHuntReport(null); setTab("matrix"); }
      stop.current = followRun(run_id, (e) => {
        if (e.kind === "scenario_start") setCurrent(e.data.name);
        else if (e.kind === "scenario_done" && m_ === "run") {
          setResults((rs) => [...rs, e.data]); setLastStatus((s) => ({ ...s, [e.data.name]: e.data.status }));
        } else if (e.kind === "bug_start") setCurrent(`injecting “${e.data.bug}”`);
        else if (e.kind === "bug_done") setHunt((h) => [...h, e.data]);
        else if (e.kind === "report" && e.data.mode === "bughunt") {
          setHunt(Object.entries(e.data.bugs as Record<string, string[]>).map(([bug, caught_by]) => ({ bug, caught_by })));
          setHuntReport(e.data);
        } else if (e.kind === "error") toast(e.data.message, "bad");
      }, () => { setRunning(false); setCurrent(null); setTimes((t) => ({ ...t, end: Date.now() })); });
    } catch (e) { toast((e as Error).message, "bad"); }
  }

  const passed = results.filter((r) => r.status === "passed").length;
  const failed = results.length - passed;
  const huntMode = mode === "bughunt";
  const bugTotal = active.size || bugs.length;
  const progress = huntMode ? hunt.length / Math.max(1, bugTotal) : results.length / Math.max(1, names.length);
  const k1 = huntMode ? huntReport?.detection_rate ?? (hunt.length ? hunt.filter((h) => h.caught_by.length).length / hunt.length : null)
    : results.length ? passed / results.length : null;

  return (
    <m.div variants={stagger(0.06)} initial="hidden" animate="show">
      <PageHeader eyebrow="UI testing" title="UI test automation"
        subtitle="A Playwright engine runs keyword-driven scenarios against ShopLite. Inject bugs to prove the tests catch them."
        right={<><Badge tone={running ? "accent" : times.end ? "ok" : "muted"}>{running && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}{running ? (huntMode ? "Bug hunt running" : "Running") : times.end ? "Finished" : "Idle"}</Badge><Badge className="font-mono">{elapsed}</Badge></>} />

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
        <m.div variants={stagger(0.07)} className="flex min-w-0 flex-col gap-5">
          <Card>
            <CardHead step={1} title="Scenarios" right={<span className="text-[12.5px] text-muted">{names.length} of {scenarios?.length ?? 0} selected</span>} />
            <div className="flex flex-col gap-3 p-4">
              <div className="relative"><Icon name="search" className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input type="search" className="input pl-9" placeholder="Search scenarios…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search scenarios" /></div>
              <LayoutGroup id="tags">
                <div className="flex flex-wrap gap-1.5">
                  {[null, ...tags].map((t) => {
                    const on = tag === t;
                    return (
                      <button key={t ?? "all"} onClick={() => setTag(t)} className={clsx("relative rounded-full px-3 py-1 text-[12px] font-medium transition-colors", on ? "text-ink" : "text-ink-2 hover:text-ink")}>
                        {on && <m.span layoutId="tag-pill" transition={spring.snappy} className="absolute inset-0 rounded-full border border-accent/50 bg-accent/15" />}
                        <span className="relative">{t ?? "All"}</span>
                      </button>
                    );
                  })}
                </div>
              </LayoutGroup>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => setSelected((s) => new Set([...s, ...visible.map((v) => v.name)]))}>Select visible</Button>
                <Button size="sm" onClick={() => setSelected((s) => { const n = new Set(s); visible.forEach((v) => n.delete(v.name)); return n; })}>Clear visible</Button>
              </div>
              <div className="max-h-[330px] overflow-auto rounded-xl border border-line">
                {scenarios == null ? <div className="flex flex-col gap-2 p-3">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-11" />)}</div> : (
                  <AnimatePresence initial={false}>
                    {visible.map((s) => (
                      <m.label key={s.name} layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, height: 0 }}
                        className="grid cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-3 border-b border-line px-3 py-2.5 last:border-0 hover:bg-panel-2/60">
                        <input type="checkbox" className="mt-1 accent-[rgb(var(--accent))]" checked={selected.has(s.name)}
                          onChange={(e) => setSelected((cur) => { const n = new Set(cur); e.target.checked ? n.add(s.name) : n.delete(s.name); return n; })} />
                        <span className="min-w-0"><span className="block text-[13px] font-semibold">{s.name}</span>
                          <span className="mt-1 flex flex-wrap gap-1">{s.tags.map((t) => <span key={t} className="rounded-full border border-line px-2 text-[11px] text-muted">{t}</span>)}</span></span>
                        <span className="flex items-center gap-1.5 whitespace-nowrap text-[11.5px] text-muted">
                          <i className={clsx("h-2 w-2 rounded-full", lastStatus[s.name] === "passed" ? "bg-ok" : lastStatus[s.name] ? "bg-bad" : "bg-line-2")} />{s.steps.length} steps</span>
                      </m.label>
                    ))}
                  </AnimatePresence>
                )}
              </div>
            </div>
          </Card>

          <Card>
            <CardHead step={2} title="Inject bugs into ShopLite" right={<Badge tone={active.size ? "bad" : "muted"}>{active.size} active</Badge>} />
            <div className="px-4 py-2">
              {bugs.map((b) => (
                <div key={b.id} className="border-b border-dashed border-line last:border-0">
                  <Switch tone="bad" checked={active.has(b.id)} label={<span className="font-mono">{b.id}</span>} hint={b.description}
                    onChange={(v) => setActive((cur) => { const n = new Set(cur); v ? n.add(b.id) : n.delete(b.id); return n; })} />
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <CardHead step={3} title="Run" />
            <div className="flex flex-col gap-4 p-4">
              <RangeField label="Assertion timeout" value={timeout} display={`${(timeout / 1000).toFixed(1)} s`} min={1000} max={10000} step={500} onChange={setTimeoutMs} />
              <Button variant="primary" size="lg" magnetic icon="play" loading={running && mode === "run"} disabled={running} onClick={() => start("run")}>Run selected scenarios</Button>
              <div className="grid grid-cols-2 gap-2">
                <Button icon="bug" loading={running && huntMode} disabled={running} onClick={() => start("bughunt")}>Run bug hunt</Button>
                <Button icon="external" onClick={() => window.open("/demo" + (active.size ? `?bugs=${[...active].join(",")}` : ""), "_blank", "noopener")}>Open ShopLite</Button>
              </div>
            </div>
          </Card>
        </m.div>

        <m.div variants={stagger(0.07, 0.1)} className="flex min-w-0 flex-col gap-5 xl:sticky xl:top-6 xl:-m-1 xl:max-h-[calc(100vh-3rem)] xl:overflow-y-auto xl:p-1">
          <m.div variants={stagger(0.05)} className="grid grid-cols-2 gap-3 md:grid-cols-[minmax(0,1.25fr)_repeat(2,minmax(0,1fr))]">
            <TiltCard className="col-span-2 md:col-span-1 md:row-span-2">
              <div className="flex h-full items-center gap-4 p-5" style={{ background: "linear-gradient(135deg, rgb(var(--ok)/.10), transparent 65%)" }}>
                <Gauge value={k1} size={92} tone="ok" />
                <div className="min-w-0">
                  <div className="text-[12px] font-medium text-muted">{huntMode ? "Detection rate" : "Pass rate"}</div>
                  <div className="text-[36px] font-bold leading-tight tracking-tight"><AnimatedNumber value={k1 != null ? k1 * 100 : null} format={(v) => `${Math.round(v)}%`} /></div>
                  <div className="text-[12.5px] text-muted">{huntMode ? "of injected bugs caught" : "of selected scenarios"}</div>
                </div>
              </div>
            </TiltCard>
            {[
              { k: huntMode ? "Bugs caught" : "Passed", v: huntMode ? hunt.filter((h) => h.caught_by.length).length : passed, c: "text-ok" },
              { k: huntMode ? "Bugs missed" : "Failed", v: huntMode ? hunt.filter((h) => !h.caught_by.length).length : failed, c: "text-bad" },
              { k: "Duration", v: null, text: times.start ? elapsed : "–", c: "" },
              { k: "Bugs injected", v: huntMode ? bugTotal : active.size, c: "" },
            ].map((s) => (
              <m.div key={s.k} variants={rise} whileHover={{ y: -2 }} className="card p-4">
                <div className="text-[12px] font-medium text-muted">{s.k}</div>
                <div className={clsx("text-[24px] font-bold tracking-tight", s.c)}>{s.v == null ? <span className="font-mono">{s.text}</span> : <AnimatedNumber value={s.v} />}</div>
              </m.div>
            ))}
          </m.div>

          <div className="h-1.5 overflow-hidden rounded-full border border-line bg-panel-2">
            <m.div className="h-full rounded-full bg-gradient-to-r from-accent to-accent-2" animate={{ width: `${progress * 100}%` }} transition={spring.gentle} />
          </div>
          <AnimatePresence>{running && current && (
            <m.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="-mt-2 text-[12.5px] text-muted">
              ▸ {current}</m.div>)}</AnimatePresence>

          <Card>
            <Tabs value={tab} onChange={setTab} tabs={[{ value: "results", label: "Scenario results", count: results.length }, { value: "matrix", label: "Bug hunt matrix" }]} />
            <AnimatePresence mode="wait">
              <m.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }} className="p-4">
                {tab === "results" && (results.length === 0
                  ? <EmptyState icon="robot" title={running ? "Starting the browser…" : "No results yet"}>Select scenarios and press <b>Run selected scenarios</b>.</EmptyState>
                  : <m.div variants={stagger(0.03)} initial="hidden" animate="show" className="flex flex-col gap-2">{results.map((r) => <ResultRow key={r.name} r={r} onShot={setShot} />)}</m.div>)}
                {tab === "matrix" && (hunt.length === 0
                  ? <EmptyState icon="bug" title={running ? "Running the clean app first…" : "No bug hunt yet"}>Each bug is injected one at a time; the matrix shows which scenarios catch it.</EmptyState>
                  : <Matrix hunt={hunt} names={names} bugs={bugs} report={huntReport} />)}
              </m.div>
            </AnimatePresence>
          </Card>
        </m.div>
      </div>
      <Modal open={!!shot} onClose={() => setShot(null)} title="Page at the failing step" wide>
        {shot && <img src={shot} alt="Screenshot of the failing step" className="w-full rounded-lg border border-line" />}
      </Modal>
    </m.div>
  );
}

function Matrix({ hunt, names, bugs, report }: { hunt: Hunt[]; names: string[]; bugs: Bug[]; report: { detected: number; total: number; detection_rate: number; clean_failures: string[] } | null }) {
  return (
    <div>
      {report && (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <b>{report.detected} of {report.total} bugs caught ({Math.round(report.detection_rate * 100)}%)</b>
          {report.clean_failures.length ? <Badge tone="bad">{report.clean_failures.length} scenario(s) already fail on the clean app</Badge> : <Badge tone="ok">all scenarios pass on the clean app</Badge>}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="text-[12px]">
          <thead><tr>
            <th className="px-2 pb-2 text-left font-semibold text-muted">Injected bug</th>
            {names.map((n) => <th key={n} title={n} className="px-[3px] pb-2 align-bottom font-medium text-ink-2"><span className="block max-h-[180px] whitespace-nowrap [writing-mode:vertical-rl] rotate-180">{n.length > 26 ? n.slice(0, 25) + "…" : n}</span></th>)}
            <th />
          </tr></thead>
          <tbody>
            {hunt.map((h, ri) => (
              <m.tr key={h.bug} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: ri * 0.03 }}
                className={clsx("border-t border-line", !h.caught_by.length && "bg-warn/10")}>
                <td className="whitespace-nowrap px-2 py-1.5" title={bugs.find((b) => b.id === h.bug)?.description}>
                  <b className="font-mono">{h.bug}</b><div className="max-w-[130px] truncate text-[11px] text-muted">{bugs.find((b) => b.id === h.bug)?.description}</div></td>
                {names.map((n, ci) => {
                  const hit = h.caught_by.includes(n);
                  return (
                    <td key={n} className="px-[3px] text-center">
                      <m.i title={`${n}${hit ? ": caught" : ""}`} initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ ...spring.bouncy, delay: ri * 0.03 + ci * 0.008 }}
                        className={clsx("mx-auto block h-3.5 w-3.5 rounded", hit ? "bg-bad shadow-[0_0_10px_rgb(var(--bad)/.6)]" : "border border-line bg-panel-2")} />
                    </td>
                  );
                })}
                <td className="pl-2">{h.caught_by.length ? <Badge tone="ok">✓ {h.caught_by.length}</Badge> : <Badge tone="warn">MISSED</Badge>}</td>
              </m.tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-[12px] text-muted">A red square means that scenario failed when the bug was injected: the test caught the bug.</p>
    </div>
  );
}
