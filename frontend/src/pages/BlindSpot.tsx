/* Blind Spot Intelligence — Cognitive Testing Laboratory.
 * A live session (module + suite + engine on the server). Every panel renders
 * server state; animations are triggered by the session's event stream. */
import { AnimatePresence, LayoutGroup, m } from "framer-motion";
import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type HistoryRow } from "../lib/api";
import { agentStates, bsApi, CELL_STYLE, PRIMARY, useBlindSpotSession, type Diagnosis, type HeatCell, type Primary, type SessionRow } from "../lib/blindspot";
import { useApp } from "../lib/store";
import { rise, spring, stagger } from "../motion/variants";
import { PageHeader } from "../components/layout/Shell";
import { AgentNetwork } from "../components/blindspot/AgentNetwork";
import { CodeInvestigation } from "../components/blindspot/CodeInvestigation";
import { DiagnosticList } from "../components/blindspot/Diagnostics";
import { Heatmap, SHORT, type Pulse } from "../components/blindspot/Heatmap";
import { CounterexampleLab, EvolutionTimeline, Overview, ResolutionCentre } from "../components/blindspot/Panels";
import { Badge, Button, Segmented } from "../components/ui/controls";
import { Card, CardHead, EmptyState, Modal } from "../components/ui/display";

type Backend = "mock" | "groq" | "ollama" | "claude";

function NewSessionModal({ open, onClose, onCreate }: { open: boolean; onClose: () => void; onCreate: (body: Record<string, unknown>) => void }) {
  const [name, setName] = useState("my_module");
  const [source, setSource] = useState("def is_adult(age: int) -> bool:\n    return age >= 18\n");
  const [tests, setTests] = useState("import my_module as m\n\n\ndef test_minor():\n    assert m.is_adult(15) is False\n\n\ndef test_adult():\n    assert m.is_adult(25) is True\n");
  const [llm, setLlm] = useState<Backend>("mock");
  return (
    <Modal open={open} onClose={onClose} title="New session from your own code" wide>
      <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); onCreate({ source, tests, module_name: name, llm }); }}>
        <label className="flex flex-col gap-1"><span className="label">Module name (the tests import it under this name)</span>
          <input className="input font-mono" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="flex flex-col gap-1"><span className="label">Module source</span>
            <textarea className="input h-64 font-mono text-[12.5px]" value={source} onChange={(e) => setSource(e.target.value)} spellCheck={false} /></label>
          <label className="flex flex-col gap-1"><span className="label">Existing pytest suite</span>
            <textarea className="input h-64 font-mono text-[12.5px]" value={tests} onChange={(e) => setTests(e.target.value)} spellCheck={false} /></label>
        </div>
        <div className="flex flex-col gap-1"><span className="label">Backend for LLM-written corrective tests (counterexample-based tests need none)</span>
          <Segmented label="Backend" value={llm} onChange={setLlm} options={(["mock", "groq", "ollama", "claude"] as Backend[]).map((b) => ({ value: b, label: b === "mock" ? "None (offline)" : b }))} /></div>
        <Button type="submit" variant="primary" icon="brain">Create session and analyse</Button>
      </form>
    </Modal>
  );
}

