/** Design tokens live in src/styles.css as CSS variables (RGB triplets) so
 *  dark/light themes can cross-fade; Tailwind maps onto them here. */
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: v("bg"), "bg-2": v("bg-2"), panel: v("panel"), "panel-2": v("panel-2"),
        line: v("line"), "line-2": v("line-2"),
        ink: v("ink"), "ink-2": v("ink-2"), muted: v("muted"),
        accent: v("accent"), "accent-2": v("accent-2"),
        ok: v("ok"), bad: v("bad"), warn: v("warn"), info: v("info"),
        intel: v("intel"), "intel-2": v("intel-2"),
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: { xl2: "1rem" },
      boxShadow: {
        card: "0 1px 0 0 rgb(255 255 255 / 0.04) inset, 0 1px 2px rgb(0 0 0 / 0.3), 0 12px 32px -12px rgb(0 0 0 / 0.45)",
        glow: "0 0 0 1px rgb(var(--accent) / 0.35), 0 10px 40px -10px rgb(var(--accent) / 0.55)",
      },
      keyframes: {
        shimmer: { "100%": { transform: "translateX(100%)" } },
      },
      animation: { shimmer: "shimmer 1.6s infinite" },
    },
  },
  plugins: [],
};
