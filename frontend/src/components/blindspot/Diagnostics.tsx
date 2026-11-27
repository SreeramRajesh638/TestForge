/* AI diagnostic panel: expandable, evidence-based diagnosis cards (shared
 * layout animation), with staggered evidence reveal and a verification
 * checklist whose ticks draw themselves (SVG pathLength) only once the backend
 * has verified each check. */
import { AnimatePresence, LayoutGroup, m } from "framer-motion";
import clsx from "clsx";
import { memo } from "react";
import { PRIMARY, type Diagnosis } from "../../lib/blindspot";
import { rise, spring, stagger } from "../../motion/variants";
import { useDraw } from "../../motion/useDraw";
import { CodeView } from "../ui/code";

function Tick() {
  const len = useDraw(1, { duration: 0.45, ease: "easeOut" });
  return <m.path d="M20 6 9 17l-5-5" fill="none" stroke="rgb(var(--ok))" strokeWidth={3.2} strokeLinecap="round" strokeLinejoin="round" style={{ pathLength: len }} />;
}
import { Badge } from "../ui/controls";
import { SHORT } from "./Heatmap";

const STATUS_STYLE = {
  confirmed: "border-solid border-line-2",
  hypothesis: "border-dashed border-warn/50",
  inconclusive: "border-dotted border-muted/60 opacity-90",
} as const;

export function Check({ ok, label, detail }: { ok: boolean | null; label: string; detail?: string }) {
  return (
    <div className="flex items-start gap-2.5 py-1">
      <span className={clsx("mt-0.5 grid h-5 w-5 flex-none place-items-center rounded-full border", ok ? "border-ok bg-ok/15" : ok === false ? "border-bad bg-bad/10" : "border-line-2")}>
        <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true">
          {ok && <Tick />}
          {ok === false && <path d="M18 6 6 18M6 6l12 12" stroke="rgb(var(--bad))" strokeWidth={3} strokeLinecap="round" />}
        </svg>
      </span>
      <span className="min-w-0 text-[12.5px]"><span className="font-semibold">{label}</span>{detail && <span className="block text-muted">{detail}</span>}</span>
    </div>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <m.div variants={rise} className="grid grid-cols-[130px_minmax(0,1fr)] gap-3 border-b border-dashed border-line py-1.5 text-[12.5px] last:border-0">
      <span className="font-semibold text-muted">{k}</span><span className="min-w-0 break-words">{children}</span>
    </m.div>
  );
}

const Detail = memo(function Detail({ d }: { d: Diagnosis }) {
  const e = d.evidence, v = d.verification, cx = d.counterexample;
  return (
    <m.div variants={stagger(0.045)} initial="hidden" animate="show" className="mt-3">
      <m.p variants={rise} className="mb-2 text-[13px] leading-relaxed text-ink-2">{d.diagnosis}</m.p>
      {d.llm_explanation && <m.p variants={rise} className="mb-2 rounded-lg border border-intel/30 bg-intel/10 px-3 py-2 text-[12.5px]"><b className="text-intel">LLM reading of the evidence: </b>{d.llm_explanation}</m.p>}
      <Row k="Reached">{e.reached == null ? "unknown" : e.reached ? `yes, by ${e.reaching_tests.length} test(s)` : "no test executes this line"}{e.reaching_tests.length > 0 && <span className="block font-mono text-[11.5px] text-muted">{e.reaching_tests.slice(0, 5).join(", ")}</span>}</Row>
      <Row k="Infection">{e.infection_supported ? `${e.infected_evaluations} of ${e.executions} evaluation(s) of ${e.infection_point} differed` : `not measurable: ${e.infection_note || "unsupported"}`}</Row>
      {e.infection_examples.length > 0 && <Row k="Measured values"><span className="font-mono text-[11.5px]">{e.infection_examples.slice(0, 3).map((x, i) => <span key={i} className="block">{x.original} vs {x.mutant} {x.infected ? "⚡" : "="}</span>)}</span></Row>}
      <Row k="Propagation">{e.propagated_calls.length ? `${e.propagated_calls.length} test-level call(s) differ` : "no observable output differed"}{e.noisy_calls > 0 && <span className="block text-muted">{e.noisy_calls} non-deterministic call(s) ignored</span>}</Row>
      {e.propagated_calls.slice(0, 2).map((c, i) => <Row key={i} k={c.test || "call"}><span className="font-mono text-[11.5px]">{c.call} → {c.original} | mutant {c.mutant}</span></Row>)}
      {Object.entries(e.assertions).filter(([, a]) => a.weak.length || a.asserts === 0).slice(0, 2).map(([t, a]) => (
        <Row key={t} k="Assertion">{t}: {a.asserts === 0 ? "no assertion" : <span className="font-mono text-[11.5px]">{a.weak.join("; ")}</span>}</Row>))}
      <Row k="Evidence">{d.evidence_strength} <span className="text-muted">(qualitative: completeness of the measurements, not a probability)</span>
        {d.evidence_notes.map((n, i) => <span key={i} className="block text-muted">• {n}</span>)}</Row>
      {e.unsupported.length > 0 && <Row k="Not measured">{e.unsupported.join("; ")}</Row>}
      <Row k="Suggested input">{cx ? <span className="font-mono">{cx.call}</span> : d.counterexample_searched ? "none found by the differential search" : "–"}{cx && <span className="block text-muted">{cx.source}{cx.tried ? ` · ${cx.tried} candidates` : ""}</span>}</Row>
      {cx && <Row k="Expected difference"><span className="font-mono">original {cx.original.repr} · mutant {cx.mutant.repr}</span></Row>}
      <Row k="Strategy">{d.corrective_strategy}</Row>
      <Row k="Priority">{d.priority.toFixed(2)} <span className="text-muted">{Object.entries(d.priority_factors).filter(([, x]) => x !== 0).map(([k, x]) => `${k} ${x > 0 ? "+" : ""}${x}`).join(" · ")}</span></Row>
      {d.proposed_test && <m.div variants={rise} className="mt-2 overflow-hidden rounded-lg border border-line"><div className="border-b border-line px-3 py-1.5 text-[11.5px] text-muted">Recommended corrective test · {d.test_origin === "llm" ? "written by the LLM from the evidence" : d.test_origin === "counterexample" ? "built from the counterexample" : "from the Writer"}</div><CodeView code={d.proposed_test} maxHeight={200} /></m.div>}
      <m.div variants={rise} className="mt-3 rounded-lg border border-line bg-panel-2/50 px-3 py-2">
        <div className="eyebrow mb-1">Verification</div>
        <Check ok={v.passes_on_original} label="Passes on the original program" />
        <Check ok={v.kills_mutant} label="Fails on the targeted mutant" detail={v.killed_by ? `killed by ${v.killed_by}` : undefined} />
        <Check ok={v.suite_regression_free} label="Whole suite still passes" detail={v.detail || undefined} />
      </m.div>
    </m.div>
  );
});

