import { LazyMotion, m, MotionConfig } from "framer-motion";
import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { Shell } from "./components/layout/Shell";
import { Skeleton } from "./components/ui/display";
import { AppProvider } from "./lib/store";
import { page } from "./motion/variants";
import "./styles.css";

// Each page is its own chunk; animation features load asynchronously.
const Generate = lazy(() => import("./pages/Generate"));
const Automation = lazy(() => import("./pages/Automation"));
const Monitor = lazy(() => import("./pages/Monitor"));
const Experiments = lazy(() => import("./pages/Experiments"));
const Runs = lazy(() => import("./pages/Runs"));
const BlindSpot = lazy(() => import("./pages/BlindSpot"));
const loadFeatures = () => import("framer-motion").then((mod) => mod.domMax);

function PageFallback() {
  return (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-10 w-72" /><Skeleton className="h-5 w-[28rem] max-w-full" />
      <div className="grid gap-5 xl:grid-cols-2"><Skeleton className="h-[420px]" /><Skeleton className="h-[420px]" /></div>
    </div>
  );
}

/* Route changes animate the incoming page only. (An exit animation under
 * AnimatePresence mode="wait" could get stuck after a long run and leave the
 * old page on screen; entering-only transitions cannot block navigation.) */
function AnimatedRoutes() {
  const location = useLocation();
  return (
    <>
      <m.div key={location.pathname} variants={page} initial="initial" animate="enter">
        <Suspense fallback={<PageFallback />}>
          <Routes location={location}>
            <Route path="/" element={<Generate />} />
            <Route path="/automation" element={<Automation />} />
            <Route path="/monitor" element={<Monitor />} />
            <Route path="/experiments" element={<Experiments />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/blindspot" element={<BlindSpot />} />
            <Route path="*" element={<Generate />} />
          </Routes>
        </Suspense>
      </m.div>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LazyMotion features={loadFeatures} strict>
      <MotionConfig reducedMotion="user">
        <AppProvider>
          <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
            <Shell><AnimatedRoutes /></Shell>
          </BrowserRouter>
        </AppProvider>
      </MotionConfig>
    </LazyMotion>
  </StrictMode>,
);
