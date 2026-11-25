/* App-wide state that several pages need: theme, toasts, backend status. */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, type StatusMap } from "./api";

export const storage = {
  get<T>(key: string, fallback: T): T {
    try { const v = localStorage.getItem("tf:" + key); return v == null ? fallback : (JSON.parse(v) as T); } catch { return fallback; }
  },
  set(key: string, value: unknown) { try { localStorage.setItem("tf:" + key, JSON.stringify(value)); } catch { /* private mode */ } },
};

export type Theme = "dark" | "light";
export interface Toast { id: number; text: string; kind: "info" | "ok" | "bad" }

interface AppState {
  theme: Theme; toggleTheme: () => void;
  toasts: Toast[]; toast: (text: string, kind?: Toast["kind"]) => void; dismiss: (id: number) => void;
  status: StatusMap | null; refreshStatus: () => void;
}

const Ctx = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme as Theme) || "dark");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [status, setStatus] = useState<StatusMap | null>(null);
  const nextId = useRef(1);

  useEffect(() => { document.documentElement.dataset.theme = theme; storage.set("theme", theme); }, [theme]);
  const toggleTheme = useCallback(() => setTheme((t) => (t === "dark" ? "light" : "dark")), []);

  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((t) => t.id !== id)), []);
  const toast = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = nextId.current++;
    setToasts((ts) => [...ts.slice(-3), { id, text, kind }]);
    window.setTimeout(() => dismiss(id), 4500);
  }, [dismiss]);

  const refreshStatus = useCallback(() => { api.status().then(setStatus).catch(() => setStatus(null)); }, []);
  useEffect(() => { refreshStatus(); const t = window.setInterval(refreshStatus, 30000); return () => window.clearInterval(t); }, [refreshStatus]);

  const value = useMemo(() => ({ theme, toggleTheme, toasts, toast, dismiss, status, refreshStatus }),
    [theme, toggleTheme, toasts, toast, dismiss, status, refreshStatus]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}

/** Elapsed m:ss from `start` to `end` (ms epochs); ticks while end is null. */
export function useElapsed(start: number | null, end: number | null): string {
  const running = start != null && end == null;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(t);
  }, [running]);
  if (!start) return "0:00";
  const s = Math.max(0, Math.floor(((end ?? now) - start) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
