// Shared helpers for both portals.

export async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  if (res.status === 204) return null;
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    const err = new Error(body?.error?.message || `Request failed (${res.status})`);
    Object.assign(err, { code: body?.error?.code, status: res.status, hint: body?.error?.hint,
      retryAfter: Number(res.headers.get("retry-after")) || null });
    if (res.status === 401) dispatchEvent(new CustomEvent("wf:unauthorized"));
    throw err;
  }
  return body;
}

export const json = (method, body) => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
export const pct = (v, d = 2) => (v == null ? "–" : `${(v * 100).toFixed(d)}%`);

export function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export const STATUS_LABEL = { processing: "Processing", complete: "Complete", no_wires_detected: "No wires",
  fit_failed: "Fit failed", error: "Failed" };

export function levelChip(level, status) {
  if (level) return `<span class="chip ${level}">${level}</span>`;
  return `<span class="chip ${status === "processing" ? "processing" : "plain"}">${esc(STATUS_LABEL[status] || status || "–")}</span>`;
}

/** Poll an analysis until it leaves "processing". Resolves with the status payload. */
export function waitForAnalysis(id, { signal, onTick } = {}) {
  return new Promise((resolve, reject) => {
    let delay = 450;
    const tick = async () => {
      if (signal?.aborted) return reject(new DOMException("aborted", "AbortError"));
      try {
        const s = await api(`/api/analysis/${id}`);
        onTick?.(s);
        if (s.status !== "processing") return resolve(s);
      } catch (e) { if (e.status === 404) return reject(e); }
      delay = Math.min(delay * 1.35, 2500);
      setTimeout(tick, delay);
    };
    tick();
  });
}
