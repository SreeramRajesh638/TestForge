/* Run visualisation shared by the live Generate page and the Runs replay:
 * pipeline stepper, KPI grid, activity feed and results tabs. */
import { AnimatePresence, LayoutGroup, m } from "framer-motion";
import clsx from "clsx";
import { memo, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PRIMARY, type Diagnosis } from "../../lib/blindspot";
import { pct, type Survivor } from "../../lib/api";
import { fmtT, STAGES, type RunView } from "../../lib/run";
import { useApp } from "../../lib/store";
import { rise, slideIn, spring, stagger } from "../../motion/variants";
import { LineChart } from "../charts/charts";
import { CodeView } from "../ui/code";
import { Badge, Button, Tabs } from "../ui/controls";
import { AnimatedNumber, Card, CardHead, EmptyState, Gauge, Modal, Skeleton, TiltCard, Tooltip } from "../ui/display";

export const Pipeline = memo(function Pipeline({ view, step, blindSpot }: { view: RunView; step?: number; blindSpot?: boolean }) {
  const activeIdx = STAGES.findIndex((s) => s.key === view.stage);
  const doneCount = view.finished ? STAGES.length : Math.max(activeIdx, 0);
  const n = STAGES.length, half = 50 / n;
  const bsOn = blindSpot || view.bs.enabled;
  return (
    <Card>
      <CardHead step={step} title="Agent pipeline" right={view.stopReason ? <span className="text-[12.5px] text-muted">Stopped: {view.stopReason}</span> : null} />
      <div className="relative overflow-x-auto px-4 pb-4 pt-5">
        <div className="absolute top-[38px] h-[2px] rounded-full bg-line" style={{ left: `calc(${half}% + 16px)`, right: `calc(${half}% + 16px)` }} />
        <m.div className="absolute top-[38px] h-[2px] origin-left rounded-full bg-gradient-to-r from-accent to-accent-2"
          style={{ left: `calc(${half}% + 16px)`, width: `calc(${100 - 2 * half}% - 32px)` }} initial={{ scaleX: 0 }}
          animate={{ scaleX: Math.min(1, doneCount / (n - 1)) }} transition={spring.gentle} />
        <LayoutGroup id="pipeline">
          <div className="relative grid min-w-[460px] grid-cols-7">
            {STAGES.map((s) => {
              const active = view.stage === s.key, done = view.reached.has(s.key) && !active;
              const intel = s.key === "blindspot";
              const off = intel && !bsOn;
              return (
                <div key={s.key} className={clsx("flex flex-col items-center gap-1.5 text-center", off && "opacity-40")} title={off ? "Blind Spot Intelligence is off for this run" : undefined}>
                  <div className="relative grid h-11 w-11 place-items-center">
                    {active && <m.span layoutId="stage-glow" transition={spring.smooth}
                      className={clsx("absolute inset-0 rounded-[14px] border", intel ? "border-intel bg-intel/20 shadow-[0_0_30px_-6px_rgb(var(--intel))]" : "border-accent bg-accent/15 shadow-glow")} />}
                    {active && <m.span className={clsx("absolute inset-0 rounded-[14px] border", intel ? "border-intel" : "border-accent")} animate={{ scale: [1, 1.35], opacity: [0.6, 0] }} transition={{ repeat: Infinity, duration: 1.4 }} />}
                    <m.span animate={{ scale: active ? 1.08 : 1 }} transition={spring.bouncy}
                      className={clsx("relative grid h-11 w-11 place-items-center rounded-[14px] border text-[18px] transition-colors",
                        active ? "border-transparent" : done && !off ? (intel ? "border-intel/50 bg-intel/10" : "border-ok/50 bg-ok/10") : intel ? "border-intel/30 bg-intel/5" : "border-line bg-panel-2")}>{s.icon}</m.span>
                  </div>
                  <span className={clsx("text-[12px] font-semibold", active ? "text-ink" : intel ? "text-intel" : "text-ink-2")}>{s.label}</span>
                  <span className="hidden text-[11px] text-muted sm:block">{off ? "off" : s.who}</span>
                </div>
              );
            })}
          </div>
        </LayoutGroup>
      </div>
    </Card>
  );
});

