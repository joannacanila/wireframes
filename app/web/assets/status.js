// System status: one source of truth for "what can the console do right now".
//   field       the models are loaded; uploads are analysed
//   validation  no model; uploads need reference polygons (the pipeline never invents masks)
//   offline     the API is unreachable

import { toast } from "./toast.js?v=2.23";

const POLL_MS = 20_000;
const OFFLINE_MS = 4_000;
const listeners = new Set();
let state = { mode: "checking", health: null };
let timer = 0;

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
  // v2.23: announce only. Failed inspections are re-run one at a time from the Inspections page (Failed tab, Retry);
  // they are never re-processed automatically, which also used to remount the open page under the user's click.
  if (prev === "validation" && mode === "field") toast.success("Inspection processing is available", { description: "New photos can be screened." });
}

export function startStatus() {
  addEventListener("focus", poll);
  addEventListener("online", poll);
  poll();
}
export const refreshStatus = poll;
