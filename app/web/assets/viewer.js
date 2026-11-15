// Interactive overlay viewer.
// Layers (all in original-image coordinates, the same frame as the API payload):
//   masks     exact pixel-boundary polygons, even-odd fill        (Algorithm 3 output)
//   vegetation  Vegetation masks, drawn under the wires; never scored, never hit-tested
//   skeleton  pruned longest-path centerline                       (Algorithm 4)
//   catenary  fitted y = a cosh((x-b)/a) + c, sampled by the API   (Equation 6)
//   sag       chord L, perpendicular delta, delta/L callout         (Equation 8)
//   wiremask  v2.11: WireNet's full per-pixel class mask (an image the size of the photo), so every
//             detected wire pixel is visible, including fragments that never became a span
//   overlays  v2.11: master switch; off = the original photo alone
// View: wheel zooms about the cursor, drag pans (pointer capture), double-click fits.
// Rendering is on demand (one rAF per change), HiDPI-correct.

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const LEVEL_VAR = { low: "--risk-low", medium: "--risk-med", high: "--risk-high" };
const CLASS_VAR = {
  "power-distribution-lines": "--cls-power", "telecom-cable": "--cls-telecom", "abandoned-wire": "--cls-dead",
};
export const CLASS_LABEL = {
  "power-distribution-lines": "Power distribution line", "telecom-cable": "Telecom / fiber cable",
  "abandoned-wire": "Abandoned wire",
};

function polyPath(polys) {
  const p = new Path2D();
  for (const poly of polys || []) {
    if (poly.length < 3) continue;
    // +0.5: contours run through pixel centres; this aligns them to the pixel grid
    p.moveTo(poly[0][0] + 0.5, poly[0][1] + 0.5);
    for (let i = 1; i < poly.length; i++) p.lineTo(poly[i][0] + 0.5, poly[i][1] + 0.5);
    p.closePath();
  }
  return p;
}

export function wireColor(w, mode = "risk") {
  if (mode === "class") return css(CLASS_VAR[w.class]);
  return css(LEVEL_VAR[w.level] || "--risk-none");
}

export class OverlayViewer {
  constructor(wrap, { onHover, onSelect } = {}) {
    this.wrap = wrap;
    this.canvas = document.createElement("canvas");
    this.ctx = this.canvas.getContext("2d");
    wrap.append(this.canvas);
    this.tip = Object.assign(document.createElement("div"), { className: "tooltip", role: "tooltip" });
    wrap.append(this.tip);
    this.onHover = onHover;
    this.onSelect = onSelect;
    this.layers = { masks: true, vegetation: true, catenary: true, skeleton: false, sag: true, wiremask: false, overlays: true };
    this.wireMask = null;
    this.vegetation = [];
    this.mode = "risk";
    this.view = { s: 1, x: 0, y: 0 };
    this.wires = [];
    this.hover = null;
    this.selected = null;
    this.image = null;
    this._raf = 0;
    this._bind();
    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(wrap);
  }

  destroy() {
    this._ro.disconnect();
    cancelAnimationFrame(this._raf);
    this._abort.abort();
  }

  async load(url, wires, vegetation = []) {
    const img = new Image();
    img.decoding = "async";
    img.src = url;
    await img.decode();
    this.image = img;
    this.vegetation = vegetation.map((v) => ({ ...v, _mask: polyPath(v.mask_polygons) }));
    this.setWires(wires);
    this.fit();
  }

  setWires(wires) {
    this.wires = wires.map((w) => {
      const mask = polyPath(w.mask_polygons);
      const line = (pts) => {
        const p = new Path2D();
        pts.forEach(([x, y], i) => (i ? p.lineTo(x, y) : p.moveTo(x, y)));
        return p;
      };
      return { ...w, _mask: mask, _curve: line(w.catenary_curve || []), _skel: line(w.centerline || []) };
    });
    this.draw();
  }

  set(layer, on) { this.layers[layer] = on; this.draw(); }

  /** v2.11: load WireNet's full class mask (indexed PNG, transparent background). */
  async setWireMask(url) {
    if (!url) { this.wireMask = null; this.draw(); return false; }
    const img = new Image();
    img.decoding = "async";
    img.src = url;
    try { await img.decode(); } catch { this.wireMask = null; return false; }
    this.wireMask = img;
    this.draw();
    return true;
  }

  zoomIn() { this.zoomBy(1.4); }
  zoomOut() { this.zoomBy(1 / 1.4); }
  reset() { this._userMoved = false; this.fit(); }
  setMode(mode) { this.mode = mode; this.draw(); }
  select(index) { this.selected = index; this.draw(); if (index != null) this.focus(index); }

