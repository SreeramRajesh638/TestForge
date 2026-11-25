import { copyFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Two build targets:
// * default: written into the Python package (base /app/), so the FastAPI server
//   ships it and running TestForge never needs Node.js;
// * on Vercel (VERCEL=1): the "frontend" service serves dist/ at the site root,
//   while /api, /demo, /classic, /static and /healthz are routed to the "app"
//   service by vercel.json. All API calls in the app are relative (/api/...).
const onVercel = !!process.env.VERCEL;
const outDir = onVercel ? "dist" : "../testforge/web/static/app";

// Client-side routes of the SPA. Each gets its own index.html copy so a deep
// link or a reload (e.g. /monitor) is served as a static file by the frontend
// service, with no server-side fallback needed.
const ROUTES = ["automation", "monitor", "experiments", "runs"];
function spaRouteCopies(): Plugin {
  let root = "";
  return {
    name: "spa-route-copies",
    apply: "build",
    configResolved(c) {
      root = resolve(c.root, outDir);
    },
    closeBundle() {
      if (!onVercel) return;
      for (const r of ROUTES) {
        mkdirSync(resolve(root, r), { recursive: true });
        copyFileSync(resolve(root, "index.html"), resolve(root, r, "index.html"));
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), spaRouteCopies()],
  base: onVercel ? "/" : "/app/",
  build: {
    outDir,
    emptyOutDir: true,
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          motion: ["framer-motion"],
          editor: ["codemirror", "highlight.js"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8080", "/demo": "http://127.0.0.1:8080" },
  },
});
