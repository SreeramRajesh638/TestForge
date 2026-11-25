/* Display primitives: cards (with optional 3D tilt), animated numbers, gauge,
 * skeletons, tooltip, modal, toasts, collapse and text reveal. */
import { AnimatePresence, m, useInView, useMotionValue, useReducedMotion, useSpring, useTransform } from "framer-motion";
import clsx from "clsx";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { collapse, pop, rise, spring } from "../../motion/variants";
import { useDraw } from "../../motion/useDraw";
import { useApp } from "../../lib/store";
import { Icon } from "./Icon";

export function Card({ children, className, tilt, ...rest }: { children: ReactNode; className?: string; tilt?: boolean } & React.HTMLAttributes<HTMLDivElement>) {
  if (tilt) return <TiltCard className={className}>{children}</TiltCard>;
  return <m.section variants={rise} className={clsx("card", className)} {...(rest as object)}>{children}</m.section>;
}

export function CardHead({ step, title, sub, right }: { step?: number; title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line px-4 py-3.5">
      {step != null && <span className="grid h-6 w-6 place-items-center rounded-lg bg-accent/15 text-[12px] font-bold text-accent">{step}</span>}
      <h2 className="text-[14px] font-semibold">{title}</h2>
      {sub && <span className="text-[12.5px] text-muted">{sub}</span>}
      {right && <div className="ml-auto flex flex-wrap items-center justify-end gap-2">{right}</div>}
    </div>
  );
}

/** Card that tilts toward the cursor in 3D (disabled for reduced motion). */
export function TiltCard({ children, className, max = 6 }: { children: ReactNode; className?: string; max?: number }) {
  const reduce = useReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const px = useMotionValue(0.5), py = useMotionValue(0.5);
  const rx = useSpring(useTransform(py, [0, 1], [max, -max]), spring.gentle);
  const ry = useSpring(useTransform(px, [0, 1], [-max, max]), spring.gentle);
  const glowX = useTransform(px, (v) => `${v * 100}%`), glowY = useTransform(py, (v) => `${v * 100}%`);
  const glow = useTransform([glowX, glowY], ([x, y]) => `radial-gradient(420px circle at ${x} ${y}, rgb(var(--accent) / .10), transparent 45%)`);
  function onMove(e: React.MouseEvent) {
    if (reduce || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    px.set((e.clientX - r.left) / r.width); py.set((e.clientY - r.top) / r.height);
  }
  return (
    <m.div ref={ref} variants={rise} onMouseMove={onMove} onMouseLeave={() => { px.set(0.5); py.set(0.5); }}
      style={reduce ? undefined : { rotateX: rx, rotateY: ry, transformPerspective: 900 }}
      className={clsx("card overflow-hidden [transform-style:preserve-3d]", className)}>
      {!reduce && <m.div aria-hidden className="pointer-events-none absolute inset-0" style={{ background: glow }} />}
      <div className="relative">{children}</div>
    </m.div>
  );
}

/** Number that springs to its new value whenever it changes. */
export function AnimatedNumber({ value, format = (v) => Math.round(v).toString(), className }: {
  value: number | null | undefined; format?: (v: number) => string; className?: string;
}) {
  const reduce = useReducedMotion();
  const mv = useSpring(0, { stiffness: 90, damping: 20 });
  const text = useTransform(mv, (v) => format(v));
  useEffect(() => { if (value != null) { if (reduce) mv.jump(value); else mv.set(value); } }, [value, reduce, mv]);
  if (value == null) return <span className={className}>–</span>;
  return <m.span className={clsx("tabular-nums", className)}>{text}</m.span>;
}

/** Circular score gauge: the arc draws itself (SVG pathLength). */
export function Gauge({ value, size = 84, stroke = 8, tone = "accent" }: { value: number | null; size?: number; stroke?: number; tone?: "accent" | "ok" }) {
  const id = "g" + useId().replace(/[^a-zA-Z0-9_-]/g, "");  // ":r1:" breaks url(#…) references
  const r = (size - stroke) / 2;
  const arc = useDraw(Math.max(0.001, value ?? 0), { type: "spring", stiffness: 60, damping: 18 });
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="flex-none -rotate-90" aria-hidden="true">
      <defs><linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" style={{ stopColor: tone === "ok" ? "rgb(var(--ok))" : "rgb(var(--accent))" }} />
        <stop offset="1" style={{ stopColor: "rgb(var(--accent-2))" }} /></linearGradient></defs>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(var(--line))" strokeWidth={stroke} />
      <m.circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={`url(#${id})`} strokeWidth={stroke} strokeLinecap="round"
        opacity={value == null ? 0 : 1} style={{ pathLength: arc }} />
    </svg>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={clsx("relative overflow-hidden rounded-lg bg-panel-2", className)}>
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/[.06] to-transparent" />
    </div>
  );
}