export const DiagnosisCard = memo(function DiagnosisCard({ d, open, onToggle, flash }: { d: Diagnosis; open: boolean; onToggle: () => void; flash?: number }) {
  const p = PRIMARY[d.primary_category];
  const resolved = d.resolution_status === "resolved";
  return (
    <m.div layout transition={spring.smooth} className={clsx("relative overflow-hidden rounded-xl border-2 bg-panel/70 p-3", STATUS_STYLE[d.finding_status], open && "bg-panel")}>
      <AnimatePresence>
        {flash && (
          <m.span key={flash} aria-hidden className="pointer-events-none absolute inset-0 rounded-xl"
            style={{ background: resolved ? "radial-gradient(circle at 20% 50%, rgb(var(--ok) / .35), transparent 60%)" : "radial-gradient(circle at 20% 50%, rgb(var(--intel) / .35), transparent 60%)" }}
            initial={{ opacity: 0.9, scale: 0.6 }} animate={{ opacity: 0, scale: 1.6 }} exit={{ opacity: 0 }} transition={{ duration: 1.2 }} />
        )}
      </AnimatePresence>
      <m.button layout="position" onClick={onToggle} aria-expanded={open} className="flex w-full flex-wrap items-center gap-2 text-left">
        <span className="font-mono text-[12px] font-bold text-intel">{d.blind_spot_id}</span>
        <Badge tone={p.tone === "intel" ? "intel" : p.tone}>{p.label}</Badge>
        <Badge tone={d.finding_status === "confirmed" ? "accent" : d.finding_status === "hypothesis" ? "warn" : "muted"}>{d.finding_status}</Badge>
        <AnimatePresence mode="popLayout" initial={false}>
          <m.span key={d.resolution_status} initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.8 }} transition={spring.bouncy}>
            <Badge tone={resolved ? "ok" : d.resolution_status === "invalid_test" ? "bad" : "muted"}>{resolved ? "✓ resolved" : d.resolution_status.replace("_", " ")}</Badge>
          </m.span>
        </AnimatePresence>
        <span className="ml-auto text-[11.5px] text-muted">{d.function_name ?? "module"} · line {d.source_line} · {SHORT[d.behaviour_category] ?? d.behaviour_category}</span>
        <span className="w-full font-mono text-[12px] text-ink-2"><span className="text-bad">{d.original_code}</span> → <span className="text-intel">{d.mutated_code}</span> <span className="text-muted">({d.mutation_operator})</span></span>
        {d.secondary_labels.length > 0 && <span className="flex w-full flex-wrap gap-1">{d.secondary_labels.map((l) => <span key={l} className="rounded-md border border-line px-1.5 text-[11px] text-muted">{l}</span>)}</span>}
      </m.button>
      <AnimatePresence initial={false}>
        {open && <m.div key="d" layout initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} transition={spring.smooth}><Detail d={d} /></m.div>}
      </AnimatePresence>
    </m.div>
  );
});

export function DiagnosticList({ items, openId, onOpen, flashes, onInvestigateCode }: {
  items: Diagnosis[]; openId: string | null; onOpen: (id: string | null) => void; flashes: Record<string, number>; onInvestigateCode?: (d: Diagnosis) => void;
}) {
  if (!items.length) return <p className="px-2 py-8 text-center text-[13px] text-muted">No diagnoses match. Start an analysis or relax the filters.</p>;
  return (
    <LayoutGroup id="diagnoses">
      <m.div variants={stagger(0.04)} initial="hidden" animate="show" className="flex flex-col gap-2">
        <AnimatePresence initial={false}>
          {items.map((d) => (
            <m.div key={d.blind_spot_id} variants={rise} layout exit={{ opacity: 0, scale: 0.96 }}>
              <DiagnosisCard d={d} open={openId === d.blind_spot_id} flash={flashes[d.blind_spot_id]}
                onToggle={() => { onOpen(openId === d.blind_spot_id ? null : d.blind_spot_id); onInvestigateCode?.(d); }} />
            </m.div>
          ))}
        </AnimatePresence>
      </m.div>
    </LayoutGroup>
  );
}