export default function BlindSpot() {
  const { toast } = useApp();
  const [params, setParams] = useSearchParams();
  const sid = params.get("session");
  const { state: s, events, connected, refresh, error } = useBlindSpotSession(sid);
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const [runs, setRuns] = useState<HistoryRow[]>([]);
  const [creating, setCreating] = useState(false);
  const [custom, setCustom] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [cell, setCell] = useState<string | null>(null);
  const [filters, setFilters] = useState({ fn: "", sev: "", op: "", cat: "", status: "" });
  const [pulses, setPulses] = useState<Record<string, Pulse>>({});
  const [flashes, setFlashes] = useState<Record<string, number>>({});
  const seen = useRef(-1);
  const firstSeq = useRef<number | null>(null);  // events before this were already in the session when the page opened
  useEffect(() => { firstSeq.current = null; }, [sid]);
  if (s && firstSeq.current == null) firstSeq.current = s.events_count ?? 0;
  const toastBudget = useRef(0);
  const wantedMutant = useRef<number | null>(params.get("mutant") ? Number(params.get("mutant")) : null);

  const loadSessions = useCallback(() => bsApi.sessions().then(setSessions).catch(() => setSessions([])), []);
  useEffect(() => { loadSessions(); api.history().then((h) => setRuns(h.filter((r) => r.kind === "generate").slice(0, 12))).catch(() => setRuns([])); }, [loadSessions]);

  const create = useCallback(async (body: Record<string, unknown>) => {
    setCreating(true);
    try {
      const r = await bsApi.create(body);
      setCustom(false); setOpenId(null); setCell(null);
      setParams((p) => { const n = new URLSearchParams(p); n.set("session", r.id); n.delete("run"); n.delete("demo"); return n; }, { replace: true });
      loadSessions();
    } catch (e) { toast((e as Error).message, "bad"); } finally { setCreating(false); }
  }, [setParams, loadSessions, toast]);

  // deep links: ?demo=1, ?run=<id>[&mutant=<id>]
  const linked = useRef(false);
  useEffect(() => {
    if (linked.current || sid) return;
    if (params.get("demo")) { linked.current = true; create({ demo: true }); }
    else if (params.get("run")) { linked.current = true; create({ from_run: params.get("run") }); }
  }, [params, sid, create]);

  // react to new events: pulses, card flashes, notifications, sidebar badge
  useEffect(() => {
    const fresh = events.filter((e) => e.seq > seen.current);
    if (!fresh.length) return;
    seen.current = fresh[fresh.length - 1].seq;
    const np: Record<string, Pulse> = {}, nf: Record<string, number> = {};
    for (const e of fresh) {
      if (firstSeq.current == null || e.seq < firstSeq.current) continue;  // history replay: no notifications
      if (e.kind === "action" && e.data.state === "started") toastBudget.current = 3;
      if (e.kind !== "blindspot") continue;
      const d = e.data;
      if (d.type === "blind_spot_discovered") {
        np[`${d.function ?? "(module level)"}|${d.category}`] = { key: "", id: e.seq, kind: "discovered" };
        nf[d.blind_spot_id] = e.seq;
        if (toastBudget.current > 0) { toastBudget.current--; toast(`Blind spot ${d.blind_spot_id}: ${PRIMARY[d.primary as Primary]?.label ?? d.primary} at line ${d.line}`, "info"); }
      }
      if (d.type === "blind_spot_resolved") {
        const diag = s?.diagnoses.find((x) => x.blind_spot_id === d.blind_spot_id);
        if (diag) np[`${diag.function_name ?? "(module level)"}|${diag.behaviour_category}`] = { key: "", id: e.seq, kind: "resolved" };
        nf[d.blind_spot_id] = e.seq;
        toast(`${d.blind_spot_id} resolved and verified`, "ok");
      }
      if (d.type === "analysis_failed") toast(d.text, "bad");
    }
    if (Object.keys(np).length) setPulses((p) => ({ ...p, ...np }));
    if (Object.keys(nf).length) setFlashes((f) => ({ ...f, ...nf }));
  }, [events, s?.diagnoses, toast]);

  useEffect(() => {
    if (s) window.dispatchEvent(new CustomEvent("tf:blindspot", { detail: { unresolved: s.summary.unresolved } }));
  }, [s?.summary.unresolved]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (s && s.status !== "running") loadSessions(); }, [s?.status, s?.summary.resolved, loadSessions]); // eslint-disable-line react-hooks/exhaustive-deps

  // ?mutant=<id>: open its diagnosis, or investigate it once the analysis is done
  useEffect(() => {
    const want = wantedMutant.current;
    if (want == null || !s || s.status === "running") return;
    const d = s.diagnoses.find((x) => x.mutant_id === want);
    if (d) { setOpenId(d.blind_spot_id); wantedMutant.current = null; }
    else if (s.mutants.some((x) => x.id === want && x.status === "survived") && s.live) { wantedMutant.current = null; bsApi.investigate(s.id, want).then(refresh).catch(() => undefined); }
  }, [s, refresh]);

  const running = s?.status === "running";
  const net = useMemo(() => agentStates(events, running), [events, running]);
  const selected: Diagnosis | null = s?.diagnoses.find((d) => d.blind_spot_id === openId) ?? null;
  const lastInvestigated = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.kind === "blindspot" && e.data.type === "mutant_under_investigation") return e.data as { mutant_id: number; line: number };
    }
    return null;
  }, [events]);
  const scanLine = running && lastInvestigated && s?.investigating.includes(lastInvestigated.mutant_id) ? lastInvestigated.line : null;
  const executed = useMemo(() => {
    const t = [...(s?.timeline ?? [])].reverse().find((x) => x.type === "evidence_collected" && Array.isArray(x.executed_lines));
    return t ? new Set(t.executed_lines as number[]) : undefined;
  }, [s?.timeline]);

  const sev = (d: Diagnosis) => (d.priority >= 4 ? "high" : d.priority >= 2 ? "medium" : "low");
  const match = useCallback((d: Diagnosis) =>
    (!filters.fn || (d.function_name ?? "(module level)") === filters.fn) && (!filters.sev || sev(d) === filters.sev) &&
    (!filters.op || d.mutation_operator === filters.op) && (!filters.cat || d.primary_category === filters.cat) &&
    (!filters.status || (filters.status === "resolved" ? d.resolution_status === "resolved" : d.resolution_status !== "resolved")) &&
    (!cell || `${d.function_name ?? "(module level)"}|${d.behaviour_category}` === cell), [filters, cell]);
  const list = (s?.diagnoses ?? []).filter(match);
  const cellMatch = useCallback((c: HeatCell) => {
    if (!filters.fn && !filters.sev && !filters.op && !filters.cat && !filters.status) return true;
    return (s?.diagnoses ?? []).some((d) => c.blind_spots.includes(d.blind_spot_id) && match({ ...d }));
  }, [filters, s?.diagnoses, match]);
  const fns = Array.from(new Set((s?.diagnoses ?? []).map((d) => d.function_name ?? "(module level)")));
  const ops = Array.from(new Set((s?.diagnoses ?? []).map((d) => d.mutation_operator)));
  const selCell = s?.heatmap.cells.find((c) => `${c.function}|${c.category}` === cell);

  return (
    <m.div variants={stagger(0.06)} initial="hidden" animate="show">
      <PageHeader eyebrow="Blind Spot Intelligence" title="Cognitive Testing Laboratory"
        subtitle="Why do the tests miss each surviving mutant? Measured reachability, infection, propagation and assertion evidence, distinguishing inputs, and corrective tests that are verified before they count."
        right={<>
          {s && <Badge tone={connected ? "ok" : "muted"}>{connected ? "● live" : "○ offline"}</Badge>}
          <Button icon="flask2" loading={creating} onClick={() => create({ demo: true })}>Demonstration</Button>
          <Button icon="code" onClick={() => setCustom(true)}>Your code</Button>
        </>} />

      <m.div variants={rise} className="mb-5 flex flex-wrap items-center gap-2 text-[13px]">
        <span className="text-muted">Session</span>
        <select aria-label="Session" className="input w-auto max-w-[340px] py-1.5" value={sid ?? ""} onChange={(e) => setParams(e.target.value ? { session: e.target.value } : {})}>
          <option value="">Choose a session…</option>
          {sessions?.map((x) => <option key={x.id} value={x.id}>{x.module} · {x.origin} · {x.resolved ?? 0}/{x.discovered ?? 0} resolved{x.live ? "" : " (saved)"}</option>)}
        </select>
        <span className="ml-2 text-muted">or analyse a finished run</span>
        <select aria-label="Run" className="input w-auto max-w-[300px] py-1.5" value="" onChange={(e) => e.target.value && create({ from_run: e.target.value })}>
          <option value="">Choose a run…</option>
          {runs.map((r) => <option key={r.id} value={r.id}>{r.summary.module} · {r.summary.backend} · {r.summary.score != null ? `${Math.round(r.summary.score * 100)}%` : "–"}</option>)}
        </select>
        {s && <span className="ml-auto text-[12px] text-muted">{s.module_name}.py · backend {s.llm} · {s.origin}</span>}
      </m.div>

      {!sid ? (
        <Card><EmptyState icon="brain" title="Start an investigation">Run the demonstration (an age check whose tests reach 100% line coverage yet miss the boundary at 18), analyse a finished run, or paste your own module and tests.</EmptyState>
          <div className="flex justify-center gap-2 pb-8"><Button variant="primary" icon="flask2" loading={creating} onClick={() => create({ demo: true })}>Run the demonstration</Button></div></Card>
      ) : !s ? (
        <Card><EmptyState icon="radar" title={error ? "Could not load the session" : "Loading session…"}>{error}</EmptyState></Card>
      ) : (
        <LayoutGroup id="workspace">
          <div className="flex min-w-0 flex-col gap-5 [&>*]:min-w-0">
            <Overview s={s} />
            <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] [&>*]:min-w-0">
              <Card>
                <CardHead title="Agent network" sub="signals follow real engine events" right={running ? <Badge tone="intel">{s.action?.replace("_", " ")}</Badge> : null} />
                <div className="p-4"><AgentNetwork states={net.states} signals={net.signals} /></div>
              </Card>
              <Card>
                <CardHead title="Behavioural blind spot heatmap" sub="functions × behaviour" right={cell ? <Button size="sm" icon="x" onClick={() => setCell(null)}>Clear cell</Button> : null} />
                <div className="p-4">
                  <Heatmap data={s.heatmap} selected={cell} pulses={pulses} match={cellMatch}
                    onSelect={(c) => { const k = `${c.function}|${c.category}`; setCell(cell === k ? null : k); const d = s.diagnoses.find((x) => c.blind_spots.includes(x.blind_spot_id)); if (d) setOpenId(d.blind_spot_id); }} />
                  <AnimatePresence>
                    {selCell && (
                      <m.div key={cell} initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} transition={spring.smooth} className="overflow-hidden">
                        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-panel-2/50 p-3 text-[12.5px]">
                          <m.span layoutId={`cell-${selCell.function}-${selCell.category}`} className="h-6 w-6 rounded-md" style={{ background: CELL_STYLE[selCell.status].color }} />
                          <span className="font-semibold">{selCell.function} · {SHORT[selCell.category]}</span>
                          <span className="text-muted">{CELL_STYLE[selCell.status].label}</span>
                          <span className="text-muted">mutants {selCell.mutants.map((x) => `#${x}`).join(", ")} · lines {selCell.lines.join(", ")} · {selCell.operators.join(", ")}</span>
                          <span className="text-muted">{selCell.killed} killed · {selCell.survived} survived{selCell.resolved ? ` · ${selCell.resolved} resolved` : ""}</span>
                        </div>
                      </m.div>
                    )}
                  </AnimatePresence>
                </div>
              </Card>
            </div>

            <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] [&>*]:min-w-0">
              <Card>
                <CardHead title="Source-code investigation" sub={selected ? `${selected.blind_spot_id} · line ${selected.source_line}` : "select a blind spot"} />
                <CodeInvestigation source={s.source} tests={s.tests} diagnosis={selected} executed={executed} scanLine={scanLine} />
              </Card>
              <Card>
                <CardHead title="AI diagnostic panel" sub={`${list.length} of ${s.diagnoses.length}`} />
                <div className="flex flex-wrap gap-2 border-b border-line px-4 py-2.5">
                  {([["fn", "Function", fns], ["sev", "Severity", ["high", "medium", "low"]], ["op", "Operator", ops],
                    ["cat", "Diagnosis", Object.keys(PRIMARY)], ["status", "Status", ["unresolved", "resolved"]]] as [keyof typeof filters, string, string[]][]).map(([k, label, opts]) => (
                    <select key={k} aria-label={label} className={clsx("input w-auto py-1 text-[12px]", filters[k] && "border-intel/60")} value={filters[k]}
                      onChange={(e) => setFilters((f) => ({ ...f, [k]: e.target.value }))}>
                      <option value="">{label}: all</option>{opts.map((o) => <option key={o} value={o}>{k === "cat" ? PRIMARY[o as Primary].label : o}</option>)}
                    </select>
                  ))}
                </div>
                <div className="max-h-[640px] overflow-auto p-3">
                  <DiagnosticList items={list} openId={openId} onOpen={setOpenId} flashes={flashes} />
                </div>
              </Card>
            </div>

            <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2 [&>*]:min-w-0">
              <Card><CardHead title="Counterexample discovery laboratory" sub="original vs mutant, in the sandbox" /><CounterexampleLab s={s} d={selected} /></Card>
              <Card><CardHead title="Autonomous resolution centre" sub={selected ? `selected: ${selected.blind_spot_id}` : "select a blind spot for single actions"} /><ResolutionCentre s={s} d={selected} onAction={refresh} /></Card>
            </div>

            <Card><CardHead title="Blind spot evolution timeline" sub="every step the engine took, with suite versions" /><EvolutionTimeline s={s} /></Card>
          </div>
        </LayoutGroup>
      )}
      <NewSessionModal open={custom} onClose={() => setCustom(false)} onCreate={create} />
    </m.div>
  );
}
