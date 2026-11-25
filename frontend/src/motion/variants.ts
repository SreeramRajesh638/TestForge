/* Centralised motion language. Springs give physical, interruptible motion;
 * every entrance uses the same distances and timings so the UI feels coherent. */
import type { Transition, Variants } from "framer-motion";

export const spring = {
  snappy: { type: "spring", stiffness: 520, damping: 38, mass: 0.8 } satisfies Transition,
  smooth: { type: "spring", stiffness: 260, damping: 30 } satisfies Transition,
  gentle: { type: "spring", stiffness: 140, damping: 22 } satisfies Transition,
  bouncy: { type: "spring", stiffness: 420, damping: 18 } satisfies Transition,
};

export const ease = {
  out: [0.16, 1, 0.3, 1] as [number, number, number, number],
  inOut: [0.65, 0, 0.35, 1] as [number, number, number, number],
};

/** Page enter/exit (used with AnimatePresence in the router). */
export const page: Variants = {
  initial: { opacity: 0, y: 14, filter: "blur(6px)" },
  enter: { opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.45, ease: ease.out, staggerChildren: 0.06 } },
  exit: { opacity: 0, y: -8, filter: "blur(4px)", transition: { duration: 0.2, ease: ease.inOut } },
};

/** Parent that staggers its children in. */
export const stagger = (gap = 0.06, delay = 0): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: gap, delayChildren: delay } },
});

/** Child entrance: rise + fade. */
export const rise: Variants = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: spring.smooth },
};

export const fade: Variants = {
  hidden: { opacity: 0 },
  show: { opacity: 1, transition: { duration: 0.35, ease: ease.out } },
};

export const pop: Variants = {
  hidden: { opacity: 0, scale: 0.94 },
  show: { opacity: 1, scale: 1, transition: spring.snappy },
  exit: { opacity: 0, scale: 0.96, transition: { duration: 0.15 } },
};

/** List rows entering from the side (activity feeds). */
export const slideIn: Variants = {
  hidden: { opacity: 0, x: -12, height: 0 },
  show: { opacity: 1, x: 0, height: "auto", transition: { ...spring.smooth, opacity: { duration: 0.25 } } },
};

/** Expand/collapse for accordions and panels. */
export const collapse: Variants = {
  closed: { height: 0, opacity: 0, transition: { duration: 0.25, ease: ease.inOut } },
  open: { height: "auto", opacity: 1, transition: { height: spring.smooth, opacity: { duration: 0.25, delay: 0.05 } } },
};