export function KpiGrid({ view, loading }: { view: RunView; loading?: boolean }) {
  const it = view.iterations, last = it[it.length - 1], first = it[0];
  const gain = last && first && it.length > 1 && last.mutation_score != null && first.mutation_score != null
    ? Math.round((last.mutation_score - first.mutation_score) * 100) : null;
  const eqv = last?.mutants_equivalent ?? 0;
  const small = [
    { k: "Tests passing", v: last ? `${last.tests_passing}/${last.tests_total}` : null, d: last ? (last.pruned_tests ? `${last.pruned_tests} failing removed` : last.fix_rounds ? `${last.fix_rounds} fix round(s)` : "all pass") : "" },
    { k: "Mutants killed", v: last ? `${last.mutants_killed}/${last.mutants_total - eqv}` : null, d: last ? `${last.mutants_survived} survived${eqv ? ` · ${eqv} equivalent` : ""}` : "" },
    { k: "Line coverage", num: last?.line_coverage, d: "statements run" },
    { k: "Branch coverage", num: last?.branch_coverage, d: "both outcomes taken" },
  ];
  return (
    <m.div variants={stagger(0.05)} initial="hidden" animate="show" className="grid grid-cols-2 gap-3 md:grid-cols-[minmax(0,1.25fr)_repeat(2,minmax(0,1fr))]">
      <TiltCard className="col-span-2 md:col-span-1 md:row-span-2">
        <div className="flex h-full items-center gap-4 p-5" style={{ background: "linear-gradient(135deg, rgb(var(--accent)/.12), transparent 65%)" }}>
          <div className="relative">
            <Gauge value={last?.mutation_score ?? null} size={92} />
            {loading && !last && <span className="absolute inset-0 grid place-items-center"><span className="h-3 w-3 animate-ping rounded-full bg-accent" /></span>}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-[12px] font-medium text-muted">Mutation score
              <Tooltip text="Share of planted bugs (mutants) that the tests detect. Higher is better; equivalent mutants are excluded."><span className="cursor-help rounded-full border border-line px-1 text-[10px]">?</span></Tooltip></div>
            {loading && !last ? <Skeleton className="my-1.5 h-9 w-24" /> :
              <div className="text-[36px] font-bold leading-tight tracking-tight"><AnimatedNumber value={last?.mutation_score != null ? last.mutation_score * 100 : null} format={(v) => `${Math.round(v)}%`} /></div>}
            <AnimatePresence mode="wait">
              <m.div key={gain ?? "first"} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                className={clsx("text-[12.5px]", gain != null && gain > 0 ? "text-ok" : "text-muted")}>
                {gain != null ? `▲ +${gain} pts from ${pct(first.mutation_score)}` : last ? "first round" : "planted bugs caught"}
              </m.div>
            </AnimatePresence>
          </div>
        </div>
      </TiltCard>
      {small.map((s) => (
        <m.div key={s.k} variants={rise} whileHover={{ y: -2 }} transition={spring.snappy} className="card p-4">
          <div className="text-[12px] font-medium text-muted">{s.k}</div>
          {loading && !last ? <Skeleton className="my-1.5 h-7 w-16" /> :
            <div className="text-[24px] font-bold tracking-tight">
              {"num" in s ? <AnimatedNumber value={s.num != null ? s.num * 100 : null} format={(v) => `${Math.round(v)}%`} /> : <span className="tabular-nums">{s.v ?? "–"}</span>}
            </div>}
          <div className="truncate text-[12px] text-muted">{s.d || " "}</div>
        </m.div>
      ))}
    </m.div>
  );
}

export function ActivityFeed({ view, running }: { view: RunView; running: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight, behavior: "smooth" }); }, [view.feed.length]);
  return (
    <Card>
      <CardHead title="Agent activity" right={<span className="flex items-center gap-2 text-[12px] text-muted">
        {running && <span className="relative flex h-2 w-2"><span className="absolute inset-0 animate-ping rounded-full bg-ok" /><span className="h-2 w-2 rounded-full bg-ok" /></span>}
        {view.feed.length} events</span>} />
      <div ref={box} className="max-h-[300px] overflow-auto px-4 py-1">
        {view.feed.length === 0 ? <EmptyState icon="robot" title="No activity yet">Start a run to watch the agents work.</EmptyState> : (
          <AnimatePresence initial={false}>
            {view.feed.map((f) => (
              <m.div key={f.id} variants={slideIn} initial="hidden" animate="show" className="overflow-hidden">
                <div className="grid grid-cols-[30px_minmax(0,1fr)_auto] items-start gap-3 border-b border-dashed border-line py-2.5">
                  <div className={clsx("grid h-[30px] w-[30px] place-items-center rounded-lg border text-[14px]",
                    f.tone === "phase" ? "border-transparent bg-accent/15 text-accent" : f.tone === "done" ? "border-transparent bg-ok/15" : f.tone === "error" ? "border-transparent bg-bad/15" : "border-line bg-panel-2")}>{f.icon}</div>
                  <div className="min-w-0">
                    <div className={clsx("text-[13px] font-semibold", f.tone === "error" && "text-bad")}>{f.who}</div>
                    <div className={clsx("break-words text-[13px]", f.tone === "error" ? "text-bad" : "text-ink-2")}>{f.what}</div>
                  </div>
                  <div className="font-mono text-[11px] text-muted">{fmtT(f.t)}</div>
                </div>
              </m.div>
            ))}
          </AnimatePresence>
        )}
      </div>
    </Card>
  );
}

