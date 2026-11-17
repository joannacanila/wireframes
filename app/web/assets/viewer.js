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

// v2.16: a strand whose class evidence is split is shown as "type uncertain" (neutral grey), never forced to a type
export const isUncertain = (w) => w.class_status === "uncertain";
const SHORT = { "power-distribution-lines": "power", "telecom-cable": "telecom", "abandoned-wire": "abandoned" };
export function classLabel(w, short = false) {
  if (isUncertain(w)) return short ? "Type uncertain" : `Type uncertain (more likely ${SHORT[w.class] || w.class})`;
  return short ? ({ "power-distribution-lines": "Power line", "telecom-cable": "Telecom cable", "abandoned-wire": "Abandoned wire" }[w.class] || w.class)
    : (CLASS_LABEL[w.class] || w.class);
}

export function wireColor(w, mode = "risk") {
  if (mode === "class") return css(isUncertain(w) ? "--cls-unknown" : CLASS_VAR[w.class]);
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
    this.layers = { masks: true, inferred: true, vegetation: true, catenary: true, skeleton: false, sag: true, ends: true, labels: true, wiremask: false, overlays: true, poles: true };
    this._off = document.createElement("canvas");
    this.wireMask = null;
    this.vegetation = [];
    this.poles = []; // v2.17: utility-pole boxes in photo pixels (context only)
    this.mode = "risk";
    this.view = { s: 1, x: 0, y: 0 };
    this.wires = [];
    this.visible = null;
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
      const inf = polyPath(w.provenance?.inferred_polygons);
      const gaps = new Path2D();
      for (const [[x0, y0], [x1, y1]] of w.provenance?.inferred_segments || []) { gaps.moveTo(x0, y0); gaps.lineTo(x1, y1); }
      return { ...w, _mask: mask, _inf: inf, _gaps: gaps, _hasInf: !!w.provenance?.inferred_px,
        _curve: line(w.catenary_curve || []), _skel: line(w.centerline || []) };
    });
    this.draw();
  }

  set(layer, on) { this.layers[layer] = on; this.draw(); }
  /** v2.20: draw and hit-test only these wire indices (null = all). Geometry is never changed, only hidden. */
  setVisible(indices) { this.visible = indices ? new Set(indices) : null; this.draw(); }
  _shown() { return this.visible ? this.wires.filter((w) => this.visible.has(w.index)) : this.wires; }
  setPoles(poles) { this.poles = poles || []; this.draw(); }

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
  get zoomPct() { return Math.round((this.view.s || 1) * 100); }
  setMode(mode) { this.mode = mode; this.draw(); }
  select(index) { this.selected = index; this.draw(); if (index != null) this.focus(index); }

  fit() {
    if (!this.image) return;
    const { width: W, height: H } = this.wrap.getBoundingClientRect();
    const pad = 24;
    // not laid out yet (zero-size container): a negative scale would invert the drawing; the resize handler fits later
    if (W <= pad * 2 || H <= pad * 2) return;
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
    for (let i = this._shown().length - 1; i >= 0 && hit == null; i--) {
      const w = this._shown()[i];
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
    // v2.19: condition and sag in plain terms; the score itself stays in the technical diagnostics
    const scored = w.status === "scored" && w.sag_ratio != null;
    this.tip.innerHTML = `<b>Wire ${w.index} · ${classLabel(w)}</b>
      <div class="row"><span>Condition</span><span>${(scored && { high: "Critical", medium: "Minor sag", low: "Within reference" }[w.level]) || "Not assessed"}</span></div>
      ${scored ? `<div class="row"><span>Sag</span><span>${(w.sag_ratio * 100).toFixed(1)}% of its length</span></div>` : ""}`;
    const x = Math.min(e.clientX - r.left + 14, r.width - 200);
    const y = Math.min(e.clientY - r.top + 14, r.height - 90);
    this.tip.style.left = `${x}px`;
    this.tip.style.top = `${y}px`;
    this.tip.setAttribute("data-show", "");
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

  /** Span pixels as WireNet labelled them: fill + a 1-image-px stroke reproduces the pixel mask exactly (a 1-2 px
   *  wide wire has a near-zero-area contour, so fill alone would vanish); inferred bridge pixels are cut out, so
   *  only observed pixels are painted. Drawn opaque off-screen, then composited once at the layer alpha. */
  _spanLayer(ws, alpha) {
    if (!ws.length) return;
    const { ctx, canvas, view, dpr = 1 } = this;
    const off = this._off;
    if (off.width !== canvas.width || off.height !== canvas.height) { off.width = canvas.width; off.height = canvas.height; }
    if (!off.width || !off.height) return; // v2.21: not laid out yet (0 x 0); the next draw paints it
    const o = off.getContext("2d");
    o.setTransform(1, 0, 0, 1, 0, 0); o.clearRect(0, 0, off.width, off.height);
    o.setTransform(dpr * view.s, 0, 0, dpr * view.s, dpr * view.x, dpr * view.y);
    o.lineJoin = "round"; o.lineWidth = 1;
    for (const w of ws) {
      const c = wireColor(w, this.mode);
      o.fillStyle = c; o.strokeStyle = c;
      o.fill(w._mask, "evenodd"); o.stroke(w._mask);
    }
    if (this.layers.inferred) {
      o.globalCompositeOperation = "destination-out";
      for (const w of ws) if (w._hasInf) { o.fill(w._inf, "evenodd"); o.stroke(w._inf); }
      o.globalCompositeOperation = "source-over";
    }
    ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = alpha; ctx.drawImage(off, 0, 0); ctx.restore();
  }

  _paintLayers() {
    const { ctx, canvas, view, dpr = 1 } = this;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!this.image) return;
    const T = () => ctx.setTransform(dpr * view.s, 0, 0, dpr * view.s, dpr * view.x, dpr * view.y);
    // line geometry is in pixel indices: +0.5 puts it on pixel centres, aligned with the mask at every zoom
    const TL = () => ctx.setTransform(dpr * view.s, 0, 0, dpr * view.s, dpr * (view.x + 0.5 * view.s), dpr * (view.y + 0.5 * view.s));
    T();
    ctx.imageSmoothingEnabled = view.s < 2; // crisp pixels when zoomed in to inspect masks
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(this.image, 0, 0);
    if (!this.layers.overlays) return;
    const L = this.layers;

    if (L.wiremask && this.wireMask) {
      ctx.save();
      ctx.imageSmoothingEnabled = false; // class pixels stay crisp
      ctx.globalAlpha = 0.9;
      ctx.drawImage(this.wireMask, 0, 0, this.image.width, this.image.height);
      ctx.restore();
    }

    const px = 1 / view.s; // one CSS pixel in image units
    if (L.poles && this.poles.length) {
      // v2.17: detected utility poles, outlined only (never hit-tested; a pole never creates or joins a wire)
      ctx.save();
      ctx.strokeStyle = css("--cls-pole") || "#e8d27a";
      ctx.lineWidth = 2 * px; ctx.setLineDash([6 * px, 4 * px]);
      for (const p of this.poles) { const [x0, y0, x1, y1] = p.bbox; ctx.strokeRect(x0, y0, x1 - x0, y1 - y0); }
      ctx.restore();
    }
    const focus = this.selected ?? this.hover;
    const dim = (w) => focus != null && w.index !== focus;
    const fw = focus != null ? this.wires.find((x) => x.index === focus) : null;

    if (L.vegetation && this.vegetation.length) {
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

    if (L.masks) {
      this._spanLayer(this._shown().filter((w) => w !== fw), fw ? 0.32 : 0.62);
      if (fw) {
        T(); ctx.globalAlpha = 1; ctx.strokeStyle = "rgba(255,255,255,0.95)"; ctx.lineWidth = 3 * px; ctx.stroke(fw._mask);
        this._spanLayer([fw], 0.95);
      }
    }

    TL();
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    if (L.skeleton) {
      for (const w of this._shown()) {
        ctx.globalAlpha = dim(w) ? 0.25 : 1;
        ctx.strokeStyle = "rgba(0,0,0,0.7)"; ctx.lineWidth = 3 * px; ctx.stroke(w._skel);
        ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 1.25 * px; ctx.stroke(w._skel);
      }
    }

    if (L.inferred) {
      // gaps the span assembler bridged: geometry used by the fit, never detected by the model
      for (const w of this._shown()) {
        if (!w._hasInf) continue;
        ctx.globalAlpha = dim(w) ? 0.3 : 1;
        ctx.setLineDash([]);
        ctx.strokeStyle = "rgba(0,0,0,0.75)"; ctx.lineWidth = 3.5 * px; ctx.stroke(w._gaps);
        ctx.setLineDash([4 * px, 3 * px]);
        ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 1.75 * px; ctx.stroke(w._gaps);
      }
      ctx.setLineDash([]);
    }

    for (const w of this._shown()) {
      const scored = w.status === "scored";
      const c = wireColor(w, this.mode);
      ctx.globalAlpha = dim(w) ? 0.25 : 1;
      if (L.catenary && scored) {
        ctx.strokeStyle = "rgba(0,0,0,0.6)"; ctx.lineWidth = 4 * px; ctx.stroke(w._curve);
        ctx.strokeStyle = c; ctx.lineWidth = 2 * px; ctx.stroke(w._curve);
      }
      const f = w.fit || {};
      if (L.sag && scored && f.endpoints && (focus == null || focus === w.index)) {
        const [[x0, y0], [x1, y1]] = f.endpoints;
        ctx.setLineDash([6 * px, 5 * px]);
        ctx.strokeStyle = "rgba(255,255,255,0.85)"; ctx.lineWidth = 1.25 * px;
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
        ctx.setLineDash([]);
        if (f.sag_point && f.chord_foot) {
          ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5 * px;
          ctx.beginPath(); ctx.moveTo(...f.sag_point); ctx.lineTo(...f.chord_foot); ctx.stroke();
          ctx.beginPath(); ctx.arc(...f.sag_point, 3.5 * px, 0, Math.PI * 2); ctx.fillStyle = c; ctx.fill();
          ctx.lineWidth = 1.5 * px; ctx.strokeStyle = "#fff"; ctx.stroke();
        }
      }
      if (L.ends) {
        // span ends as traced (fit end points when measured): not pole attachment points, which are not detected
        const ends = scored && f.endpoints ? f.endpoints : (w.centerline?.length ? [w.centerline[0], w.centerline.at(-1)] : []);
        for (const [x, y] of ends) {
          ctx.beginPath(); ctx.rect(x - 3 * px, y - 3 * px, 6 * px, 6 * px);
          ctx.fillStyle = "#fff"; ctx.fill(); ctx.lineWidth = 1.25 * px; ctx.strokeStyle = "rgba(0,0,0,0.8)"; ctx.stroke();
        }
      }
    }
    ctx.globalAlpha = 1;

    // screen-space annotations: span numbers and the sag callout stay legible at any zoom
    if (!L.labels || !(L.masks || L.catenary)) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const toScreen = ([x, y]) => [(x + 0.5) * view.s + view.x, (y + 0.5) * view.s + view.y];
    ctx.font = `600 11px ${css("--mono") || "monospace"}`;
    ctx.textBaseline = "middle";
    for (const w of this._shown()) {
      if (focus != null && w.index !== focus && this.selected != null) continue; // selected: only its own label
      const c = wireColor(w, this.mode);
      const curve = w.catenary_curve?.length ? w.catenary_curve : w.centerline;
      const anchor = curve?.length ? curve[Math.floor(curve.length * 0.18)] : [(w.bbox[0] + w.bbox[2]) / 2, w.bbox[1]];
      const [bx, by] = toScreen(anchor);
      const t = String(w.index), tw = Math.max(30, ctx.measureText("888").width + 10); // one width for 1-3 digits
      ctx.globalAlpha = dim(w) ? 0.4 : 1;
      ctx.fillStyle = "rgba(20,21,23,0.9)";
      ctx.beginPath(); ctx.roundRect(bx - tw / 2, by - 23, tw, 16, 3); ctx.fill();
      ctx.fillStyle = c; ctx.fillRect(bx - tw / 2, by - 9, tw, 2);
      // v2.21: centre on the measured digit box (textBaseline "middle" centres the em box, which sits off-centre)
      ctx.textBaseline = "alphabetic";
      const m = ctx.measureText(t);
      ctx.fillStyle = "#fff"; ctx.textAlign = "center";
      ctx.fillText(t, bx, by - 15 + (m.actualBoundingBoxAscent - m.actualBoundingBoxDescent) / 2);
      ctx.textBaseline = "middle";

      const f = w.fit || {};
      if (L.sag && w.status === "scored" && f.sag_point && focus === w.index) {
        const [sx, sy] = toScreen(f.sag_point);
        const label = `sags ${(w.sag_ratio * 100).toFixed(1)}% of its length`; // v2.19: no raw score on the photo
        const lw = ctx.measureText(label).width + 16;
        const cw = this.canvas.width / dpr, ch = this.canvas.height / dpr;
        const lx = Math.max(4, Math.min(sx + 10, cw - lw - 4)), ly = Math.max(14, Math.min(sy + 16, ch - 14));
        ctx.fillStyle = "rgba(20,21,23,0.92)";
        ctx.beginPath(); ctx.roundRect(lx, ly - 10, lw, 20, 3); ctx.fill();
        ctx.fillStyle = c; ctx.fillRect(lx, ly - 10, 2.5, 20);
        ctx.fillStyle = "#f2f2f0"; ctx.textAlign = "left"; ctx.fillText(label, lx + 9, ly + 0.5);
      }
    }
    ctx.globalAlpha = 1;
  }
}
