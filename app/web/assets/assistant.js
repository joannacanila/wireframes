// v2.20 WIREFRAME Assistant. Opened from the "Assistant" control in the top bar (it never floats over tables, map
// controls or notices); a side panel on desktop, a full-screen sheet on phones. Three layers, all server-side
// (/api/assistant/ask):
//   1. live data: workload questions are answered from the records this user may see (counts, pole labels);
//   2. documentation: WIREFRAME's knowledge base, optionally phrased by Claude, with the open inspection's facts;
//   3. actions: navigation inside the portal ("Show failed inspections", "Open pole SAL-001"). The page performs
//      the navigation and only then says it happened. The assistant has no write actions.
// The conversation is kept for this browser tab only (sessionStorage).

const KEY = "wf-assistant";
const SUGGEST = {
  inspection: ["Explain this inspection's findings", "Why did some wires remain unassessed?", "What does this technical metric mean?"],
  map: ["Which high-risk poles are missing GPS?", "How do I navigate to a pole?", "Summarize today's dispatch priorities"],
  inspections: ["Show failed inspections", "Which poles need review?", "What does not assessed mean?"],
  default: ["What needs urgent review?", "Which inspections failed?", "Summarize the current workload"],
};
const PAGE = { inspection: "Inspection", map: "Map & Dispatch", inspections: "Inspections", default: "Dashboard" };
const SUGGEST_NAV = { map: "How can I go to the poles?", inspections: "How do I open an inspection report?", inspection: "Explain this inspection's findings", default: "What can you do?" };
const RISK_CLS = { high: "high", medium: "medium", low: "low", unassessed: "unassessed", failed: "failed", processing: "processing" };

