/* Side-by-side source investigation: original vs mutant (with the mutation
 * annotated on its line), existing tests (weak assertions highlighted) and the
 * corrective test. A scan line sweeps the line under investigation while the
 * engine is actually investigating it. */
import { AnimatePresence, m, useReducedMotion } from "framer-motion";
import clsx from "clsx";
import hljs from "highlight.js/lib/core";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { Diagnosis } from "../../lib/blindspot";
import { Tabs } from "../ui/controls";

function highlightLines(code: string): string[] {
  return code.split("\n").map((line) => (line ? hljs.highlight(line, { language: "python", ignoreIllegals: true }).value : " "));
}

const Lines = memo(function Lines({ code, mark, markTone = "bad", gutter, annotate, scanLine, weak, maxHeight = 380 }: {
  code: string; mark?: number | null; markTone?: "bad" | "intel" | "ok"; gutter?: Set<number>; annotate?: Record<number, string>;
  scanLine?: number | null; weak?: string[]; maxHeight?: number;
}) {
  const reduce = useReducedMotion();
  const html = useMemo(() => highlightLines(code), [code]);
  const raw = useMemo(() => code.split("\n"), [code]);
  const box = useRef<HTMLDivElement>(null);
  const target = scanLine ?? mark;
  useEffect(() => {
    if (!target || !box.current) return;
    const el = box.current.querySelector<HTMLElement>(`[data-line="${target}"]`);
    if (el) box.current.scrollTo({ top: Math.max(0, el.offsetTop - 80), behavior: reduce ? "auto" : "smooth" });
  }, [target, reduce]);
  return (
    <div ref={box} className="relative overflow-auto bg-bg/50 py-2 font-mono text-[12.5px] leading-[1.6]" style={{ maxHeight }}>
      {html.map((h, i) => {
        const n = i + 1;
        const isMark = n === mark;
        const isWeak = weak?.some((w) => w && raw[i].includes(w));
        return (
          <div key={n} data-line={n} className={clsx("relative flex min-w-max pr-4",
            isMark && (markTone === "bad" ? "bg-bad/12" : markTone === "intel" ? "bg-intel/15" : "bg-ok/12"), isWeak && "bg-warn/12")}>
            <span className="sticky left-0 z-[1] flex w-12 flex-none select-none items-center justify-end gap-1.5 bg-bg/80 pr-3 text-right text-muted/70">
              {gutter && <i className={clsx("h-1.5 w-1.5 rounded-full", gutter.has(n) ? "bg-ok/80" : "bg-transparent")} title={gutter.has(n) ? "executed by the tests" : undefined} />}
              {n}
            </span>
            <code className="hljs whitespace-pre" dangerouslySetInnerHTML={{ __html: h }} />
            {annotate?.[n] && <span className="ml-4 whitespace-pre rounded bg-intel/15 px-1.5 text-[11px] text-intel">{annotate[n]}</span>}
            {isWeak && <span className="ml-3 rounded bg-warn/15 px-1.5 text-[11px] text-warn">weak assertion</span>}
            {scanLine === n && !reduce && (
              <m.span key={`scan-${n}`} aria-hidden className="pointer-events-none absolute inset-y-0 left-12 right-0"
                style={{ background: "linear-gradient(90deg, transparent, rgb(var(--intel) / .35), transparent)" }}
                initial={{ x: "-60%", opacity: 0 }} animate={{ x: ["-60%", "100%"], opacity: [0, 1, 0] }}
                transition={{ duration: 1.4, repeat: 3, ease: "easeInOut" }} />
            )}
          </div>
        );
      })}
    </div>
  );
});

/** The mutant shown on the ORIGINAL text, so lines align (the engine's mutated
 * module is re-generated code with different formatting). */
export function mutantText(source: string, d: { source_line: number; original_code: string; mutated_code: string }): { text: string; exact: boolean } {
  const lines = source.split("\n");
  const i = d.source_line - 1;
  if (i < 0 || i >= lines.length) return { text: source, exact: false };
  const variants = [d.original_code, d.original_code.replace(/\s+/g, " "), d.original_code.replace(/'/g, '"')];
  for (const v of variants) {
    const at = lines[i].indexOf(v);
    if (at >= 0) {
      lines[i] = lines[i].slice(0, at) + d.mutated_code + lines[i].slice(at + v.length);
      return { text: lines.join("\n"), exact: true };
    }
  }
  return { text: source, exact: false };
}

export function CodeInvestigation({ source, tests, diagnosis, executed, scanLine }: {
  source: string; tests: string; diagnosis: Diagnosis | null; executed?: Set<number>; scanLine?: number | null;
}) {
  const [tab, setTab] = useState<"diff" | "tests" | "corrective">("diff");
  const mt = diagnosis ? mutantText(source, diagnosis) : null;
  const weak = diagnosis ? Object.values(diagnosis.evidence.assertions).flatMap((a) => a.weak) : [];
  const reachingTests = new Set(diagnosis?.evidence.reaching_tests ?? []);
  const testLines = useMemo(() => {
    const marks: number[] = [];
    tests.split("\n").forEach((l, i) => { const mm = /def (test\w+)/.exec(l); if (mm && reachingTests.has(mm[1])) marks.push(i + 1); });
    return marks;
  }, [tests, diagnosis?.blind_spot_id]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="min-w-0">
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: "diff", label: "Original ⟷ Mutant" },
        { value: "tests", label: "Existing tests", count: (tests.match(/^\s*def test/gm) || []).length },
        { value: "corrective", label: "Corrective test" },
      ]} />
      <AnimatePresence mode="wait" initial={false}>
        <m.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.18 }}>
          {tab === "diff" && (
            <div className="grid grid-cols-1 divide-line md:grid-cols-2 md:divide-x [&>*]:min-w-0">
              <div>
                <div className="flex items-center gap-2 border-b border-line px-3 py-1.5 text-[11.5px] text-muted">original
                  {executed && <span className="ml-auto flex items-center gap-1"><i className="h-1.5 w-1.5 rounded-full bg-ok/80" />executed by tests</span>}</div>
                <Lines code={source} mark={diagnosis?.source_line} gutter={executed} scanLine={scanLine} />
              </div>
              <div>
                <div className="border-b border-line px-3 py-1.5 text-[11.5px] text-muted">mutant {diagnosis && <span className="font-mono text-intel">#{diagnosis.mutant_id} · {diagnosis.mutation_operator}</span>}</div>
                <Lines code={mt?.text ?? source} mark={diagnosis?.source_line} markTone="intel"
                  annotate={diagnosis && !mt?.exact ? { [diagnosis.source_line]: `mutated: ${diagnosis.original_code} → ${diagnosis.mutated_code}` } : diagnosis ? { [diagnosis.source_line]: `${diagnosis.original_code} → ${diagnosis.mutated_code}` } : undefined} />
              </div>
            </div>
          )}
          {tab === "tests" && <Lines code={tests} weak={weak} mark={testLines[0] ?? null} markTone="ok" maxHeight={420} />}
          {tab === "corrective" && (diagnosis?.proposed_test
            ? <Lines code={diagnosis.proposed_test} markTone="ok" maxHeight={420} />
            : <p className="px-4 py-8 text-center text-[13px] text-muted">{diagnosis ? "No corrective test yet. Generate one in the Resolution Centre." : "Select a blind spot."}</p>)}
        </m.div>
      </AnimatePresence>
    </div>
  );
}
