/* Interactive primitives: Button (with optional magnetic pull), Switch,
 * Segmented control and Tabs (sliding indicators via shared layoutId). */
import { m, useMotionValue, useReducedMotion, useSpring } from "framer-motion";
import clsx from "clsx";
import { useId, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { spring } from "../../motion/variants";
import { Icon, type IconName } from "./Icon";

type Variant = "primary" | "ghost" | "subtle" | "danger";
const styles: Record<Variant, string> = {
  primary: "text-white bg-gradient-to-br from-accent to-accent-2 shadow-glow hover:brightness-110",
  ghost: "text-ink bg-panel-2 border border-line hover:border-line-2",
  subtle: "text-ink-2 hover:text-ink hover:bg-panel-2",
  danger: "text-bad bg-bad/10 border border-bad/30 hover:bg-bad/15",
};

interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onAnimationStart" | "onDrag" | "onDragStart" | "onDragEnd" | "style"> {
  variant?: Variant; icon?: IconName; loading?: boolean; magnetic?: boolean; size?: "sm" | "md" | "lg"; children?: ReactNode;
}

export function Button({ variant = "ghost", icon, loading, magnetic, size = "md", className, children, disabled, ...rest }: ButtonProps) {
  const ref = useRef<HTMLButtonElement>(null);
  const reduce = useReducedMotion();
  const x = useSpring(useMotionValue(0), spring.gentle);
  const y = useSpring(useMotionValue(0), spring.gentle);
  const pull = magnetic && !reduce && !disabled;

  function onMove(e: React.MouseEvent) {
    if (!pull || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    x.set((e.clientX - (r.left + r.width / 2)) * 0.18);
    y.set((e.clientY - (r.top + r.height / 2)) * 0.3);
  }
  const reset = () => { x.set(0); y.set(0); };

  return (
    <m.button ref={ref} {...rest} disabled={disabled || loading}
      onMouseMove={onMove} onMouseLeave={reset}
      style={pull ? { x, y } : undefined}
      whileHover={disabled ? undefined : { scale: size === "lg" ? 1.01 : 1.02 }}
      whileTap={disabled ? undefined : { scale: 0.97 }}
      transition={spring.snappy}
      className={clsx("relative inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-xl font-semibold outline-none transition-[filter,background-color,border-color,color] disabled:cursor-not-allowed disabled:opacity-55",
        size === "sm" && "px-2.5 py-1.5 text-[12.5px]", size === "md" && "px-3.5 py-2 text-[13.5px]", size === "lg" && "w-full px-5 py-3 text-[14.5px]",
        styles[variant], className)}>
      {loading ? <Spinner /> : icon ? <Icon name={icon} size={size === "sm" ? 14 : 16} /> : null}
      {children}
    </m.button>
  );
}

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <m.svg width={size} height={size} viewBox="0 0 24 24" animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 0.9, ease: "linear" }} aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity=".25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </m.svg>
  );
}

export function Switch({ checked, onChange, label, hint, tone = "accent" }: {
  checked: boolean; onChange: (v: boolean) => void; label: ReactNode; hint?: ReactNode; tone?: "accent" | "bad" | "intel";
}) {
  return (
    <label className="group flex cursor-pointer select-none items-center gap-3 py-1.5">
      <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={clsx("relative h-[22px] w-[38px] flex-none rounded-full p-[3px] transition-colors duration-200",
          checked ? (tone === "bad" ? "bg-bad" : tone === "intel" ? "bg-intel" : "bg-accent") : "bg-line-2")}>
        <m.span layout transition={spring.snappy} className={clsx("block h-4 w-4 rounded-full bg-white shadow", checked && "ml-auto")} />
      </button>
      <span className="min-w-0">
        <span className="block text-[13px] font-semibold text-ink">{label}</span>
        {hint && <span className="block text-[12px] text-muted">{hint}</span>}
      </span>
    </label>
  );
}

export function Segmented<T extends string>({ value, onChange, options, disabled, label }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode; dot?: "on" | "off" }[]; disabled?: boolean; label: string;
}) {
  const id = useId();
  return (
    <div role="radiogroup" aria-label={label} className={clsx("flex gap-1 rounded-xl border border-line bg-panel-2 p-1", disabled && "opacity-50")}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button key={o.value} type="button" role="radio" aria-checked={on} disabled={disabled} onClick={() => onChange(o.value)}
            className={clsx("relative flex flex-1 items-center justify-center gap-2 whitespace-nowrap rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-colors",
              on ? "text-ink" : "text-ink-2 hover:text-ink")}>
            {on && <m.span layoutId={`seg-${id}`} transition={spring.snappy} className="absolute inset-0 rounded-lg border border-line-2 bg-panel shadow-sm" />}
            {o.dot && <span className={clsx("relative h-2 w-2 rounded-full", o.dot === "on" ? "bg-ok shadow-[0_0_0_3px_rgb(var(--ok)/.18)]" : "bg-line-2")} />}
            <span className="relative">{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, tabs }: {
  value: T; onChange: (v: T) => void; tabs: { value: T; label: ReactNode; count?: number }[];
}) {
  const id = useId();
  return (
    <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-line px-3">
      {tabs.map((t) => {
        const on = t.value === value;
        return (
          <button key={t.value} role="tab" aria-selected={on} onClick={() => onChange(t.value)}
            className={clsx("relative flex items-center gap-2 whitespace-nowrap px-3 py-3 text-[13px] font-semibold transition-colors", on ? "text-ink" : "text-muted hover:text-ink-2")}>
            {t.label}
            {t.count != null && (
              <span className="rounded-full border border-line bg-panel-2 px-1.5 text-[11px] tabular-nums text-ink-2">{t.count}</span>
            )}
            {on && <m.span layoutId={`tab-${id}`} transition={spring.snappy} className="absolute inset-x-2 -bottom-px h-[2px] rounded-full bg-gradient-to-r from-accent to-accent-2" />}
          </button>
        );
      })}
    </div>
  );
}

export function Badge({ tone = "muted", children, className }: { tone?: "ok" | "bad" | "warn" | "accent" | "muted" | "info" | "intel"; children: ReactNode; className?: string }) {
  const t = {
    ok: "bg-ok/10 text-ok", bad: "bg-bad/10 text-bad", warn: "bg-warn/15 text-warn", accent: "bg-accent/15 text-accent",
    info: "bg-info/10 text-info", muted: "border border-line bg-panel-2 text-ink-2", intel: "bg-intel/15 text-intel",
  }[tone];
  return <span className={clsx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11.5px] font-bold tracking-wide", t, className)}>{children}</span>;
}

export function RangeField({ label, value, display, min, max, step = 1, onChange }: {
  label: string; value: number; display: string; min: number; max: number; step?: number; onChange: (v: number) => void;
}) {
  return (
    <label className="flex flex-col gap-2">
      <span className="flex items-center justify-between"><span className="label">{label}</span>
        <m.span key={display} initial={{ y: -4, opacity: 0 }} animate={{ y: 0, opacity: 1 }} className="text-[13px] font-bold tabular-nums">{display}</m.span></span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[rgb(var(--accent))]" />
    </label>
  );
}
