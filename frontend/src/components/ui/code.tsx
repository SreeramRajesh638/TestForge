/* Code editor (CodeMirror 5) and read-only highlighted code (highlight.js). */
import CodeMirror from "codemirror";
import "codemirror/mode/python/python";
import "codemirror/lib/codemirror.css";
import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import { memo, useEffect, useMemo, useRef } from "react";

hljs.registerLanguage("python", python);

export function CodeEditor({ value, onChange, onRun, fileName }: { value: string; onChange: (v: string) => void; onRun?: () => void; fileName: string }) {
  const host = useRef<HTMLDivElement>(null);
  const cm = useRef<CodeMirror.Editor | null>(null);
  const handlers = useRef({ onChange, onRun });
  handlers.current = { onChange, onRun };

  useEffect(() => {
    if (!host.current) return;
    const ed = CodeMirror(host.current, {
      value, mode: "python", theme: "tf", lineNumbers: true, indentUnit: 4, viewportMargin: 30,
      extraKeys: {
        "Ctrl-Enter": () => handlers.current.onRun?.(), "Cmd-Enter": () => handlers.current.onRun?.(),
        Tab: (c) => c.replaceSelection("    "),
      },
    });
    ed.on("change", (c) => handlers.current.onChange(c.getValue()));
    cm.current = ed;
    return () => { host.current?.replaceChildren(); cm.current = null; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { // external value changes (examples) without fighting the cursor
    const ed = cm.current;
    if (ed && ed.getValue() !== value) ed.setValue(value);
  }, [value]);

  const lines = value ? value.split("\n").length : 0;
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-bg/60">
      <div className="flex items-center gap-2 border-b border-line bg-panel-2/70 px-3 py-2 text-[12px] text-muted">
        <span className="flex gap-1.5"><i className="h-2.5 w-2.5 rounded-full bg-bad/70" /><i className="h-2.5 w-2.5 rounded-full bg-warn/70" /><i className="h-2.5 w-2.5 rounded-full bg-ok/70" /></span>
        <span className="font-mono text-ink-2">{fileName}</span>
        <span className="ml-auto tabular-nums">{lines} lines</span>
      </div>
      <div ref={host} className="cm-host" />
    </div>
  );
}

export const CodeView = memo(function CodeView({ code, maxHeight = 460 }: { code: string; maxHeight?: number }) {
  const html = useMemo(() => (code ? hljs.highlight(code, { language: "python" }).value : ""), [code]);
  return (
    <pre className="overflow-auto bg-bg/50 p-4 font-mono text-[12.5px] leading-relaxed" style={{ maxHeight }}>
      <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
    </pre>
  );
});
