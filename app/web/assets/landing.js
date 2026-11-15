// Landing drawing (v2.15). Two supports and the wires between them, drawn in once on load. One wire (gold) carries the
// measurement WIREFRAME makes on every span: the chord L between its ends and the sag δ at mid-span. Moving the
// pointer up or down changes that wire's sag; the δ/L readout is computed from the drawn curve, not animated text.
// No other motion: the sheet is still unless the pointer moves.

const svg = document.getElementById("draw");
const NS = "http://www.w3.org/2000/svg";
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
let W = 0, H = 0, key = null, target = 0.026, sag = 0.026, raf = 0, first = true;

const el = (name, attrs, parent = svg) => {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  parent.append(e);
  return e;
};

// a hanging wire between (x0, y0) and (x1, y1) with mid-span sag s (px), as a quadratic through the mid point
const span = (x0, y0, x1, y1, s) => {
  const mx = (x0 + x1) / 2, my = (y0 + y1) / 2 + s;
  return `M${x0},${y0} Q${2 * mx - (x0 + x1) / 2},${2 * my - (y0 + y1) / 2} ${x1},${y1}`;
};

function wire(cls, pts, d = 0) {
  const p = el("path", { class: `wire ${cls}`, d: pts });
  if (first && !reduce) {
    const len = p.getTotalLength();
    p.style.setProperty("--len", len);
    p.style.setProperty("--d", `${d}ms`);
    p.classList.add("enter");
  }
  return p;
}

function build() {
  W = innerWidth; H = innerHeight;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.replaceChildren();
  const ax = W * 0.2, bx = W * 0.8, top = H * 0.14, foot = H * 0.98;
  for (const x of [ax, bx]) {
    el("line", { class: "support", x1: x, y1: top, x2: x, y2: foot });
    for (const y of [H * 0.2, H * 0.27]) el("line", { class: "support", x1: x - 26, y1: y, x2: x + 26, y2: y });
  }
  // the wires on both crossarms, continuing beyond the supports to the edges of the sheet
  const rows = [
    [H * 0.2 - 0, 0.012, "far"], [H * 0.2, 0.02, ""], [H * 0.27, 0.017, "far"], [H * 0.27, 0.031, ""], [H * 0.33, 0.045, "far"],
  ];
  const L = bx - ax;
  rows.forEach(([y, s, cls], i) => {
    const dy = (i - 2) * 6;
    wire(cls, span(ax, y + dy, bx, y + dy * 0.6, s * L), 120 + i * 90);
    wire(cls, span(-40, y + dy + 30, ax, y + dy, s * (ax + 40) * 1.4), 300 + i * 90);
    wire(cls, span(bx, y + dy * 0.6, W + 40, y + dy * 0.6 + 26, s * (W - bx + 40) * 1.4), 300 + i * 90);
  });
  // the measured wire, level ends so the sag is the mid-span drop
  // low on the sheet, but always clear of the text block below it, even at the largest sag the pointer can set
  const actTop = document.querySelector(".lx-act").getBoundingClientRect().top;
  key = { x0: ax, x1: bx, y: Math.max(H * 0.3, Math.min(H * 0.42, actTop - 0.06 * L - 20)) };
  key.path = wire("key", span(ax, key.y, bx, key.y, sag * L), 520);
  key.chord = el("line", { class: "chord fade", x1: ax, y1: key.y, x2: bx, y2: key.y, style: "--d:1300ms" });
  key.drop = el("line", { class: "sag fade", style: "--d:1400ms" });
  key.ends = [ax, bx].map((x) => el("circle", { class: "dot fade", cx: x, cy: key.y, r: 3, style: "--d:1300ms" }));
  key.tL = el("text", { class: "fade", x: (ax + bx) / 2 - L * 0.22, y: key.y - 10, style: "--d:1450ms" });
  key.tL.textContent = `L = ${Math.round(L)} px`;
  key.tD = el("text", { class: "key fade", style: "--d:1500ms" });
  if (reduce) svg.querySelectorAll(".fade").forEach((n) => n.classList.remove("fade"));
  first = false;
  place();
}

function place() {
  const L = key.x1 - key.x0, s = sag * L, mx = (key.x0 + key.x1) / 2;
  key.path.setAttribute("d", span(key.x0, key.y, key.x1, key.y, s));
  key.drop.setAttribute("x1", mx); key.drop.setAttribute("y1", key.y);
  key.drop.setAttribute("x2", mx); key.drop.setAttribute("y2", key.y + s);
  key.tD.setAttribute("x", mx + 10); key.tD.setAttribute("y", key.y + s / 2 + 4);
  key.tD.textContent = `δ = ${s.toFixed(0)} px   δ/L = ${(100 * s / L).toFixed(1)}%`;
}

function tick() {
  sag += (target - sag) * 0.12;
  place();
  raf = Math.abs(target - sag) > 1e-5 ? requestAnimationFrame(tick) : 0;
}

addEventListener("pointermove", (e) => {
  if (reduce || e.pointerType === "touch") return;
  target = 0.01 + 0.05 * Math.min(1, Math.max(0, e.clientY / H));
  if (!raf) raf = requestAnimationFrame(tick);
}, { passive: true });

let rt = 0;
addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(build, 120); });
build();