function MutantCard({ m: mu, onOpen, diagnosis, onInvestigate }: { m: Survivor; onOpen: () => void; diagnosis?: Diagnosis; onInvestigate?: () => void }) {
  return (
    <m.div variants={rise} layout whileHover={{ y: -2 }} className="card w-full p-3 text-left">
      <button onClick={onOpen} className="block w-full text-left">
        <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
          <span className="rounded-md bg-accent/15 px-1.5 py-0.5 font-mono text-[11.5px] font-bold text-accent">{mu.operator}</span>
          <span>line {mu.lineno}{mu.function ? <> · <b className="text-ink-2">{mu.function}</b></> : null}</span>
          {diagnosis && <Badge tone="intel">{diagnosis.blind_spot_id} · {PRIMARY[diagnosis.primary_category].label}</Badge>}
          <span className="ml-auto font-mono">#{mu.id}</span>
        </div>
        <div className="mt-2 overflow-hidden rounded-lg border border-line font-mono text-[12.5px]">
          <div className="bg-bad/10 px-3 py-1"><span className="text-bad">− </span>{mu.original}</div>
          <div className="bg-ok/10 px-3 py-1"><span className="text-ok">+ </span>{mu.mutated}</div>
        </div>
      </button>
      {onInvestigate && (
        <div className="mt-2 flex items-center gap-2">
          {diagnosis && <span className="min-w-0 flex-1 truncate text-[12px] text-muted" title={diagnosis.diagnosis}>{diagnosis.diagnosis}</span>}
          <Button size="sm" icon="brain" className="ml-auto border-intel/40 text-intel" onClick={onInvestigate}>Investigate Blind Spot</Button>
        </div>
      )}
    </m.div>
  );
}

