/* Workspace panels: intelligence overview, counterexample laboratory,
 * autonomous resolution centre and the blind spot evolution timeline. Every
 * button calls a real backend operation; every number comes from the server. */
import { AnimatePresence, m } from "framer-motion";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { bsApi, fmtPct, type Diagnosis, type SessionState, type TimelineItem, type TryResult } from "../../lib/blindspot";
import { useApp } from "../../lib/store";
import { rise, slideIn, spring, stagger } from "../../motion/variants";
import { Badge, Button, Spinner } from "../ui/controls";
import { AnimatedNumber, Gauge, Tooltip } from "../ui/display";
import type { IconName } from "../ui/Icon";

// ------------------------------------------------------------------ overview
export function Overview({ s }: { s: SessionState | null }) {
  const sm = s?.summary;
  const delta = sm && sm.score_before != null && sm.score_after != null ? Math.round((sm.score_after - sm.score_before) * 100) : null;
  const status = !s ? "–" : s.status === "running" ? `${(s.action || "working").replace("_", " ")}…` : s.status;
  const tiles: { k: string; v: number | null | undefined; hint: string; tone?: string }[] = [
    { k: "Analyzed mutants", v: sm?.analyzed_mutants, hint: "every mutant of the module" },
    { k: "Blind spots", v: sm?.discovered, hint: "surviving mutants diagnosed", tone: "text-intel" },
    { k: "Resolved", v: sm?.resolved, hint: "verified on original + mutant + suite", tone: "text-ok" },
    { k: "Unresolved", v: sm?.unresolved, hint: `${sm?.high_priority ?? 0} high priority`, tone: "text-bad" },
  ];
  return (
    <m.div variants={stagger(0.05)} initial="hidden" animate="show" className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-[minmax(0,1.8fr)_repeat(4,minmax(0,1fr))_minmax(0,1.3fr)]">
      <m.div variants={rise} className="card col-span-2 flex min-w-0 items-center gap-4 p-4 md:col-span-4 xl:col-span-1" style={{ background: "linear-gradient(135deg, rgb(var(--intel)/.14), transparent 70%)" }}>
        <Gauge value={sm?.score_after ?? null} size={78} tone="ok" />
        <div>
          <div className="text-[12px] font-medium text-muted">Mutation score</div>
          <div className="flex items-baseline whitespace-nowrap text-[21px] font-bold leading-tight tracking-tight 2xl:text-[26px]">
            <AnimatedNumber value={sm?.score_before != null ? sm.score_before * 100 : null} format={(v) => `${Math.round(v)}%`} />
            <span className="px-1.5 text-muted">→</span>
            <AnimatedNumber value={sm?.score_after != null ? sm.score_after * 100 : null} format={(v) => `${Math.round(v)}%`} className="text-ok" />
          </div>
          <AnimatePresence mode="wait">
            <m.div key={delta ?? "x"} initial={{ opacity: 0, y: 3 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className={clsx("text-[12px]", delta && delta > 0 ? "text-ok" : "text-muted")}>
              {delta != null ? (delta > 0 ? `▲ +${delta} pts, measured by full mutation runs` : "no change yet") : "run an analysis"}
            </m.div>
          </AnimatePresence>
        </div>
      </m.div>
      {tiles.map((t) => (
        <m.div key={t.k} variants={rise} whileHover={{ y: -2 }} transition={spring.snappy} className="card p-4">
          <div className="text-[12px] font-medium text-muted">{t.k}</div>
          <div className={clsx("text-[26px] font-bold tracking-tight", t.tone)}><AnimatedNumber value={t.v ?? null} /></div>
          <div className="truncate text-[11.5px] text-muted">{t.hint}</div>
        </m.div>
      ))}
      <m.div variants={rise} className="card col-span-2 p-4 md:col-span-4 xl:col-span-1">
        <div className="flex items-center gap-2 text-[12px] font-medium text-muted">Diagnosis confidence
          <Tooltip text="How many diagnoses are confirmed by direct measurement plus a distinguishing input. Not a calibrated probability."><span className="cursor-help rounded-full border border-line px-1 text-[10px]">?</span></Tooltip></div>
        <div className="mt-1 flex h-2.5 overflow-hidden rounded-full bg-line">
          {sm && sm.discovered > 0 && (["confirmed", "hypotheses", "inconclusive"] as const).map((k, i) => (
            <m.div key={k} className={["bg-ok", "bg-warn", "bg-muted"][i]} initial={{ width: 0 }} animate={{ width: `${(sm[k] / sm.discovered) * 100}%` }} transition={spring.gentle} />
          ))}
        </div>
        <div className="mt-1.5 text-[11.5px] text-muted">{sm ? `${sm.confirmed} confirmed · ${sm.hypotheses} hypotheses · ${sm.inconclusive} inconclusive` : "–"}</div>
        <div className="mt-2 flex items-center gap-2 text-[12px]">
          <span className="text-muted">Status</span>
          <Badge tone={s?.status === "running" ? "intel" : s?.status === "failed" ? "bad" : "muted"}>
            {s?.status === "running" && <Spinner size={11} />}{status}</Badge>
          {sm?.resolution_rate != null && <span className="ml-auto text-muted">resolution {fmtPct(sm.resolution_rate)}</span>}
        </div>
      </m.div>
    </m.div>
  );
}

// ------------------------------------------------------------------ counterexample lab
function splitArgs(text: string): string[] {
  const out: string[] = []; let depth = 0, cur = "", quote: string | null = null;
  for (const ch of text) {
    if (quote) { cur += ch; if (ch === quote) quote = null; continue; }
    if (ch === "'" || ch === '"') { quote = ch; cur += ch; continue; }
    if ("([{".includes(ch)) depth++;
    if (")]}".includes(ch)) depth--;
    if (ch === "," && depth === 0) { out.push(cur.trim()); cur = ""; continue; }
    cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

export function CounterexampleLab({ s, d }: { s: SessionState | null; d: Diagnosis | null }) {
  const { toast } = useApp();
  const cx = d?.counterexample;
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [runs, setRuns] = useState<(TryResult & { id: number })[]>([]);
  const key = d?.blind_spot_id;
  const assertion = useMemo(() => (d?.proposed_test || "").split("\n").find((l) => /^\s*(assert|with pytest\.raises)/.test(l))?.trim(), [d?.proposed_test]);
  async function run(args?: string[]) {
    if (!s || !d) return;
    setBusy(true);
    try {
      const r = await bsApi.tryInputs(s.id, d.blind_spot_id, args);
      setRuns((xs) => [{ ...r, id: Date.now() }, ...xs].slice(0, 8));
    } catch (e) { toast((e as Error).message, "bad"); } finally { setBusy(false); }
  }
  if (!d) return <p className="px-4 py-8 text-center text-[13px] text-muted">Select a blind spot to explore inputs that separate the original from the mutant.</p>;
  return (
    <div className="flex flex-col gap-3 p-4" key={key}>
      <div className="grid gap-2 rounded-xl border border-line bg-panel-2/40 p-3 text-[12.5px] sm:grid-cols-2">
        <div><span className="text-muted">Smallest counterexample</span><div className="font-mono font-semibold">{cx ? cx.call : d.counterexample_searched ? "none found" : "not searched"}</div>
          {cx && <div className="text-muted">{cx.source}{cx.alternatives > 0 ? ` · ${cx.alternatives} more found` : ""}</div>}</div>
        <div><span className="text-muted">Expected difference</span><div className="font-mono">{cx ? <>original <b className="text-ok">{cx.original.repr}</b> · mutant <b className="text-bad">{cx.mutant.repr}</b></> : "–"}</div>
          {cx?.original.state && cx.original.state !== cx.mutant.state && <div className="font-mono text-[11.5px] text-muted">state {cx.original.state} vs {cx.mutant.state}</div>}</div>
        {assertion && <div className="sm:col-span-2"><span className="text-muted">Relevant assertion</span><div className="font-mono text-ok">{assertion}</div></div>}
      </div>
      <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); run(text.trim() ? splitArgs(text) : undefined); }}>
        <label className="flex min-w-[200px] flex-1 flex-col gap-1"><span className="label">Arguments for {cx?.function ?? d.function_name ?? "?"}(…) as Python literals</span>
          <input className="input font-mono" value={text} placeholder={cx ? cx.args.join(", ") : "e.g. 18"} onChange={(e) => setText(e.target.value)} spellCheck={false} /></label>
        <Button type="submit" variant="primary" icon="play" loading={busy}>Run on both</Button>
        {cx && <Button type="button" icon="target" disabled={busy} onClick={() => run(undefined)}>Run suggested</Button>}
      </form>
      <div className="flex flex-col gap-1.5">
        <AnimatePresence initial={false}>
          {runs.map((r) => (
            <m.div key={r.id} variants={slideIn} initial="hidden" animate="show" className="overflow-hidden">
              <div className={clsx("grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-2 rounded-lg border px-3 py-2 font-mono text-[12px]", r.differs ? "border-ok/40 bg-ok/5" : "border-line")}>
                <span className="truncate" title={r.call}>{r.call}</span>
                <span className="truncate">orig: {r.original.kind === "exception" ? `raises ${r.original.repr}` : r.original.repr}</span>
                <span className="truncate">mut: {r.mutant.kind === "exception" ? `raises ${r.mutant.repr}` : r.mutant.repr}</span>
                <Badge tone={r.differs ? "ok" : "muted"}>{r.differs ? "differs" : "same"}</Badge>
              </div>
            </m.div>
          ))}
        </AnimatePresence>
        {runs.length === 0 && <p className="text-[12px] text-muted">Runs execute in the sandbox on the original module and on mutant #{d.mutant_id}.</p>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ resolution centre
export function ResolutionCentre({ s, d, onAction }: { s: SessionState | null; d: Diagnosis | null; onAction: () => void }) {
  const { toast } = useApp();
  const running = s?.status === "running";
  const live = !!s?.live;
  async function act(name: string, fn: () => Promise<unknown>) {
    try { await fn(); onAction(); } catch (e) { toast(`${name}: ${(e as Error).message}`, "bad"); }
  }
  const actions: { label: string; icon: IconName; hint: string; disabled: boolean; run: () => Promise<unknown>; variant?: "primary" | "danger" }[] = s ? [
    { label: "Investigate selected", icon: "scan", hint: "re-collect evidence for the selected mutant", disabled: !d || running || !live, run: () => bsApi.investigate(s.id, d!.mutant_id) },
    { label: "Generate corrective test", icon: "sparkles", hint: "from the counterexample, or by the LLM from the evidence", disabled: !d || running || !live || d.resolution_status === "resolved", run: () => bsApi.corrective(s.id, d!.blind_spot_id) },
    { label: "Execute corrective test", icon: "play", hint: "run it on the original program", disabled: !d?.proposed_test || running || !live, run: () => bsApi.execute(s.id, d!.blind_spot_id) },
    { label: "Verify mutation detection", icon: "shield", hint: "original passes · mutant fails · suite green", disabled: !d?.proposed_test || running || !live || d.resolution_status === "resolved", run: () => bsApi.verify(s.id, d!.blind_spot_id) },
    { label: "Resolve all eligible", icon: "zap", hint: "generate + verify every open blind spot, then re-analyse", disabled: running || !live || !s.summary.unresolved, run: () => bsApi.resolveAll(s.id), variant: "primary" },
    { label: "Reanalyze test suite", icon: "refresh", hint: "fresh mutation run and diagnosis", disabled: running || !live, run: () => bsApi.analyze(s.id) },
  ] : [];
  return (
    <div className="flex flex-col gap-3 p-4">
      {!live && s && <p className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-[12.5px] text-warn">Saved snapshot (read-only): start a new session to run actions.</p>}
      <m.div variants={stagger(0.04)} initial="hidden" animate="show" className="grid grid-cols-1 gap-2 sm:grid-cols-2 [&>*]:min-w-0">
        {actions.map((a) => (
          <m.div key={a.label} variants={rise} className="min-w-0">
            <Button className="w-full justify-start overflow-hidden" variant={a.variant ?? "ghost"} icon={a.icon} disabled={a.disabled}
              loading={running && s?.action === { "Investigate selected": "investigate", "Generate corrective test": "corrective", "Execute corrective test": "execute", "Verify mutation detection": "verify", "Resolve all eligible": "resolve_all", "Reanalyze test suite": "analyze" }[a.label]}
              onClick={() => act(a.label, a.run)}>
              <span className="flex min-w-0 flex-1 flex-col items-start overflow-hidden leading-tight"><span>{a.label}</span><span className="w-full truncate text-left text-[11px] font-normal opacity-70" title={a.hint}>{a.hint}</span></span>
            </Button>
          </m.div>
        ))}
      </m.div>
      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
        <Button variant="danger" icon="x" disabled={!running} onClick={() => act("Cancel", () => bsApi.cancel(s!.id))}>Cancel analysis</Button>
        <span className="ml-auto text-[12px] text-muted">Export</span>
        {(["json", "csv", "html"] as const).map((f) => (
          <a key={f} href={s ? bsApi.exportUrl(s.id, f) : undefined} target={f === "html" ? "_blank" : undefined} rel="noopener"
            className={clsx("rounded-lg border border-line px-2.5 py-1.5 text-[12px] font-semibold hover:border-line-2", !s && "pointer-events-none opacity-50")}>
            {f === "html" ? "Report (print → PDF)" : f.toUpperCase()}</a>
        ))}
      </div>
      {s?.error && <p className="rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-[12.5px] text-bad">{s.error}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ evolution timeline
const STAGE: Record<string, { icon: string; label: string; tone: string }> = {
  initial_generation: { icon: "✍️", label: "Initial test generation", tone: "text-accent" },
  mutation_started: { icon: "🧬", label: "Mutation analysis", tone: "text-accent" },
  survivors_detected: { icon: "🧬", label: "Mutation survival detection", tone: "text-warn" },
  analysis_started: { icon: "🧠", label: "Analysis started", tone: "text-intel" },
  blind_spot_discovered: { icon: "📡", label: "Blind spot discovery", tone: "text-bad" },
  diagnosis_completed: { icon: "🔬", label: "Diagnostic classification", tone: "text-intel" },
  counterexample_found: { icon: "🎯", label: "Counterexample identification", tone: "text-intel" },
  counterexample_executed: { icon: "🎯", label: "Counterexample executed", tone: "text-intel" },
  corrective_test_generated: { icon: "✍️", label: "Corrective test generation", tone: "text-accent" },
  corrective_tests_executed: { icon: "▶️", label: "Test execution", tone: "text-accent" },
  verification_started: { icon: "🛡️", label: "Mutation verification", tone: "text-warn" },
  blind_spot_resolved: { icon: "✅", label: "Resolved", tone: "text-ok" },
  blind_spot_unresolved: { icon: "⛔", label: "Unresolved", tone: "text-bad" },
  suite_updated: { icon: "📦", label: "Suite updated", tone: "text-ok" },
  analysis_completed: { icon: "🏁", label: "Analysis completed", tone: "text-muted" },
  analysis_cancelled: { icon: "⏹", label: "Cancelled", tone: "text-warn" },
  analysis_failed: { icon: "⚠", label: "Failed", tone: "text-bad" },
};

export function EvolutionTimeline({ s }: { s: SessionState | null }) {
  const [round, setRound] = useState<number | "all">("all");
  const [a, setA] = useState(0);
  const [b, setB] = useState<number | null>(null);
  const items = (s?.timeline ?? []).filter((t) => STAGE[t.type] && (round === "all" || t.round === round));
  const rounds = Array.from(new Set((s?.timeline ?? []).map((t) => t.round))).sort((x, y) => x - y);
  const versions = s?.versions ?? [];
  const vb = b ?? versions.length - 1;
  const names = (code: string) => new Set(Array.from(code.matchAll(/def (test\w+)/g)).map((x) => x[1]));
  const added = versions[a] && versions[vb] ? [...names(versions[vb].tests)].filter((n) => !names(versions[a].tests).has(n)) : [];
  return (
    <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[12px] text-muted">Round</span>
          {(["all", ...rounds] as (number | "all")[]).map((r) => (
            <button key={String(r)} onClick={() => setRound(r)} className={clsx("rounded-lg border px-2 py-0.5 text-[12px] font-semibold", round === r ? "border-intel/50 bg-intel/15 text-intel" : "border-line text-ink-2")}>{r === "all" ? "all" : `#${r}`}</button>
          ))}
          <span className="ml-auto text-[11.5px] text-muted">{items.length} events</span>
        </div>
        <div className="relative max-h-[360px] overflow-auto pl-5">
          <div className="absolute bottom-0 left-[9px] top-0 w-px bg-line" />
          <AnimatePresence initial={false}>
            {items.slice(-160).map((t: TimelineItem, i) => {
              const st = STAGE[t.type];
              return (
                <m.div key={`${t.t}-${i}-${t.type}`} variants={slideIn} initial="hidden" animate="show" className="relative overflow-hidden">
                  <div className="flex items-start gap-3 py-1.5">
                    <span className="absolute left-[-15px] top-2.5 grid h-4 w-4 place-items-center rounded-full border border-line bg-panel text-[9px]">{st.icon}</span>
                    <div className="min-w-0 flex-1">
                      <div className={clsx("text-[12px] font-semibold", st.tone)}>{st.label}{t.round ? <span className="ml-2 font-normal text-muted">round {t.round}</span> : null}</div>
                      <div className="break-words text-[12.5px] text-ink-2">{t.text}</div>
                    </div>
                    <span className="font-mono text-[11px] text-muted">{t.t.toFixed(1)}s</span>
                  </div>
                </m.div>
              );
            })}
          </AnimatePresence>
          {items.length === 0 && <p className="py-6 text-[13px] text-muted">The timeline fills as the engine works.</p>}
        </div>
      </div>
      <div className="rounded-xl border border-line bg-panel-2/40 p-3">
        <div className="eyebrow mb-2">Test-suite versions</div>
        <div className="flex flex-col gap-1.5">
          {versions.map((v, i) => (
            <div key={i} className="flex items-center gap-2 text-[12.5px]">
              <span className="w-5 font-mono text-muted">v{i}</span>
              <span className="min-w-0 flex-1 truncate">{v.label}</span>
              <span className="tabular-nums text-muted">{v.test_count} tests</span>
            </div>
          ))}
        </div>
        {versions.length > 1 && (
          <div className="mt-3 border-t border-line pt-3 text-[12.5px]">
            <div className="flex items-center gap-2"><span className="text-muted">Compare</span>
              <select className="input w-auto py-1" value={a} onChange={(e) => setA(Number(e.target.value))}>{versions.map((_, i) => <option key={i} value={i}>v{i}</option>)}</select>
              <span>→</span>
              <select className="input w-auto py-1" value={vb} onChange={(e) => setB(Number(e.target.value))}>{versions.map((_, i) => <option key={i} value={i}>v{i}</option>)}</select></div>
            <div className="mt-2 text-muted">{added.length} test(s) added{versions[vb] && versions[a] ? `, ${versions[vb].test_count - versions[a].test_count >= 0 ? "+" : ""}${versions[vb].test_count - versions[a].test_count} overall` : ""}</div>
            <div className="mt-1 flex flex-col gap-0.5 font-mono text-[11.5px] text-ok">{added.slice(0, 10).map((n) => <span key={n}>+ {n}</span>)}</div>
          </div>
        )}
        {(s?.scores?.length ?? 0) > 0 && (
          <div className="mt-3 border-t border-line pt-3">
            <div className="eyebrow mb-1.5">Mutation score per measurement</div>
            <div className="flex items-end gap-1.5">
              {s!.scores.map((x, i) => (
                <Tooltip key={i} text={`${x.label}: ${fmtPct(x.score)} (${x.killed}/${x.total})`}>
                  <m.div className="w-6 rounded-t bg-gradient-to-t from-intel to-accent" initial={{ height: 0 }} animate={{ height: Math.max(4, x.score * 70) }} transition={spring.gentle} />
                </Tooltip>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
