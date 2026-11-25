import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production build is written into the Python package, so the FastAPI
// server ships it and running TestForge never needs Node.js.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  build: {
    outDir: "../testforge/web/static/app",
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
    proxy: { "/api": "http://127.0.0.1:8000", "/demo": "http://127.0.0.1:8000" },
  },
});