  fit() {
    if (!this.image) return;
    const { width: W, height: H } = this.wrap.getBoundingClientRect();
    const pad = 24;
    const s = Math.min((W - pad * 2) / this.image.width, (H - pad * 2) / this.image.height);
    this.view = { s, x: (W - this.image.width * s) / 2, y: (H - this.image.height * s) / 2 };
    this._emitZoom();
    this.draw();
  }

  zoomBy(f, cx, cy, user = true) {
    if (user) this._userMoved = true;
    const r = this.wrap.getBoundingClientRect();
    cx ??= r.width / 2; cy ??= r.height / 2;
    const s = Math.min(40, Math.max(0.02, this.view.s * f));
    const k = s / this.view.s;
    this.view = { s, x: cx - (cx - this.view.x) * k, y: cy - (cy - this.view.y) * k };
    this._emitZoom();
    this.draw();
  }

  /** Centre the selected wire, with a short ease so the jump is legible. */
  focus(index) {
    const w = this.wires.find((x) => x.index === index);
    if (!w || !this.image) return;
    const r = this.wrap.getBoundingClientRect();
    const [x1, y1, x2, y2] = w.bbox;
    const s = Math.min(this.view.s * 1.0, (r.width * 0.8) / Math.max(1, x2 - x1), (r.height * 0.8) / Math.max(1, y2 - y1));
    const target = { s: Math.max(s, this.view.s * 0.999), x: 0, y: 0 };
    target.x = r.width / 2 - ((x1 + x2) / 2) * target.s;
    target.y = r.height / 2 - ((y1 + y2) / 2) * target.s;
    const from = { ...this.view };
    const t0 = performance.now();
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dur = reduce ? 0 : 240;
    const ease = (t) => 1 - Math.pow(1 - t, 4);
    const step = (now) => {
      const t = dur ? Math.min(1, (now - t0) / dur) : 1;
      const e = ease(t);
      this.view = { s: from.s + (target.s - from.s) * e, x: from.x + (target.x - from.x) * e, y: from.y + (target.y - from.y) * e };
      this._paint();
      if (t < 1) requestAnimationFrame(step); else this._emitZoom();
    };
    requestAnimationFrame(step);
  }

  draw() {
    cancelAnimationFrame(this._raf);
    this._raf = requestAnimationFrame(() => this._paint());
  }

  // ---------------------------------------------------------------- internals

  _emitZoom() { this.wrap.dispatchEvent(new CustomEvent("zoom", { detail: this.view.s })); }

  _resize() {
    const r = this.wrap.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    this.canvas.width = Math.round(r.width * dpr);
    this.canvas.height = Math.round(r.height * dpr);
    this.dpr = dpr;
    // Until the user pans or zooms, keep the image fitted to the container as layout settles
    // (breakpoints, sidebars, rotation); after that, respect their view.
    if (this.image && !this._userMoved) this.fit(); else this.draw();
  }

  _toImage(clientX, clientY) {
    const r = this.wrap.getBoundingClientRect();
    return [(clientX - r.left - this.view.x) / this.view.s, (clientY - r.top - this.view.y) / this.view.s];
  }

  _hit(clientX, clientY) {
    const [ix, iy] = this._toImage(clientX, clientY);
    const ctx = this.ctx;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.lineWidth = 10 / this.view.s; // generous target for 1-px wires
    let hit = null;
    // topmost = last drawn
    for (let i = this.wires.length - 1; i >= 0 && hit == null; i--) {
      const w = this.wires[i];
      if (ctx.isPointInPath(w._mask, ix, iy, "evenodd") || ctx.isPointInStroke(w._curve, ix, iy) || ctx.isPointInStroke(w._skel, ix, iy)) hit = w.index;
    }
    ctx.restore();
    return hit;
  }

