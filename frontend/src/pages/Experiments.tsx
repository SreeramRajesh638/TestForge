/* Experiments: the thesis results, read live from results/<name>/runs.csv. */
import { AnimatePresence, m } from "framer-motion";
import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";
import { api, pct, type Experiment, type ExperimentInfo } from "../lib/api";
import { rise, spring, stagger } from "../motion/variants";
import { PageHeader } from "../components/layout/Shell";
import { BarChart, LineChart, SERIES_COLORS } from "../components/charts/charts";
import { Badge, Button } from "../components/ui/controls";
import { AnimatedNumber, Card, CardHead, Collapse, EmptyState, Skeleton } from "../components/ui/display";

const LABELS: Record<string, string> = {
  mock_baseline: "Non-LLM baseline", groq_full: "Groq · TestForge", groq_single_shot: "Groq · single prompt",
  groq_no_planner: "Groq · no Planner", groq_no_fixer: "Groq · no Fixer", groq_no_feedback: "Groq · no feedback",
  claude_full: "Claude · TestForge", claude_single_shot: "Claude · single prompt", ollama_full: "Local model · TestForge",
  ollama_single_shot: "Local model · single prompt",
  mock_blindspot: "Non-LLM + Blind Spot Intelligence", groq_blindspot: "Groq · TestForge + Blind Spot",
  claude_blindspot: "Claude · TestForge + Blind Spot", ollama_blindspot: "Local model · TestForge + Blind Spot",
};
const label = (c: string) => LABELS[c] || c.replace(/_/g, " ");

