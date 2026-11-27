/* App shell: sidebar navigation (sliding active indicator), live backend
 * status, theme toggle, mobile drawer, ambient background and toasts. */
import { AnimatePresence, LayoutGroup, m } from "framer-motion";
import clsx from "clsx";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { bsApi } from "../../lib/blindspot";
import { useApp } from "../../lib/store";
import { spring } from "../../motion/variants";
import { Icon, type IconName } from "../ui/Icon";
import { Toaster } from "../ui/display";
import { AmbientBackground } from "./AmbientBackground";

const NAV: { to: string; label: string; icon: IconName; hint: string; intel?: boolean }[] = [
  { to: "/", label: "Generate tests", icon: "code", hint: "AI agents + mutation testing" },
  { to: "/blindspot", label: "Blind Spot Intelligence", icon: "brain", hint: "Why tests miss faults", intel: true },
  { to: "/automation", label: "UI automation", icon: "robot", hint: "Playwright scenarios" },
  { to: "/monitor", label: "System monitor", icon: "activity", hint: "Live client ↔ server flow" },
  { to: "/experiments", label: "Experiments", icon: "chart", hint: "Thesis results" },
  { to: "/runs", label: "Runs", icon: "history", hint: "History, compare, replay" },
];

function Brand() {
  const { status } = useApp();
  return (
    <div className="flex items-center gap-3 px-2 pb-5 pt-1">
      <m.div whileHover={{ rotate: -8, scale: 1.06 }} transition={spring.bouncy}
        className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-accent to-accent-2 text-[17px] font-black text-white shadow-glow">T</m.div>
      <div className="leading-tight">
        <div className="flex items-center gap-2 text-[15.5px] font-bold tracking-tight">TestForge
          {status?.version && <span className="rounded-md border border-line px-1.5 py-px font-mono text-[10.5px] font-semibold text-muted">v{status.version}</span>}</div>
        <div className="text-[11.5px] text-muted">Multi-agent test generation</div>
      </div>
    </div>
  );
}

function BackendStatus() {
  const { status } = useApp();
  const names = { mock: "Mock (no AI)", groq: "Groq", ollama: "Ollama", claude: "Claude" } as const;
  return (
    <div className="rounded-xl border border-line bg-panel/70 p-3">
      <div className="eyebrow mb-2">AI backends</div>
      {!status ? <div className="text-[12px] text-muted">checking…</div> :
        (Object.keys(names) as (keyof typeof names)[]).map((k) => (
          <div key={k} className="flex items-center gap-2 py-0.5 text-[12.5px]" title={status[k].note}>
            <span className="relative flex h-2 w-2">
              {status[k].ready && <m.span className="absolute inset-0 rounded-full bg-ok" animate={{ scale: [1, 2.2], opacity: [0.5, 0] }} transition={{ repeat: Infinity, duration: 2.2 }} />}
              <span className={clsx("relative h-2 w-2 rounded-full", status[k].ready ? "bg-ok" : "bg-line-2")} />
            </span>
            <span className="text-ink-2">{names[k]}</span>
            <span className="ml-auto max-w-[110px] truncate text-[11.5px] text-muted">{status[k].note}</span>
          </div>
        ))}
    </div>
  );
}

function ThemeToggle() {
  const { theme, toggleTheme } = useApp();
  return (
    <button onClick={toggleTheme} className="flex w-full items-center justify-center gap-2 rounded-xl border border-line bg-panel/70 py-2 text-[13px] font-semibold text-ink-2 transition hover:text-ink">
      <AnimatePresence mode="wait" initial={false}>
        <m.span key={theme} initial={{ rotate: -90, opacity: 0, scale: 0.6 }} animate={{ rotate: 0, opacity: 1, scale: 1 }} exit={{ rotate: 90, opacity: 0, scale: 0.6 }} transition={spring.snappy}>
          <Icon name={theme === "dark" ? "sun" : "moon"} />
        </m.span>
      </AnimatePresence>
      {theme === "dark" ? "Light mode" : "Dark mode"}
    </button>
  );
}

