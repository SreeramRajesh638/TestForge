/* Subtle depth behind the UI: a masked grid, two slow light orbs with scroll
 * parallax, and a few drifting particles. Static when reduced motion is on.
 * Only transform/opacity animate (GPU-friendly); it never captures input. */
import { m, useReducedMotion, useScroll, useTransform } from "framer-motion";
import { memo, useMemo } from "react";

function AmbientBackgroundImpl() {
  const reduce = useReducedMotion();
  const { scrollY } = useScroll();
  const y1 = useTransform(scrollY, [0, 1200], [0, -160]);
  const y2 = useTransform(scrollY, [0, 1200], [0, -60]);
  const particles = useMemo(() => Array.from({ length: 14 }, (_, i) => ({
    left: (i * 37 + 11) % 100, top: (i * 53 + 7) % 100, size: 2 + (i % 3), delay: (i % 7) * 0.9, dur: 9 + (i % 5) * 2,
  })), []);

  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute inset-0"
        style={{
          backgroundImage: "linear-gradient(rgb(var(--grid) / var(--grid-alpha)) 1px, transparent 1px), linear-gradient(90deg, rgb(var(--grid) / var(--grid-alpha)) 1px, transparent 1px)",
          backgroundSize: "44px 44px",
          maskImage: "radial-gradient(ellipse 80% 60% at 50% 0%, #000 30%, transparent 75%)",
          WebkitMaskImage: "radial-gradient(ellipse 80% 60% at 50% 0%, #000 30%, transparent 75%)",
        }} />
      <m.div style={reduce ? undefined : { y: y1 }} className="absolute -top-40 left-[12%] h-[520px] w-[520px]">
        <m.div className="h-full w-full rounded-full bg-accent/20 blur-[120px]"
          animate={reduce ? undefined : { x: [0, 60, -20, 0], scale: [1, 1.08, 0.96, 1] }}
          transition={{ duration: 26, repeat: Infinity, ease: "easeInOut" }} />
      </m.div>
      <m.div style={reduce ? undefined : { y: y2 }} className="absolute -top-24 right-[6%] h-[460px] w-[460px]">
        <m.div className="h-full w-full rounded-full bg-accent-2/[.16] blur-[120px]"
          animate={reduce ? undefined : { x: [0, -50, 30, 0], scale: [1, 0.94, 1.06, 1] }}
          transition={{ duration: 30, repeat: Infinity, ease: "easeInOut" }} />
      </m.div>
      {!reduce && particles.map((p, i) => (
        <m.span key={i} className="absolute rounded-full bg-accent/40"
          style={{ left: `${p.left}%`, top: `${p.top}%`, width: p.size, height: p.size }}
          animate={{ y: [0, -28, 0], opacity: [0, 0.8, 0] }}
          transition={{ duration: p.dur, delay: p.delay, repeat: Infinity, ease: "easeInOut" }} />
      ))}
    </div>
  );
}

export const AmbientBackground = memo(AmbientBackgroundImpl);