export default function Experiments() {
  const [list, setList] = useState<ExperimentInfo[] | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [data, setData] = useState<Record<string, Experiment>>({});
  const [showErrors, setShowErrors] = useState(false);

  useEffect(() => {
    api.experiments().then((l) => {
      setList(l);
      const pref = ["benchmark_mock", "benchmark_groq"].filter((n) => l.some((x) => x.name === n));
      setSelected(pref.length ? pref : l.slice(0, 1).map((x) => x.name));
    }).catch(() => setList([]));
  }, []);
  useEffect(() => {
    selected.filter((n) => !data[n]).forEach((n) => api.experiment(n).then((e) => setData((d) => ({ ...d, [n]: e }))).catch(() => undefined));
  }, [selected, data]);

  const loaded = selected.map((n) => data[n]).filter(Boolean) as Experiment[];
  const summary = loaded.flatMap((e) => e.summary.map((s) => ({ ...s, exp: e.name })));
  const configs = summary.map((s) => s.config);
  const modules = useMemo(() => {
    const all = new Map<string, Record<string, { final: number | null; initial: number | null }>>();
    loaded.forEach((e) => e.modules.forEach((mo) => {
      const cur = all.get(mo.module) || {};
      Object.entries(mo.configs).forEach(([c, v]) => { cur[c] = v; });
      all.set(mo.module, cur);
    }));
    return [...all.entries()].map(([module, cfgs]) => ({ module, cfgs }));
  }, [loaded]);
  const both = modules.filter((m_) => configs.every((c) => m_.cfgs[c]?.final != null));
  const errors = loaded.flatMap((e) => e.errors.map((x) => ({ ...x, exp: e.name })));
  const curveConfigs = loaded.flatMap((e) => Object.entries(e.curves));
  const maxIt = Math.max(1, ...curveConfigs.map(([, pts]) => pts.length));

  return (
    <m.div variants={stagger(0.06)} initial="hidden" animate="show">
      <PageHeader eyebrow="Evaluation" title="Experiments"
        subtitle="Results of the benchmark experiments (experiments/run.py), read directly from the CSV files in results/. Nothing here is estimated." />

      <Card className="mb-5">
        <CardHead title="Result sets" sub="select one or more to compare" />
        <div className="flex flex-wrap gap-2 p-4">
          {list == null ? <Skeleton className="h-16 w-full" /> : list.length === 0 ? (
            <EmptyState icon="chart" title="No experiment results yet">Run <code className="font-mono">python experiments/run.py</code> to create some.</EmptyState>
          ) : list.map((x) => {
            const on = selected.includes(x.name);
            return (
              <m.button key={x.name} whileHover={{ y: -2 }} whileTap={{ scale: 0.98 }} transition={spring.snappy}
                onClick={() => setSelected((s) => (on ? s.filter((n) => n !== x.name) : [...s, x.name]))}
                className={clsx("rounded-xl border px-4 py-3 text-left transition-colors", on ? "gradient-border shadow-glow" : "border-line bg-panel-2 hover:border-line-2")}>
                <div className="font-mono text-[13px] font-semibold">{x.name}</div>
                <div className="text-[12px] text-muted">{x.ok}/{x.runs} runs ok · {x.modules} modules · {x.configs.map(label).join(", ")}</div>
              </m.button>
            );
          })}
        </div>
      </Card>

      {selected.length > 0 && loaded.length < selected.length && <div className="grid gap-3 md:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-28" />)}</div>}

      <AnimatePresence mode="wait">
        {loaded.length > 0 && (
          <m.div key={selected.join("|")} variants={stagger(0.07)} initial="hidden" animate="show" exit={{ opacity: 0 }} className="flex flex-col gap-5">
            <m.div variants={stagger(0.05)} className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {summary.map((s, i) => (
                <m.div key={s.exp + s.config} variants={rise} whileHover={{ y: -3 }} className="card overflow-hidden p-4">
                  <div className="absolute inset-x-0 top-0 h-[3px]" style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} />
                  <div className="text-[12px] font-semibold text-muted">{label(s.config)}</div>
                  <div className="text-[30px] font-bold tracking-tight">
                    <AnimatedNumber value={s.mutation_score_final != null ? s.mutation_score_final * 100 : null} format={(v) => `${v.toFixed(1)}%`} />
                  </div>
                  <div className="text-[12px] text-muted">mean mutation score · {s.runs} module runs{s.failed_runs ? ` · ${s.failed_runs} failed` : ""}</div>
                  <div className="mt-2 grid grid-cols-3 gap-2 border-t border-line pt-2 text-[11.5px]">
                    <div><div className="text-muted">line</div><b>{pct(s.line_coverage)}</b></div>
                    <div><div className="text-muted">branch</div><b>{pct(s.branch_coverage)}</b></div>
                    <div><div className="text-muted">first round</div><b>{pct(s.mutation_score_initial)}</b></div>
                  </div>
                </m.div>
              ))}
            </m.div>

            <div className="grid items-start gap-5 xl:grid-cols-2">
              <Card>
                <CardHead title="Mutation score per configuration" sub="mean over modules" />
                <div className="p-4">
                  <BarChart keys={[{ key: "final", label: "after feedback", color: "rgb(var(--accent))" }, { key: "initial", label: "first round", color: "rgb(var(--line-2))" }]}
                    rows={summary.map((s) => ({ label: label(s.config), sub: s.exp, values: { final: s.mutation_score_final, initial: s.mutation_score_initial } }))} />
                </div>
              </Card>
              <Card>
                <CardHead title="Refinement curve" sub="mean mutation score after each round" />
                <div className="p-4">
                  <LineChart xLabels={Array.from({ length: maxIt }, (_, i) => (i ? `round ${i}` : "initial"))}
                    series={curveConfigs.map(([c, pts], i) => ({
                      key: c, label: label(c), color: SERIES_COLORS[i % SERIES_COLORS.length], emphasis: i === curveConfigs.length - 1,
                      points: Array.from({ length: maxIt }, (_, k) => pts.find((p) => p.iteration === k)?.mutation_score ?? null),
                    }))} />
                  <p className="mt-2 text-[11.5px] text-muted">Later rounds average only the modules that were still refining (others had reached the target or plateaued).</p>
                </div>
              </Card>
            </div>

            <Card>
              <CardHead title="Per-module comparison" sub={configs.length > 1 ? `${both.length} modules with results in every selected configuration` : "final mutation score"} />
              <div className="p-4">
                {(configs.length > 1 ? both : modules).length === 0 ? <EmptyState icon="chart" title="No overlapping modules yet">Some configurations have not covered the same modules.</EmptyState> : (
                  <BarChart keys={configs.map((c, i) => ({ key: c, label: label(c), color: SERIES_COLORS[i % SERIES_COLORS.length] }))}
                    rows={(configs.length > 1 ? both : modules).map((mo) => ({ label: mo.module, values: Object.fromEntries(configs.map((c) => [c, mo.cfgs[c]?.final ?? null])) }))} />
                )}
              </div>
            </Card>

            {errors.length > 0 && (
              <Card>
                <CardHead title="Failed runs" right={<Button size="sm" onClick={() => setShowErrors((s) => !s)}>{showErrors ? "Hide" : `Show ${errors.length}`}</Button>} />
                <Collapse open={showErrors}>
                  <div className="flex flex-col gap-2 p-4">
                    {errors.map((x, i) => (
                      <div key={i} className="rounded-lg border border-line bg-panel-2 p-3 text-[12.5px]">
                        <div className="flex flex-wrap gap-2"><Badge>{x.module}</Badge><Badge tone="accent">{label(x.config)}</Badge><Badge>{x.exp}</Badge></div>
                        <div className="mt-1.5 break-words font-mono text-[12px] text-bad">{x.error}</div>
                      </div>
                    ))}
                  </div>
                </Collapse>
              </Card>
            )}
          </m.div>
        )}
      </AnimatePresence>
    </m.div>
  );
}