export function mountAssistant({ api, json, esc, getContext, navigate, launcher }) {
  let state = { open: false, wide: false, turns: [] };
  try { state = { ...state, ...JSON.parse(sessionStorage.getItem(KEY) || "{}") }; } catch { /* storage blocked */ }
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify({ ...state, busy: false, turns: state.turns.slice(-30) })); } catch { /* ignore */ } };
  let provider = null;
  api("/api/assistant/status").then((s) => { provider = s.provider; draw(); }).catch(() => {});

  const root = document.createElement("div");
  root.className = "asst";
  root.innerHTML = `
    <section class="asst-panel" id="asst-panel" role="dialog" aria-label="WIREFRAME Assistant" hidden>
      <header class="asst-head"><img src="/assets/logo.svg" alt="" width="20" height="20">
        <h2>WIREFRAME Assistant</h2><span class="asst-ctx" id="asst-ctx"></span><span class="asst-sp"></span>
        <button class="asst-ic" type="button" data-act="clear" title="Reset conversation" aria-label="Reset conversation"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4"/></svg></button>
        <button class="asst-ic asst-wide" type="button" data-act="wide" title="Expand" aria-label="Expand"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 15v5h-5M4 4l6 6M20 20l-6-6"/></svg></button>
        <button class="asst-ic" type="button" data-act="close" title="Close" aria-label="Close"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button></header>
      <div class="asst-log" id="asst-log" aria-live="polite"></div>
      <form class="asst-form" id="asst-form"><label class="sr-only" for="asst-q">Ask a question</label>
        <textarea id="asst-q" rows="1" maxlength="1000" placeholder="Ask about this page, a pole or a result"></textarea>
        <button class="btn sm asst-send" type="submit" disabled>Send</button></form>
      <p class="asst-foot">Decision-support tool. Always verify findings with a qualified professional.</p>
    </section>`;
  document.body.append(root);
  const panel = root.querySelector(".asst-panel"), log = root.querySelector("#asst-log");
  const form = root.querySelector("#asst-form"), q = root.querySelector("#asst-q");

  const fmtText = (t) => {
    const lines = esc(t).split(/\n+/);
    let html = "", list = null;
    for (const l of lines) {
      const m = l.match(/^\s*(?:[-•*]|\d+[.)])\s+(.*)$/);
      if (m) { if (!list) { list = []; } list.push(`<li>${m[1]}</li>`); continue; }
      if (list) { html += `<ul>${list.join("")}</ul>`; list = null; }
      if (l.trim()) html += `<p>${l}</p>`;
    }
    if (list) html += `<ul>${list.join("")}</ul>`;
    return html.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  };
  const kind = () => {
    const c = getContext();
    return c.analysisId ? "inspection" : c.page.startsWith("/admin/map") ? "map" : c.page.startsWith("/admin/inspections") ? "inspections"
      : "default";
  };
  const card = (p) => `<div class="pcard"><div class="pcard-top"><b>${esc(p.label)}</b>${p.label !== p.pole_code ? `<span class="mono">${esc(p.pole_code)}</span>` : ""}
      <span class="risk risk-${RISK_CLS[p.risk] || "unassessed"}"><i></i>${esc(p.risk_label)}</span></div>
    ${p.place || p.issue ? `<p>${esc([p.place, p.issue].filter(Boolean).join(" · "))}</p>` : ""}
    <div class="pcard-acts"><button type="button" class="link-btn" data-go="/admin/inspections/${esc(p.id)}">Open inspection</button>
      <button type="button" class="link-btn" data-go="/admin/map?pole=${encodeURIComponent(p.pole_code)}">${p.location ? "Locate on map" : "Needs pinning"}</button></div></div>`;

  function draw() {
    const c = getContext(), k = kind();
    void provider; void SUGGEST_NAV;
    root.querySelector("#asst-ctx").textContent = PAGE[k]; // a fixed, short page name (never a sentence that clips)
    if (!state.turns.length) {
      log.innerHTML = `<div class="asst-empty"><p>Ask how to do something, what a result means, or about the current workload.</p>
        <ul class="asst-sug">${SUGGEST[k].map((s) => `<li><button type="button" data-q="${esc(s)}">${esc(s)}</button></li>`).join("")}</ul></div>`;
      return;
    }
    log.innerHTML = state.turns.map((t) => t.role === "user" ? `<div class="turn q"><p>${esc(t.content)}</p></div>`
      : t.error ? `<div class="turn a err"><p>${esc(t.content)}</p><button type="button" class="link-btn" data-retry>Try again</button></div>`
      : `<div class="turn a">${fmtText(t.content)}
          ${t.poles?.length ? `<div class="pcards">${t.poles.map(card).join("")}</div>` : ""}
          ${t.suggest?.length ? `<div class="asst-chips">${t.suggest.map((x) => `<button type="button" data-q="${esc(x)}">${esc(x)}</button>`).join("")}</div>` : ""}
          ${t.nav?.length ? `<div class="asst-nav">${t.nav.map((n) => `<button type="button" class="btn sm" data-go="${esc(n.href)}">Go to ${esc(n.label)} →</button>`).join("")}</div>` : ""}
          ${t.inspection ? `<details class="msg-insp"><summary>Facts used from this inspection</summary><p>${esc(t.inspection.summary)}</p></details>` : ""}
          ${t.done ? `<p class="msg-done">✓ ${esc(t.done)}</p>` : ""}
          ${t.sources?.length ? `<p class="msg-src"><span>Sources</span>${t.sources.map((x) => x.link ? `<a href="${esc(x.link)}" data-go="${esc(x.link)}">${esc(x.title)}</a>` : `<span class="src-t">${esc(x.title)}</span>`).join("")}</p>` : ""}
          ${t.mode === "fallback" ? `<p class="msg-note">The AI service is unavailable, so this is the closest part of the documentation.</p>` : ""}</div>`).join("")
      + (state.busy ? `<div class="turn a busy" aria-label="Answering"><span></span><span></span><span></span></div>` : "");
    log.scrollTop = log.scrollHeight;
  }

  async function ask(text) {
    text = text.trim(); if (!text || state.busy) return;
    const c = getContext();
    const history = state.turns.filter((t) => !t.error).slice(-6).map((t) => ({ role: t.role, content: t.content.slice(0, 4000) }));
    state.turns.push({ role: "user", content: text });
    state.busy = true; draw(); syncSend?.();
    try {
      const r = await api("/api/assistant/ask", json("POST", { question: text, page: c.page, analysis_id: c.analysisId || null, history }));
      const turn = { role: "assistant", content: r.answer, sources: r.sources, mode: r.mode, inspection: r.inspection, poles: r.poles || [],
        nav: (r.nav || []).filter((n) => typeof n.href === "string" && (n.href.startsWith("/admin") || n.href === "/workspace")), suggest: r.suggest || [] };
      state.turns.push(turn);
      if (r.action?.type === "navigate" && typeof r.action.href === "string" && r.action.href.startsWith("/admin")) {
        state.busy = false;
        try { navigate(r.action.href); turn.done = r.action.label ? `${r.action.label}: done` : "Done"; }
        catch { turn.done = null; turn.content += "\n\nI could not open that page. Use the link above instead."; }
      }
    } catch (e) {
      state.turns.push({ role: "assistant", error: true, retryOf: text,
        content: e.status === 429 ? e.message : "The assistant could not answer just now. Check the connection and try again." });
    } finally { state.busy = false; save(); draw(); syncSend?.(); }
  }

  function setOpen(open) {
    state.open = open; save();
    panel.hidden = !open; launcher?.setAttribute("aria-expanded", String(open)); root.toggleAttribute("data-open", open);
    document.body.toggleAttribute("data-asst", open && !state.wide);
    if (open) { draw(); setTimeout(() => q.focus(), 0); } else launcher?.focus({ preventScroll: true });
  }
  const setWide = (w) => {
    state.wide = w; save(); root.toggleAttribute("data-wide", w);
    const b = root.querySelector(".asst-wide"); b.setAttribute("aria-label", w ? "Make smaller" : "Expand"); b.title = w ? "Make smaller" : "Expand";
    document.body.toggleAttribute("data-asst", !panel.hidden && !w);
  };

  launcher?.addEventListener("click", () => setOpen(panel.hidden));
  root.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (b?.dataset.act === "close") setOpen(false);
    if (b?.dataset.act === "wide") setWide(!state.wide);
    if (b?.dataset.act === "clear") { state.turns = []; save(); draw(); q.focus(); }
    const s = e.target.closest("[data-q]"); if (s) ask(s.dataset.q);
    const g = e.target.closest("[data-go]");
    if (g) {
      e.preventDefault();
      const href = g.dataset.go;
      if (href === "/workspace") open(href, "_blank", "noopener"); else navigate(href);
      if (matchMedia("(max-width: 760px)").matches) setOpen(false);
    }
    if (e.target.closest("[data-retry]")) { const last = state.turns.pop(); state.turns.pop(); save(); ask(last.retryOf || ""); }
  });
  panel.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
  form.addEventListener("submit", (e) => { e.preventDefault(); const v = q.value; q.value = ""; q.style.height = ""; ask(v); });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  const send = root.querySelector(".asst-send");
  const syncSend = () => { send.disabled = !q.value.trim() || !!state.busy; send.textContent = state.busy ? "Sending…" : "Send"; };
  q.addEventListener("input", () => { q.style.height = "auto"; q.style.height = `${Math.min(q.scrollHeight, 120)}px`; syncSend(); });

  state.busy = false;
  setWide(!!state.wide);
  if (state.open) { panel.hidden = false; launcher?.setAttribute("aria-expanded", "true"); root.setAttribute("data-open", ""); document.body.toggleAttribute("data-asst", !state.wide); }
  return {
    context: () => { if (!panel.hidden) draw(); },
    hide: () => { root.hidden = true; if (launcher) launcher.hidden = true; document.body.removeAttribute("data-asst"); },
    show: () => { root.hidden = false; if (launcher) launcher.hidden = false; document.body.toggleAttribute("data-asst", !panel.hidden && !state.wide); },
  };
}
