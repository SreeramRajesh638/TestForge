/* Dashboard card: Blind Spot Intelligence metrics of the current run (live,
 * from its events and final report), else of the latest session or run. */
import { m } from "framer-motion";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { agentStates, bsApi, fmtPct, type Heatmap as HM, type Overview, type Summary } from "../../lib/blindspot";
import type { RunView } from "../../lib/run";
import { rise, spring } from "../../motion/variants";
import { Button } from "../ui/controls";
import { AnimatedNumber, Card, CardHead } from "../ui/display";
import { AgentNetwork } from "./AgentNetwork";
import { Heatmap } from "./Heatmap";

export function BlindSpotOverviewCard({ view, runId, running }: { view: RunView; runId: string | null; running: boolean }) {
  const navigate = useNavigate();
  const [latest, setLatest] = useState<Overview | null>(null);
  useEffect(() => { bsApi.overview().then(setLatest).catch(() => setLatest(null)); }, [view.finished]);
  const report = view.report as unknown as { blind_spot?: { summary: Summary; heatmap: HM } } | null;
  const fromRun = report?.blind_spot;
  const live = view.bs.enabled && !fromRun;
  const sm: Partial<Summary> | undefined = fromRun?.summary ?? (live ? { discovered: view.bs.discovered, resolved: view.bs.resolved, unresolved: view.bs.discovered - view.bs.resolved } : latest?.summary);
  const heat = fromRun?.heatmap ?? (live ? undefined : latest?.heatmap);
  const net = agentStates(view.bs.events, running);
  const source = fromRun || live ? "this run" : latest?.source ? `latest ${latest.source}: ${latest.module ?? ""}` : null;
  const explore = () => {
    if (runId && view.finished && (fromRun || view.bs.enabled)) navigate(`/blindspot?run=${runId}`);
    else if (latest?.source === "session" && latest.id) navigate(`/blindspot?session=${latest.id}`);
    else navigate("/blindspot");
  };
  const tiles: [string, number | null | undefined, string][] = [
    ["Discovered", sm?.discovered, "text-intel"], ["Unresolved", sm?.unresolved, "text-bad"], ["Resolved", sm?.resolved, "text-ok"],
    ["High priority", sm?.high_priority, "text-warn"], ["Verified tests", sm?.verified_corrective_tests, "text-ok"],
  ];
  return (
    <Card className="overflow-hidden">
      <CardHead title={<span className="flex items-center gap-2"><span className="text-intel">🧠</span>Blind Spot Intelligence</span>} sub={source ?? "no analysis yet"}
        right={<Button size="sm" icon="radar" className="border-intel/40 text-intel" onClick={explore}>Explore Blind Spots</Button>} />
      <div className="grid gap-4 p-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" style={{ background: "linear-gradient(160deg, rgb(var(--intel)/.08), transparent 60%)" }}>
        <div>
          <div className="grid grid-cols-3 gap-2">
            {tiles.map(([k, v, tone]) => (
              <m.div key={k} variants={rise} className="rounded-xl border border-line bg-panel/60 p-2.5">
                <div className="text-[11px] text-muted">{k}</div>
                <div className={`text-[20px] font-bold ${tone}`}><AnimatedNumber value={v ?? null} /></div>
              </m.div>
            ))}
            <m.div variants={rise} className="rounded-xl border border-line bg-panel/60 p-2.5">
              <div className="text-[11px] text-muted">Resolution rate</div>
              <div className="text-[20px] font-bold">{fmtPct(sm?.resolution_rate ?? (sm?.discovered ? (sm.resolved ?? 0) / sm.discovered : null))}</div>
            </m.div>
          </div>
          <div className="mt-2 flex items-center gap-2 rounded-xl border border-line bg-panel/60 px-3 py-2 text-[12.5px]">
            <span className="text-muted">Mutation score</span>
            <b>{fmtPct(sm?.score_before)}</b><span className="text-muted">→</span><b className="text-ok">{fmtPct(sm?.score_after)}</b>
            {sm?.score_before != null && sm?.score_after != null && <m.span initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={spring.smooth} className="ml-auto text-ok">+{Math.round((sm.score_after - sm.score_before) * 100)} pts</m.span>}
          </div>
        </div>
        <div className="min-w-0">
          {live || (running && view.bs.enabled) ? <AgentNetwork compact states={net.states} signals={net.signals} />
            : <Heatmap data={heat} compact onSelect={() => explore()} />}
        </div>
      </div>
    </Card>
  );
}
