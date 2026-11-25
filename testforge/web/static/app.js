/* TestForge UI: shared shell (sidebar, theme, backend status, helpers). */
const TF = (() => {
  const ICONS = {
    code: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/></svg>',
    robot: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01M9 17h6"/></svg>',
    shop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><path d="M3 6h18M16 10a4 4 0 0 1-8 0"/></svg>',
    book: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5V21h16"/></svg>',
    sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
    menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>',
    play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15a1 1 0 0 0 1.5.86l12-7.5a1 1 0 0 0 0-1.72l-12-7.5A1 1 0 0 0 7 4.5z"/></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>',
    bug: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="6" width="8" height="14" rx="4"/><path d="M19 7l-3 2M5 7l3 2M19 19l-3-2M5 19l3-2M20 13h-4M4 13h4M10 4l1 2M14 4l-1 2"/></svg>',
  };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem("tf:" + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("tf:" + k, JSON.stringify(v)); } catch (e) {} },
  };

  function applyTheme(t) {
    const theme = t || store.get("theme", window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    document.documentElement.dataset.theme = theme;
    const btn = document.getElementById("themeBtn");
    if (btn) btn.innerHTML = (theme === "dark" ? ICONS.sun + " Light mode" : ICONS.moon + " Dark mode");
    return theme;
  }
  applyTheme();

  function shell(active) {
    const nav = [
      ["generate", "/", ICONS.code, "Generate tests"],
      ["automation", "/automation", ICONS.robot, "UI automation"],
    ];
    const side = document.createElement("aside");
    side.className = "sidebar";
    side.innerHTML = `
      <div class="brand"><div class="brand-logo">T</div><div><b>TestForge</b><small>Multi-agent test generation</small></div></div>
      <div class="nav-label">Workspace</div>
      <nav class="nav">${nav.map(([k, href, ic, label]) => `<a href="${href}" class="${k === active ? "on" : ""}">${ic}${label}</a>`).join("")}</nav>
      <div class="nav-label">Resources</div>
      <nav class="nav"><a href="/demo" target="_blank" rel="noopener">${ICONS.shop}ShopLite demo ↗</a>
        <a href="https://github.com/SreeramRajesh638/testforge#readme" target="_blank" rel="noopener">${ICONS.book}Documentation ↗</a></nav>
      <div class="side-foot">
        <div class="status-card"><h4>AI backends</h4><div id="backendStatus" class="small muted">checking…</div></div>
        <button class="btn btn-ghost theme-btn" id="themeBtn"></button>
      </div>`;
    const shellEl = document.querySelector(".shell");
    shellEl.prepend(side);
    const top = document.createElement("div");
    top.className = "topbar";
    top.innerHTML = `<button class="btn btn-ghost" id="menuBtn" aria-label="Menu">${ICONS.menu}</button><b>TestForge</b>`;
    shellEl.querySelector(".main").prepend(top);
    document.getElementById("menuBtn").onclick = e => { e.stopPropagation(); document.body.classList.toggle("nav-open"); };
    document.addEventListener("click", e => { if (!side.contains(e.target)) document.body.classList.remove("nav-open"); });
    document.getElementById("themeBtn").onclick = () => {
      const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      store.set("theme", t); applyTheme(t); document.dispatchEvent(new CustomEvent("tf-theme"));
    };
    applyTheme();
    loadStatus();
  }

  let status = null;
  async function loadStatus() {
    try { status = await (await fetch("/api/status")).json(); } catch (e) { status = null; }
    const el = document.getElementById("backendStatus");
    if (!el) return status;
    if (!status) { el.textContent = "unavailable"; return null; }
    const names = {mock: "Mock (no AI)", groq: "Groq", ollama: "Ollama", claude: "Claude"};
    el.innerHTML = Object.entries(names).map(([k, n]) => `<div class="status-row" title="${esc(status[k].note)}">
      <span class="dot ${status[k].ready ? "on" : "off"}"></span>${n}<span class="note">${esc(status[k].note)}</span></div>`).join("");
    document.dispatchEvent(new CustomEvent("tf-status", {detail: status}));
    return status;
  }

  function toast(msg, kind = "") {
    let wrap = document.querySelector(".toast-wrap");
    if (!wrap) { wrap = document.createElement("div"); wrap.className = "toast-wrap"; document.body.appendChild(wrap); }
    const t = document.createElement("div");
    t.className = "toast " + kind; t.textContent = msg; wrap.appendChild(t);
    setTimeout(() => t.remove(), 4200);
  }
  function lightbox(src) {
    const lb = document.createElement("div");
    lb.className = "lightbox"; lb.innerHTML = `<img alt="Screenshot" src="${src}">`;
    lb.onclick = () => lb.remove(); document.body.appendChild(lb);
  }
  async function apiError(res) {
    let msg = await res.text();
    try { const d = JSON.parse(msg).detail; msg = typeof d === "string" ? d : JSON.stringify(d); } catch (e) {}
    return msg;
  }
  return {ICONS, esc, store, shell, toast, lightbox, apiError, get status() { return status; }, loadStatus};
})();