export function Tooltip({ text, children }: { text: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}>
      {children}
      <AnimatePresence>
        {open && (
          <m.span role="tooltip" initial={{ opacity: 0, y: 4, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 2 }}
            transition={spring.snappy}
            className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-max max-w-[260px] -translate-x-1/2 rounded-lg border border-line-2 bg-panel px-2.5 py-1.5 text-[12px] font-medium leading-snug text-ink-2 shadow-card">
            {text}
          </m.span>
        )}
      </AnimatePresence>
    </span>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; wide?: boolean }) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    window.setTimeout(() => panel.current?.focus(), 30);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [open, onClose]);
  return createPortal(
    <AnimatePresence>
      {open && (
        <m.div className="fixed inset-0 z-[70] grid place-items-center p-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <m.div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
          <m.div ref={panel} tabIndex={-1} role="dialog" aria-modal="true" variants={pop} initial="hidden" animate="show" exit="exit"
            className={clsx("card relative max-h-[88vh] w-full overflow-auto bg-panel outline-none", wide ? "max-w-5xl" : "max-w-xl")}>
            <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-line bg-panel/95 px-5 py-3.5 backdrop-blur">
              <h3 className="text-[15px] font-semibold">{title}</h3>
              <button onClick={onClose} aria-label="Close" className="ml-auto rounded-lg p-1.5 text-muted hover:bg-panel-2 hover:text-ink"><Icon name="x" /></button>
            </div>
            <div className="p-5">{children}</div>
          </m.div>
        </m.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

export function Toaster() {
  const { toasts, dismiss } = useApp();
  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-[80] flex w-[min(380px,calc(100vw-40px))] flex-col gap-2">
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <m.div key={t.id} layout initial={{ opacity: 0, y: 16, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 40, transition: { duration: 0.2 } }} transition={spring.snappy}
            className={clsx("card pointer-events-auto flex items-start gap-3 bg-panel px-4 py-3 text-[13px]",
              t.kind === "bad" && "border-bad/40", t.kind === "ok" && "border-ok/40")}>
            <Icon name={t.kind === "bad" ? "alert" : t.kind === "ok" ? "check" : "sparkles"}
              className={clsx("mt-0.5 flex-none", t.kind === "bad" ? "text-bad" : t.kind === "ok" ? "text-ok" : "text-accent")} />
            <span className="min-w-0 flex-1 break-words text-ink-2">{t.text}</span>
            <button onClick={() => dismiss(t.id)} aria-label="Dismiss" className="text-muted hover:text-ink"><Icon name="x" size={14} /></button>
          </m.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function Collapse({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <AnimatePresence initial={false}>
      {open && (
        <m.div key="c" variants={collapse} initial="closed" animate="open" exit="closed" className="overflow-hidden">
          {children}
        </m.div>
      )}
    </AnimatePresence>
  );
}

/** Words fade up one after another the first time the text scrolls into view. */
export function TextReveal({ text, className }: { text: string; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-40px" });
  return (
    <span ref={ref} className={className} aria-label={text}>
      {text.split(" ").map((w, i) => (
        <m.span key={i} aria-hidden className="inline-block whitespace-pre"
          initial={{ opacity: 0, y: "0.4em", filter: "blur(4px)" }}
          animate={inView ? { opacity: 1, y: 0, filter: "blur(0px)" } : undefined}
          transition={{ delay: i * 0.045, duration: 0.5, ease: [0.16, 1, 0.3, 1] }}>
          {w + " "}
        </m.span>
      ))}
    </span>
  );
}

export function EmptyState({ icon, title, children }: { icon: React.ComponentProps<typeof Icon>["name"]; title: string; children?: ReactNode }) {
  return (
    <m.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col items-center gap-2 px-6 py-10 text-center">
      <m.div animate={{ y: [0, -5, 0] }} transition={{ repeat: Infinity, duration: 3.2, ease: "easeInOut" }}
        className="grid h-12 w-12 place-items-center rounded-2xl border border-line bg-panel-2 text-accent">
        <Icon name={icon} size={22} />
      </m.div>
      <p className="font-semibold">{title}</p>
      {children && <p className="max-w-sm text-[13px] text-muted">{children}</p>}
    </m.div>
  );
}
