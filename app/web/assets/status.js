// System status: one source of truth for "what can the console do right now".
//   field       the segmentation model is loaded; uploads are segmented by YOLO11-seg
//   validation  no model; uploads need reference polygons (the pipeline never invents masks)
//   offline     the API is unreachable
// When the model appears, the admin portal can re-queue analyses that failed for want of it.

import { toast, update } from "./toast.js";

const POLL_MS = 20_000;
const OFFLINE_MS = 4_000;
const listeners = new Set();
let state = { mode: "checking", health: null };
let timer = 0;
let opts = { retryFailed: false };

export const getStatus = () => state;
export function onStatus(fn) { listeners.add(fn); fn(state); return () => listeners.delete(fn); }

async function poll() {
  clearTimeout(timer);
  let next;
  try {
    const r = await fetch("/api/health", { cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    const h = await r.json();
    next = { mode: h.mode, health: h };
  } catch {
    next = { mode: "offline", health: state.health };
  }
  const prev = state.mode;
  state = next;
  if (prev !== next.mode) transition(prev, next.mode);
  listeners.forEach((fn) => fn(state));
  timer = setTimeout(poll, state.mode === "offline" ? OFFLINE_MS : POLL_MS);
}

function transition(prev, mode) {
  if (prev === "offline" && mode !== "offline") toast.success("Connection restored");
  if (prev === "validation" && mode === "field") {
    if (opts.retryFailed) offerRetry(); else toast.success("Detection model online", { description: "Photos are now segmented automatically." });
  }
}

async function offerRetry() {
  const id = toast.loading("Segmentation model loaded — field mode on", { description: "Looking for analyses that failed without it…" });
  try {
    const list = await (await fetch("/api/analysis?limit=200")).json();
    const failed = list.items.filter((a) => a.status === "error");
    if (!failed.length) { update(id, "Segmentation model loaded — field mode on", { type: "success" }); return; }
    let ok = 0;
    for (const a of failed) {
      const r = await fetch(`/api/analysis/${a.id}/retry`, { method: "POST" });
      if (r.ok) ok++;
    }
    update(id, `Field mode on · ${ok} failed ${ok === 1 ? "analysis" : "analyses"} re-queued`, { type: "success" });
    dispatchEvent(new CustomEvent("wf:refresh"));
  } catch (e) {
    update(id, "Field mode on", { type: "success", description: "Re-run failed analyses from their pages." });
  }
}

export function startStatus(options = {}) {
  opts = { ...opts, ...options };
  addEventListener("focus", poll);
  addEventListener("online", poll);
  poll();
}
export const refreshStatus = poll;
