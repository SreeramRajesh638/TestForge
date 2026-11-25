/* SVG line drawing via a motion value: <m.path style={{ pathLength: useDraw(1) }} />.
 * Animating the motion value directly avoids re-renders and keeps types simple. */
import { animate, useMotionValue, useReducedMotion, type MotionValue, type ValueAnimationTransition } from "framer-motion";
import { useEffect } from "react";

export function useDraw(target: number, transition: ValueAnimationTransition<number> = { duration: 0.9, ease: [0.16, 1, 0.3, 1] }, from = 0): MotionValue<number> {
  const reduce = useReducedMotion();
  const mv = useMotionValue(reduce ? target : from);
  useEffect(() => {
    if (reduce) { mv.jump(target); return; }
    const ctl = animate(mv, target, transition);
    return () => ctl.stop();
  }, [target, reduce]); // eslint-disable-line react-hooks/exhaustive-deps
  return mv;
}