/** Unresolved blind spots of the latest session/run (polled, and pushed by the workspace). */
function useUnresolved(): number | null {
  const [n, setN] = useState<number | null>(null);
  useEffect(() => {
    const load = () => bsApi.overview().then((o) => setN(o.summary ? o.summary.unresolved : null)).catch(() => undefined);
    load();
    const t = window.setInterval(load, 20000);
    const onPush = (e: Event) => setN((e as CustomEvent).detail.unresolved);
    window.addEventListener("tf:blindspot", onPush);
    return () => { window.clearInterval(t); window.removeEventListener("tf:blindspot", onPush); };
  }, []);
  return n;
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const unresolved = useUnresolved();
  return (
    <div className="flex h-full flex-col gap-1 p-4">
      <Brand />
      <div className="eyebrow px-2 pb-1.5 pt-2">Workspace</div>
      <LayoutGroup id="sidebar">
        <nav className="flex flex-col gap-0.5">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} onClick={onNavigate}
              className={({ isActive }) => clsx("group relative flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors", isActive ? "text-ink" : "text-ink-2 hover:text-ink")}>
              {({ isActive }) => (
                <>
                  {isActive && <m.span layoutId="nav-active" transition={spring.snappy}
                    className={clsx("absolute inset-0 rounded-xl border", n.intel ? "border-intel/30 bg-intel/10 shadow-[inset_2px_0_0_rgb(var(--intel))]" : "border-accent/25 bg-accent/10 shadow-[inset_2px_0_0_rgb(var(--accent))]")} />}
                  <Icon name={n.icon} size={18} className={clsx("relative", n.intel && "text-intel")} />
                  <span className="relative min-w-0 flex-1">
                    <span className="block text-[13.5px] font-semibold">{n.label}</span>
                    <span className="block truncate text-[11px] text-muted">{n.hint}</span>
                  </span>
                  {n.intel && unresolved != null && (
                    <AnimatePresence mode="popLayout" initial={false}>
                      <m.span key={unresolved} title={`${unresolved} unresolved blind spot(s) in the latest session`}
                        initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.4, opacity: 0 }} transition={spring.bouncy}
                        className={clsx("relative min-w-[22px] rounded-full px-1.5 text-center text-[11px] font-bold tabular-nums",
                          unresolved > 0 ? "bg-bad/15 text-bad" : "bg-ok/15 text-ok")}>{unresolved}</m.span>
                    </AnimatePresence>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>
      </LayoutGroup>
      <div className="eyebrow px-2 pb-1.5 pt-4">Resources</div>
      <a href="/demo" target="_blank" rel="noopener" className="flex items-center gap-3 rounded-xl px-3 py-2 text-[13px] text-ink-2 hover:bg-panel-2 hover:text-ink">
        <Icon name="shop" size={17} /> ShopLite demo <Icon name="external" size={12} className="ml-auto opacity-60" /></a>
      <a href="https://github.com/SreeramRajesh638/testforge#readme" target="_blank" rel="noopener" className="flex items-center gap-3 rounded-xl px-3 py-2 text-[13px] text-ink-2 hover:bg-panel-2 hover:text-ink">
        <Icon name="book" size={17} /> Documentation <Icon name="external" size={12} className="ml-auto opacity-60" /></a>
      <div className="mt-auto flex flex-col gap-2.5 pt-4">
        <BackendStatus />
        <ThemeToggle />
      </div>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[264px_minmax(0,1fr)]">
      <AmbientBackground />
      <aside className="sticky top-0 hidden h-screen border-r border-line bg-bg-2/70 backdrop-blur-xl lg:block">
        <SidebarContent />
      </aside>
      <div className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-bg-2/80 px-4 py-2.5 backdrop-blur-xl lg:hidden">
        <button onClick={() => setOpen(true)} aria-label="Open menu" className="rounded-lg border border-line bg-panel-2 p-2"><Icon name="menu" /></button>
        <span className="font-bold">TestForge</span>
      </div>
      <AnimatePresence>
        {open && (
          <>
            <m.div className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setOpen(false)} />
            <m.aside className="fixed inset-y-0 left-0 z-50 w-[280px] border-r border-line bg-bg-2 lg:hidden"
              initial={{ x: "-100%" }} animate={{ x: 0 }} exit={{ x: "-100%" }} transition={spring.smooth}
              drag="x" dragConstraints={{ left: -280, right: 0 }} dragElastic={0.05}
              onDragEnd={(_, info) => { if (info.offset.x < -80) setOpen(false); }}>
              <SidebarContent onNavigate={() => setOpen(false)} />
            </m.aside>
          </>
        )}
      </AnimatePresence>
      <main className="min-w-0 px-4 pb-14 pt-5 sm:px-7 lg:pt-7">{children}</main>
      <Toaster />
    </div>
  );
}

export function PageHeader({ eyebrow, title, subtitle, right }: { eyebrow: string; title: string; subtitle: string; right?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <m.div variants={{ hidden: { opacity: 0, y: 6 }, show: { opacity: 1, y: 0 } }} className="eyebrow mb-1.5 text-accent">{eyebrow}</m.div>
        <m.h1 variants={{ hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0, transition: spring.smooth } }}
          className="text-gradient text-[26px] font-bold tracking-tight sm:text-[30px]">{title}</m.h1>
        <m.p variants={{ hidden: { opacity: 0 }, show: { opacity: 1, transition: { delay: 0.08 } } }} className="mt-1 max-w-2xl text-ink-2">{subtitle}</m.p>
      </div>
      {right && <m.div variants={{ hidden: { opacity: 0, x: 10 }, show: { opacity: 1, x: 0 } }} className="flex items-center gap-2">{right}</m.div>}
    </div>
  );
}
