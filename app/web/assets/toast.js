// Sonner-style toasts for a framework-free page: one <ol id="toaster">, call toast() anywhere.
// Stack of 3 visible, expands on hover, timers pause on hover and while the tab is hidden,
// transitions (not keyframes) so rapid toasts retarget smoothly.

const MAX_VISIBLE = 3;
const DURATION = 4200;
const GAP = 10;
const ICONS = {
  success: '<path d="M4 12.5l5 5L20 6.5"/>',
  error: '<path d="M12 7v6M12 17h.01M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  loading: '<path d="M12 3a9 9 0 1 0 9 9"/>',
  info: '<path d="M12 8h.01M11 12h1v5h1"/><circle cx="12" cy="12" r="9"/>',
};

const root = () => document.getElementById("toaster");
const items = []; // { id, el, timer, remaining, started }
let seq = 0;
let hovering = false;

function layout() {
  let offset = 0;
  items.forEach((t, i) => {
    t.el.style.setProperty("--i", String(i));
    t.el.style.setProperty("--offset", String(offset));
    t.el.style.zIndex = String(100 - i);
    t.el.toggleAttribute("data-hidden", i >= MAX_VISIBLE);
    offset += t.el.offsetHeight + GAP;
  });
}

function arm(t) {
  if (t.type === "loading" || hovering || document.hidden) return;
  t.started = performance.now();
  t.timer = setTimeout(() => dismiss(t.id), t.remaining);
}

function pause(t) {
  if (!t.timer) return;
  clearTimeout(t.timer);
  t.timer = null;
  t.remaining = Math.max(600, t.remaining - (performance.now() - t.started));
}

function render(t, type, title, description) {
  t.type = type;
  t.el.className = `toast ${type}`;
  t.el.innerHTML = `<svg class="ti" viewBox="0 0 24 24" aria-hidden="true">${ICONS[type] || ICONS.info}</svg>
    <div><div class="tt"></div>${description ? '<div class="td"></div>' : ""}</div>`;
  t.el.querySelector(".tt").textContent = title;
  if (description) t.el.querySelector(".td").textContent = description;
}

export function toast(title, { type = "info", description = "", duration = DURATION } = {}) {
  const id = ++seq;
  const el = document.createElement("li");
  el.setAttribute("role", type === "error" ? "alert" : "status");
  const t = { id, el, timer: null, remaining: duration, started: 0 };
  render(t, type, title, description);
  items.unshift(t);
  root().prepend(el);
  layout();
  arm(t);
  return id;
}
toast.success = (title, o = {}) => toast(title, { ...o, type: "success" });
toast.error = (title, o = {}) => toast(title, { ...o, type: "error", duration: o.duration ?? 6500 });
toast.loading = (title, o = {}) => toast(title, { ...o, type: "loading" });

export function update(id, title, { type = "success", description = "", duration = DURATION } = {}) {
  const t = items.find((x) => x.id === id);
  if (!t) return toast(title, { type, description, duration });
  pause(t);
  render(t, type, title, description);
  t.remaining = duration;
  layout();
  arm(t);
  return id;
}

export function dismiss(id) {
  const i = items.findIndex((x) => x.id === id);
  if (i < 0) return;
  const [t] = items.splice(i, 1);
  clearTimeout(t.timer);
  t.el.setAttribute("data-leaving", "");
  t.el.addEventListener("transitionend", () => t.el.remove(), { once: true });
  setTimeout(() => t.el.remove(), 400);
  layout();
}

/** toast.promise(p, {loading, success: v => str, error: e => str}) */
toast.promise = async (promise, msgs) => {
  const id = toast.loading(msgs.loading);
  try {
    const v = await promise;
    update(id, typeof msgs.success === "function" ? msgs.success(v) : msgs.success, { type: "success" });
    return v;
  } catch (e) {
    update(id, typeof msgs.error === "function" ? msgs.error(e) : msgs.error, { type: "error", description: e?.message || "" });
    throw e;
  }
};

document.addEventListener("DOMContentLoaded", () => {
  const r = root();
  r.addEventListener("pointerenter", () => { hovering = true; items.forEach(pause); });
  r.addEventListener("pointerleave", () => { hovering = false; items.forEach(arm); });
});
document.addEventListener("visibilitychange", () => items.forEach(document.hidden ? pause : arm));