function BlindSpotTab({ view, report, runId }: { view: RunView; report: RunView["report"]; runId?: string | null }) {
  const bsReport = (report as unknown as { blind_spot?: { corrective_tests: { blind_spot_id: string; test_name: string; origin: string | null; resolved: boolean; in_suite: boolean; code: string | null; primary_category: string; round: number }[]; summary: Record<string, any> } } | null)?.blind_spot;
  const items = bsReport ? bsReport.corrective_tests.map((c) => ({ id: c.blind_spot_id, name: c.test_name, origin: c.origin, resolved: c.resolved, inSuite: c.in_suite, code: c.code, cat: c.primary_category, round: c.round }))
    : view.bs.corrective.map((c) => ({ id: c.blind_spot_id, name: c.name, origin: c.origin, resolved: c.resolved, inSuite: c.resolved, code: c.code, cat: "", round: 0 }));
  if (!view.bs.enabled && !bsReport) return <EmptyState icon="brain" title="Blind Spot Intelligence was off">Turn it on in the configuration to diagnose surviving mutants and add verified corrective tests.</EmptyState>;
  if (!items.length) return <EmptyState icon="brain" title="No corrective tests yet">{view.finished ? "No blind spot produced a verifiable corrective test in this run." : "Corrective tests appear here as blind spots are diagnosed."}</EmptyState>;
  return (
    <div className="flex flex-col gap-3 p-4">
      <p className="text-[12.5px] text-muted">Targeted corrective tests written to eliminate diagnosed blind spots, separate from the ordinary generated tests.
        {bsReport && ` ${bsReport.summary.resolved} of ${bsReport.summary.discovered} blind spots resolved.`}
        {bsReport && runId && <> Report: {(["html", "json", "csv"] as const).map((f) => (
          <a key={f} className="ml-1 font-semibold text-accent hover:underline" target={f === "html" ? "_blank" : undefined} rel="noopener"
            href={`/api/blindspot/runs/${runId}/export?format=${f}`}>{f === "html" ? "printable (PDF)" : f.toUpperCase()}</a>))}</>}</p>
      <m.div variants={stagger(0.04)} initial="hidden" animate="show" className="flex flex-col gap-2.5">
        {items.map((c) => (
          <m.div key={`${c.id}-${c.name}`} variants={rise} className={clsx("overflow-hidden rounded-xl border", c.resolved ? "border-ok/40" : "border-line")}>
            <div className="flex flex-wrap items-center gap-2 border-b border-line bg-panel-2/60 px-3 py-2 text-[12px]">
              <span className="font-mono font-bold text-intel">{c.id}</span>
              <span className="font-mono">{c.name}</span>
              {c.cat && <Badge tone="intel">{PRIMARY[c.cat as keyof typeof PRIMARY]?.label ?? c.cat}</Badge>}
              <Badge tone={c.resolved ? "ok" : "muted"}>{c.resolved ? "✓ verified" : "not verified"}</Badge>
              <span className="ml-auto text-muted">{c.origin === "counterexample" ? "from counterexample" : c.origin === "llm" ? "LLM, from evidence" : c.origin === "writer" ? "Writer refinement" : ""}{c.inSuite ? " · in final suite" : ""}</span>
            </div>
            {c.code ? <CodeView code={c.code} maxHeight={200} /> : <p className="px-3 py-2 text-[12px] text-muted">Killed by {c.name} from the Writer's targeted refinement (see Generated tests).</p>}
          </m.div>
        ))}
      </m.div>
    </div>
  );
}

const OPERATOR_HELP: Record<string, string> = {
  AOR: "Arithmetic operator replacement: e.g. * becomes /",
  ROR: "Relational operator replacement: e.g. >= becomes >",
  LCR: "Logical connector replacement: and becomes or",
  UOI: "Unary operator removed: e.g. not x becomes x",
  CRP: "Constant replacement: e.g. 100 becomes 101, \"msg\" becomes \"\"",
  RET: "Return value replaced by None",
  ASR: "Augmented assignment replacement: += becomes -=",
};

export function ResultsTabs({ view, moduleName, runId }: { view: RunView; moduleName: string; runId?: string | null }) {
  const { toast } = useApp();
  const navigate = useNavigate();
  const [tab, setTab] = useState<"chart" | "tests" | "blindspot" | "mutants" | "details">("chart");
  const bsDiag = new Map((((view.report as unknown as { blind_spot?: { diagnoses: Diagnosis[] } } | null)?.blind_spot?.diagnoses) ?? []).map((d) => [d.mutant_id, d]));
  const correctiveCount = (view.report as unknown as { blind_spot?: { corrective_tests: unknown[] } } | null)?.blind_spot?.corrective_tests.length ?? view.bs.corrective.length;
  const [open, setOpen] = useState<Survivor | null>(null);
  const r = view.report, it = view.iterations;
  const tests = r?.final_tests || "";
  const labels = it.map((_, i) => (i === 0 ? "initial" : `round ${i}`));
  const last = r?.iterations?.[r.iterations.length - 1];

  return (
    <Card>
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: "chart", label: "Progress" },
        { value: "tests", label: "Generated tests", count: last?.tests_total ?? 0 },
        { value: "blindspot", label: "Blind Spot Refinement", count: correctiveCount },
        { value: "mutants", label: "Surviving mutants", count: r?.survivors.length ?? 0 },
        { value: "details", label: "Run details" },
      ]} />
      <AnimatePresence mode="wait">
        <m.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.2 }}>
          {tab === "chart" && (
            <div className="p-4">
              <LineChart xLabels={labels} empty="Scores appear here after the first mutation run" series={[
                { key: "m", label: "Mutation score", color: "rgb(var(--accent))", points: it.map((x) => x.mutation_score), emphasis: true },
                { key: "l", label: "Line coverage", color: "rgb(var(--ok))", points: it.map((x) => x.line_coverage) },
                { key: "b", label: "Branch coverage", color: "rgb(var(--warn))", points: it.map((x) => x.branch_coverage) },
              ]} />
            </div>
          )}
          {tab === "tests" && (tests ? (
            <div>
              <div className="flex items-center gap-2 border-b border-line px-4 py-2 text-[12px]">
                <span className="font-mono text-ink-2">test_{moduleName}.py</span>
                <span className="ml-auto flex gap-2">
                  <Button size="sm" icon="copy" onClick={() => { navigator.clipboard.writeText(tests); toast("Tests copied to the clipboard", "ok"); }}>Copy</Button>
                  <Button size="sm" icon="download" onClick={() => {
                    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([tests], { type: "text/x-python" }));
                    a.download = `test_${moduleName}.py`; a.click(); URL.revokeObjectURL(a.href);
                  }}>Download</Button>
                </span>
              </div>
              <CodeView code={tests} />
            </div>
          ) : <EmptyState icon="code" title="No tests yet">The final test file appears here when the run finishes.</EmptyState>)}
          {tab === "blindspot" && <BlindSpotTab view={view} report={r} runId={runId} />}
          {tab === "mutants" && (
            <div className="p-4">
              {!r ? <EmptyState icon="dna" title="No results yet" /> : r.survivors.length === 0 ? (
                <EmptyState icon="check" title="Every planted bug was caught">The tests detect all non-equivalent mutants.</EmptyState>
              ) : (
                <>
                  <p className="mb-3 text-[12.5px] text-muted">{r.survivors.length} planted bug(s) were not caught. Some may be <b className="text-ink-2">equivalent</b>: no test can tell them apart from the original. Click one for details.</p>
                  <m.div variants={stagger(0.04)} initial="hidden" animate="show" className="grid gap-2.5">
                    {r.survivors.map((s) => <MutantCard key={s.id} m={s} onOpen={() => setOpen(s)} diagnosis={bsDiag.get(s.id)}
                      onInvestigate={runId && view.finished ? () => navigate(`/blindspot?run=${runId}&mutant=${s.id}`) : undefined} />)}
                  </m.div>
                </>
              )}
            </div>
          )}
          {tab === "details" && (
            <div className="p-4">
              {!r ? <EmptyState icon="chart" title="No results yet" /> : (
                <table className="w-full text-[13px]"><tbody>
                  {[
                    ["Backend", r.config.llm === "mock" ? "Mock (offline, no AI)" : `${r.config.llm} · ${r.config.model}`],
                    ["Module", `${r.module.module} · ${r.module.functions} functions · ${r.module.loc} lines · complexity ${r.module.total_complexity}`],
                    ["Stop reason", r.stop_reason.replace(/_/g, " ")],
                    ["Refinement rounds", Math.max(0, r.iterations.length - 1)],
                    ["Mutants", last ? `${last.mutants_total} planted · ${last.mutants_killed} killed · ${last.mutants_survived} survived · ${last.mutants_equivalent} equivalent` : "–"],
                    ["Time", `${r.elapsed_s} s`],
                    ["Tokens (in / out)", `${r.total_input_tokens.toLocaleString()} / ${r.total_output_tokens.toLocaleString()}`],
                    ["Estimated cost", `$${r.cost_usd}`],
                    ...(((r as unknown as { blind_spot?: { summary: Record<string, any> } }).blind_spot) ? (() => {
                      const sm = (r as unknown as { blind_spot: { summary: Record<string, any> } }).blind_spot.summary;
                      return [["Blind Spot Intelligence", `${sm.discovered} blind spots · ${sm.resolved} resolved (verified) · ${sm.unresolved} unresolved · ${sm.confirmed} confirmed / ${sm.hypotheses} hypotheses / ${sm.inconclusive} inconclusive · ${sm.potential_equivalent} possibly equivalent`]] as [string, string][];
                    })() : []),
                  ].map(([k, v]) => (
                    <tr key={k as string} className="border-b border-line last:border-0"><th className="w-44 py-2 pr-4 text-left text-[12px] font-semibold text-muted">{k}</th><td className="py-2">{v}</td></tr>
                  ))}
                </tbody></table>
              )}
            </div>
          )}
        </m.div>
      </AnimatePresence>
      <Modal open={!!open} onClose={() => setOpen(null)} title={open ? `Surviving mutant #${open.id}` : ""}>
        {open && (
          <div className="flex flex-col gap-4 text-[13.5px]">
            <div className="flex flex-wrap gap-2"><Badge tone="accent">{open.operator}</Badge><Badge>line {open.lineno}</Badge>{open.function && <Badge>{open.function}</Badge>}</div>
            <p className="text-ink-2">{OPERATOR_HELP[open.operator] || "Code mutation"}.</p>
            <div className="overflow-hidden rounded-xl border border-line font-mono text-[13px]">
              <div className="bg-bad/10 px-4 py-2"><span className="text-bad">− original </span><br />{open.original}</div>
              <div className="bg-ok/10 px-4 py-2"><span className="text-ok">+ mutant </span><br />{open.mutated}</div>
            </div>
            <p className="text-ink-2">Every test still passes with this change, so the suite cannot detect this fault. Add a test whose result differs between the two versions, or, if no input can tell them apart, it is an <b>equivalent mutant</b> and can be ignored.</p>
          </div>
        )}
      </Modal>
    </Card>
  );
}
