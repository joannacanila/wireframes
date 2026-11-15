// Browser implementation of the VNI (Equations 1-4) and of the chord sag (Equation 8) for the
// landing-page visualizer. It mirrors app/pipeline/vni.py and catenary.py and is checked
// against them numerically by tests/test_web_math.py, so the page shows the same numbers the
// pipeline would compute.

/** In-place iterative radix-2 complex FFT. n must be a power of two. */
function fft1d(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    const wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const a = i + k, b = a + len / 2;
        const tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr;
        re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
        const nr = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = nr;
      }
    }
  }
}

/** Periodic Hann window (scipy.signal.windows.hann(n, sym=False)). */
export function hann(n) {
  const w = new Float64Array(n);
  for (let i = 0; i < n; i++) w[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / n);
  return w;
}

/** Centred power spectrum P(u, v) of the Hann-windowed m x n image (row-major Float64Array). */
export function powerSpectrum(gray, m, n, removeMean = false) {
  const wm = hann(m), wn = hann(n);
  let mean = 0;
  if (removeMean) { for (let i = 0; i < gray.length; i++) mean += gray[i]; mean /= gray.length; }
  const re = new Float64Array(m * n), im = new Float64Array(m * n);
  for (let y = 0; y < m; y++) for (let x = 0; x < n; x++) re[y * n + x] = (gray[y * n + x] - mean) * wm[y] * wn[x];
  const rr = new Float64Array(n), ri = new Float64Array(n);
  for (let y = 0; y < m; y++) {
    rr.set(re.subarray(y * n, y * n + n)); ri.set(im.subarray(y * n, y * n + n));
    fft1d(rr, ri);
    re.set(rr, y * n); im.set(ri, y * n);
  }
  const cr = new Float64Array(m), ci = new Float64Array(m);
  for (let x = 0; x < n; x++) {
    for (let y = 0; y < m; y++) { cr[y] = re[y * n + x]; ci[y] = im[y * n + x]; }
    fft1d(cr, ci);
    for (let y = 0; y < m; y++) { re[y * n + x] = cr[y]; im[y * n + x] = ci[y]; }
  }
  // fftshift + power
  const p = new Float64Array(m * n);
  for (let y = 0; y < m; y++) for (let x = 0; x < n; x++) {
    const sy = (y + (m >> 1)) % m, sx = (x + (n >> 1)) % n;
    const k = y * n + x;
    p[sy * n + sx] = re[k] * re[k] + im[k] * im[k];
  }
  return p;
}

/** Equation 3 on a precomputed spectrum. */
export function vniRawFromSpectrum(p, m, n, radiusFactor = 0.15) {
  const rl = (radiusFactor * Math.min(m, n)) / 2;
  let num = 0, den = 0;
  for (let y = 0; y < m; y++) for (let x = 0; x < n; x++) {
    const r = Math.hypot(y - (m >> 1), x - (n >> 1));
    if (r > 0) { den += p[y * n + x]; if (r > rl) num += p[y * n + x]; }
  }
  return den > 0 ? num / den : 0;
}

export const vniRaw = (gray, m, n, radiusFactor = 0.15, removeMean = false) =>
  vniRawFromSpectrum(powerSpectrum(gray, m, n, removeMean), m, n, radiusFactor);

/** Equation 4 and Table 3.7. */
export function normalizeVni(raw, q01, q99, bands) {
  const v = Math.min(1, Math.max(0, (raw - q01) / (q99 - q01)));
  const band = (bands.find((b) => v <= b.max) || bands[bands.length - 1]).label;
  return { value: v, band };
}

// ------------------------------------------------------------------ catenary (Equations 6, 8, 10)

export const catenary = (x, a, b, c) => a * Math.cosh((x - b) / a) + c;

/** Chord L, perpendicular sag delta and its point for the curve between xa and xb (y up). */
export function chordSag(a, b, c, xa, xb) {
  const ya = catenary(xa, a, b, c), yb = catenary(xb, a, b, c);
  const dx = xb - xa, dy = yb - ya;
  const L = Math.hypot(dx, dy);
  const m = dy / dx;
  const xs = b + a * Math.asinh(m);
  if (!(xa < xs && xs < xb)) return { L, delta: 0, xs: xa, ys: ya, t: 0, ya, yb };
  const ys = catenary(xs, a, b, c);
  const delta = Math.abs(dx * (ya - ys) - (xa - xs) * dy) / L;
  const t = ((xs - xa) * dx + (ys - ya) * dy) / (L * L);
  return { L, delta, xs, ys, t, ya, yb };
}

/** a such that a level span has midspan sag D: a (cosh(span / 2a) - 1) = D (bisection). */
export function aForSag(span, sag) {
  let lo = 1e-6, hi = (span * span) / (8 * sag) * 4 + 1;
  for (let i = 0; i < 200; i++) {
    const mid = 0.5 * (lo + hi);
    const d = mid * (Math.cosh(span / (2 * mid)) - 1);
    if (d > sag) lo = mid; else hi = mid;
  }
  return 0.5 * (lo + hi);
}

export const crs = (deltaRel, deltaLim, w = 1) => Math.min(10, (10 * deltaRel / deltaLim) * w);
export const level = (c, low = 3, high = 6) => (c < low ? "low" : c <= high ? "medium" : "high");