  _bind() {
    this._abort = new AbortController();
    const o = { signal: this._abort.signal };
    const el = this.wrap;
    let drag = null;
    el.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const f = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015));
      this.zoomBy(f, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false, ...o });
    el.addEventListener("pointerdown", (e) => {
      if (drag || e.button !== 0) return; // ignore extra touch points
      drag = { id: e.pointerId, x: e.clientX, y: e.clientY, vx: this.view.x, vy: this.view.y, moved: false };
      el.setPointerCapture(e.pointerId);
    }, o);
    el.addEventListener("pointermove", (e) => {
      if (drag && e.pointerId === drag.id) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (!drag.moved && Math.hypot(dx, dy) > 3) { drag.moved = true; el.setAttribute("data-dragging", ""); }
        if (drag.moved) { this._userMoved = true; this.view.x = drag.vx + dx; this.view.y = drag.vy + dy; this.draw(); this._tip(null); }
        return;
      }
      const h = this._hit(e.clientX, e.clientY);
      if (h !== this.hover) { this.hover = h; this.onHover?.(h); this.draw(); }
      this._tip(h, e);
    }, o);
    const end = (e) => {
      if (!drag || e.pointerId !== drag.id) return;
      if (!drag.moved) { const h = this._hit(e.clientX, e.clientY); this.selected = h; this.onSelect?.(h); this.draw(); }
      drag = null;
      el.removeAttribute("data-dragging");
    };
    el.addEventListener("pointerup", end, o);
    el.addEventListener("pointercancel", end, o);
    el.addEventListener("pointerleave", () => { if (!drag) { this.hover = null; this.onHover?.(null); this._tip(null); this.draw(); } }, o);
    el.addEventListener("dblclick", () => { this._userMoved = false; this.fit(); }, o);
  }

  _tip(index, e) {
    const w = index == null ? null : this.wires.find((x) => x.index === index);
    if (!w) { this.tip.removeAttribute("data-show"); return; }
    const r = this.wrap.getBoundingClientRect();
    const crs = w.crs == null ? "—" : w.crs.toFixed(1);
    const ratio = w.sag_ratio == null ? "—" : `${(w.sag_ratio * 100).toFixed(2)}%`;
    this.tip.innerHTML = `<b>#${w.index} ${CLASS_LABEL[w.class]}</b>
      <div class="row"><span>CRS</span><span>${crs}${w.level ? " · " + w.level : ""}</span></div>
      <div class="row"><span>sag/L</span><span>${ratio}</span></div>
      <div class="row"><span>status</span><span>${w.status.replace(/_/g, " ")}</span></div>`;
    const x = Math.min(e.clientX - r.left + 14, r.width - 200);
    const y = Math.min(e.clientY - r.top + 14, r.height - 90);
    this.tip.style.left = `${x}px`;
    this.tip.style.top = `${y}px`;
    this.tip.setAttribute("data-show", "");
  }

  _fillWire(w, focus, dim, px) {
    const { ctx } = this;
    const c = wireColor(w, this.mode);
    ctx.globalAlpha = dim(w) ? 0.12 : focus === w.index ? 0.7 : 0.38;
    ctx.fillStyle = c;
    ctx.fill(w._mask, "evenodd");
    ctx.globalAlpha = dim(w) ? 0.25 : 0.95;
    ctx.strokeStyle = c;
    ctx.lineWidth = (focus === w.index ? 2 : 1) * px;
    ctx.stroke(w._mask);
  }

  /** v2.13: overlay opacity 0-1 (1 = overlays as drawn, 0 = the photo alone). */
  setOpacity(a) { this.opacity = Math.max(0, Math.min(1, a)); this.draw(); }

  _paint() {
    this._paintLayers();
    // fade the overlays toward the photo: photo * (1 - o) + overlaid * o
    const o = this.opacity ?? 1;
    if (!this.image || o >= 1 || !this.layers.overlays) return;
    const { ctx, view, dpr = 1 } = this;
    ctx.save();
    ctx.setTransform(dpr * view.s, 0, 0, dpr * view.s, dpr * view.x, dpr * view.y);
    ctx.globalAlpha = 1 - o;
    ctx.drawImage(this.image, 0, 0);
    ctx.restore();
  }

  _paintLayers() {
    const { ctx, canvas, view, dpr = 1 } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!this.image) return;
    ctx.setTransform(dpr * view.s, 0, 0, dpr * view.s, dpr * view.x, dpr * view.y);
    ctx.imageSmoothingEnabled = view.s < 2; // crisp pixels when zoomed in to inspect masks
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(this.image, 0, 0);
    if (!this.layers.overlays) return;

    if (this.layers.wiremask && this.wireMask) {
      ctx.save();
      ctx.imageSmoothingEnabled = false; // class pixels stay crisp
      ctx.globalAlpha = 0.85;
      ctx.drawImage(this.wireMask, 0, 0, this.image.width, this.image.height);
      ctx.restore();
    }

    const px = 1 / view.s; // one CSS pixel in image units
    const focus = this.selected ?? this.hover;
    const dim = (w) => focus != null && w.index !== focus;

    if (this.layers.vegetation && this.vegetation.length) {
      const c = css("--cls-veg");
      ctx.fillStyle = c; ctx.strokeStyle = c; ctx.lineWidth = px;
      ctx.setLineDash([4 * px, 3 * px]);
      for (const v of this.vegetation) {
        ctx.globalAlpha = 0.22; ctx.fill(v._mask, "evenodd");
        ctx.globalAlpha = 0.8; ctx.stroke(v._mask);
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }

    if (this.layers.masks) {
      for (const w of this.wires) {
        if (focus === w.index) continue; // drawn last, on top, with a halo
        this._fillWire(w, focus, dim, px);
      }
      const fw = focus != null && this.wires.find((x) => x.index === focus);
      if (fw) {
        ctx.globalAlpha = 1; ctx.strokeStyle = "rgba(255,255,255,0.95)"; ctx.lineWidth = 3.5 * px; ctx.stroke(fw._mask);
        this._fillWire(fw, focus, dim, px);
      }
      ctx.globalAlpha = 1;
    }

    if (this.layers.skeleton) {
      ctx.lineJoin = "round";
      for (const w of this.wires) {
        ctx.globalAlpha = dim(w) ? 0.25 : 1;
        ctx.strokeStyle = "rgba(0,0,0,0.65)"; ctx.lineWidth = 3 * px; ctx.stroke(w._skel);
        ctx.strokeStyle = "#7ee2ff"; ctx.lineWidth = 1.25 * px; ctx.stroke(w._skel);
      }
      ctx.globalAlpha = 1;
    }

    if (this.layers.catenary || this.layers.sag) {
      for (const w of this.wires) {
        if (w.status !== "scored") continue;
        const c = wireColor(w, this.mode);
        ctx.globalAlpha = dim(w) ? 0.25 : 1;
        if (this.layers.catenary) {
          ctx.lineCap = "round";
          ctx.strokeStyle = "rgba(0,0,0,0.6)"; ctx.lineWidth = 4.5 * px; ctx.stroke(w._curve);
          ctx.strokeStyle = c; ctx.lineWidth = 2.25 * px; ctx.stroke(w._curve);
        }
        const f = w.fit || {};
        if (this.layers.sag && f.endpoints) {
          const [[x0, y0], [x1, y1]] = f.endpoints;
          ctx.setLineDash([6 * px, 5 * px]);
          ctx.strokeStyle = "rgba(255,255,255,0.85)"; ctx.lineWidth = 1.25 * px;
          ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
          ctx.setLineDash([]);
          for (const [x, y] of f.endpoints) { ctx.beginPath(); ctx.arc(x, y, 3 * px, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill(); }
          if (f.sag_point && f.chord_foot) {
            ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5 * px;
            ctx.beginPath(); ctx.moveTo(...f.sag_point); ctx.lineTo(...f.chord_foot); ctx.stroke();
            ctx.beginPath(); ctx.arc(...f.sag_point, 3.5 * px, 0, Math.PI * 2); ctx.fillStyle = c; ctx.fill();
            ctx.lineWidth = 1.5 * px; ctx.strokeStyle = "#fff"; ctx.stroke();
          }
        }
      }
      ctx.globalAlpha = 1;
    }

    // screen-space annotations: wire badges and sag callouts stay legible at any zoom
    if (!this.layers.masks && !this.layers.catenary) { ctx.globalAlpha = 1; return; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const toScreen = ([x, y]) => [x * view.s + view.x, y * view.s + view.y];
    ctx.font = `500 11px ${css("--mono") || "monospace"}`;
    ctx.textBaseline = "middle";
    for (const w of this.wires) {
      const c = wireColor(w, this.mode);
      const curve = w.catenary_curve?.length ? w.catenary_curve : w.centerline;
      const anchor = curve?.length ? curve[Math.floor(curve.length * 0.18)] : [(w.bbox[0] + w.bbox[2]) / 2, w.bbox[1]];
      const [bx, by] = toScreen(anchor);
      ctx.globalAlpha = dim(w) ? 0.35 : 1;
      ctx.beginPath(); ctx.arc(bx, by - 14, 9, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(12,13,15,0.88)"; ctx.fill();
      ctx.lineWidth = 1.5; ctx.strokeStyle = c; ctx.stroke();
      ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.fillText(String(w.index), bx, by - 13.5);

      const f = w.fit || {};
      if (this.layers.sag && w.status === "scored" && f.sag_point && (focus == null || focus === w.index)) {
        const [sx, sy] = toScreen(f.sag_point);
        const label = `sag/L ${(w.sag_ratio * 100).toFixed(2)}%  ·  CRS ${w.crs.toFixed(1)}`;
        const tw = ctx.measureText(label).width + 16;
        const cw = this.canvas.width / dpr, ch = this.canvas.height / dpr;
        // keep the callout inside the canvas (it used to run off the right edge)
        const lx = Math.max(4, Math.min(sx + 10, cw - tw - 4)), ly = Math.max(14, Math.min(sy + 16, ch - 14));
        ctx.fillStyle = "rgba(12,13,15,0.9)";
        ctx.beginPath(); ctx.roundRect(lx, ly - 10, tw, 20, 5); ctx.fill();
        ctx.fillStyle = c; ctx.fillRect(lx, ly - 10, 2.5, 20);
        ctx.fillStyle = "#e9ebee"; ctx.textAlign = "left"; ctx.fillText(label, lx + 9, ly + 0.5);
      }
    }
    ctx.globalAlpha = 1;
  }
}
