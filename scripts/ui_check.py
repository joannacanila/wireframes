"""Headless-browser check of the field screener at /workspace (v2.13), against a RUNNING server.

Real photos (needs the detection models on the server):

    python -m scripts.ui_check --base http://127.0.0.1:8000 --shots runs/ui_shots photos...

Mocked results (no model needed; the server only serves the page, assets and one sample analysis):

    python -m scripts.ui_check --mock --base http://127.0.0.1:8000 --shots runs/ui_shots \
        --out docs/thesis/results/ui_check_v213.json

--mock uploads the bundled synthetic sample with its reference polygons once, to get a genuine /result payload, and
derives four field scenarios from it (HAZARD DETECTED, REVIEW REQUIRED, NOT AT RISK with suppressed ground traces,
RETAKE PHOTO). Playwright intercepts /api/health, the upload, the status poll, /result, the wire-mask PNG and the PDF,
so the browser sees each scenario exactly as the API would deliver it. Every scenario is checked in the dark and the
light theme, at desktop (1440 x 900) and a narrow laptop window (1024 x 768); WIREFRAME is a desktop website and
does not support phones (v2.14):
  * no console error and no uncaught page error,
  * the shimmer skeleton is shown (and animating) while the analysis is processing,
  * the hero verdict: status pill text, worst CRS, measured vs total spans, one next action,
  * Original / Detected spans / Full wire mask ([data-view]) switch the viewer layers; the full mask loads,
  * the #opacity slider sets the viewer's overlay opacity (and is marked off in the Original view),
  * the #diag drawer is collapsed by default and opens / closes (aria-expanded, height),
  * every unmeasured span shows its reason; suppressed ground traces are listed in the diagnostics,
  * table -> canvas and canvas -> table selection (desktop),
  * key transitions last 200-300 ms, and there is no horizontal page scroll,
and saves a screenshot of each combination.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "app" / "web" / "assets" / "samples"
DESKTOP = {"width": 1440, "height": 900}
NARROW = {"width": 1024, "height": 768}  # smallest supported browser window (v2.14: no phone support)
MASK_URL = "/mock/wiremask.png"


# ---------------------------------------------------------------------------------------------- shared page checks

def _errors(page) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    return errors


def check_result(page, desktop: bool) -> dict:
    """Everything on a rendered result screen; returns measurements (pass/fail is decided by the caller)."""
    page.wait_for_selector("#verdict-pill", timeout=120_000)
    page.wait_for_function("window.wfViewer && window.wfViewer.image", timeout=30_000)
    row: dict = {"url": page.url}
    row["pill"] = page.inner_text("#verdict-pill").strip()
    row["title"] = page.inner_text(".fx-outcome h2")
    row["next_action"] = page.inner_text("#next-action li").strip()
    row["assessment"] = page.inner_text(".fx-outcome")
    row["limitations"] = [x.get_attribute("data-limit") for x in page.query_selector_all("#limits li[data-limit]")]
    row["heading"] = page.inner_text(".fx-insp h1")
    row["subline"] = page.inner_text(".fx-insp p")
    row["kpis"] = [k.inner_text().replace("\n", " ") for k in page.query_selector_all(".fx-counts > div")]
    row["hazards"] = [x.inner_text() for x in page.query_selector_all(".fx-hazards li")]
    rows = page.query_selector_all("tr[data-wire]")
    row["table_rows"] = len(rows)
    row["unmeasured_without_reason"] = page.evaluate(
        "[...document.querySelectorAll('tr[data-group=none][data-wire]')].filter(tr => !(tr.querySelector('td.find')?.textContent || '').trim()).length")
    # v2.14: the full reason and next step live in the span detail panel; check it for the first unmeasured row
    row["detail_shows_reason"] = None
    none_row = page.query_selector("tr[data-group=none][data-wire]")
    if none_row:
        none_row.click()
        row["detail_shows_reason"] = bool(page.query_selector("#span-detail .fx-finding"))
    # the retake step of every unmeasured span, as the detail panel shows it when that span is selected
    row["actions_shown"] = page.evaluate("""() => { let n = 0;
        for (const tr of document.querySelectorAll('tr[data-group=none][data-wire]')) {
          if (tr.getAttribute('aria-selected') !== 'true') tr.click();
          n += document.querySelectorAll('#span-detail .fx-do').length; tr.click(); }
        return n; }""")

    # ---- view presets: Original | Spans | Mask | Overlay
    views = {}
    for name in ("photo", "mask", "overlay", "spans"):  # end on the default view
        b = page.query_selector(f'[data-view="{name}"]')
        if b.is_disabled():
            views[name] = "disabled"
            continue
        b.click()
        page.wait_for_timeout(150)
        st = page.evaluate("""() => { const v = window.wfViewer, L = v.layers;
            return { checked: document.querySelector('[data-view][aria-checked=true]').dataset.view,
                     overlays: L.overlays, masks: L.masks, wiremask: L.wiremask, maskLoaded: !!v.wireMask,
                     inferred: L.inferred, opOff: document.querySelector('#opacity-wrap').hasAttribute('data-off') }; }""")
        if name in ("mask", "overlay"):
            page.wait_for_function("!!window.wfViewer.wireMask", timeout=15_000)
            st["maskLoaded"] = True
        ok = st["checked"] == name and {
            "photo": not st["overlays"] and st["opOff"],
            "spans": st["overlays"] and st["masks"] and st["inferred"] and not st["wiremask"] and not st["opOff"],
            "mask": st["overlays"] and st["wiremask"] and not st["masks"] and st["maskLoaded"],
            "overlay": st["overlays"] and st["wiremask"] and st["masks"] and st["maskLoaded"],
        }[name]
        views[name] = "ok" if ok else f"bad: {st}"
    row["views"] = views

    # ---- opacity slider
    page.eval_on_selector("#opacity", "el => { el.value = 40; el.dispatchEvent(new Event('input', {bubbles: true})); }")
    op = page.evaluate("({o: window.wfViewer.opacity, out: document.querySelector('#opacity-wrap output').textContent})")
    page.eval_on_selector("#opacity", "el => { el.value = 100; el.dispatchEvent(new Event('input', {bubbles: true})); }")
    row["opacity"] = "ok" if abs((op["o"] or 0) - 0.4) < 1e-9 and op["out"] == "40%" else f"bad: {op}"

    # ---- layers menu: an independent layer switches in the viewer and un-checks the preset (v2.14)
    page.click("#layers-btn")
    page.click('#layers-menu [data-l="skeleton"]')
    lay = page.evaluate("""() => ({ on: window.wfViewer.layers.skeleton,
        preset: !!document.querySelector('[data-view][aria-checked=true]'),
        box: document.querySelector('#layers-menu [data-l=skeleton]').checked })""")
    page.keyboard.press("Escape")
    page.mouse.click(5, 5)
    row["layers"] = "ok" if lay["on"] and lay["box"] and not lay["preset"] else f"bad: {lay}"

    # ---- zoom, fit, reset
    z0 = page.evaluate("window.wfViewer.view.s")
    page.click('[data-zoom="in"]')
    z1 = page.evaluate("window.wfViewer.view.s")
    pct = page.inner_text("#zoom-pct")
    page.click('[data-zoom="reset"]')
    page.wait_for_timeout(100)
    rs = page.evaluate("""() => ({ s: window.wfViewer.view.s, sel: window.wfViewer.selected, skel: window.wfViewer.layers.skeleton,
        view: (document.querySelector('[data-view][aria-checked=true]') || {}).dataset?.view })""")
    row["zoom"] = "ok" if z1 > z0 * 1.3 and pct.endswith("%") and abs(rs["s"] - z0) < 1e-6 and rs["sel"] is None \
        and not rs["skel"] and rs["view"] == "spans" else f"bad: {z0, z1, pct, rs}"

    # ---- diagnostics drawer
    def drawer():
        return page.evaluate("""() => { const d = document.querySelector('#diag');
            return { open: d.hasAttribute('data-open'), expanded: d.querySelector('.fx-drawer-btn').getAttribute('aria-expanded'),
                     h: document.querySelector('#diag-body').getBoundingClientRect().height }; }""")
    closed0 = drawer()
    page.click("#diag .fx-drawer-btn")
    page.wait_for_timeout(450)
    opened = drawer()
    row["diag_text"] = page.inner_text("#diag-body")
    page.click("#diag .fx-drawer-btn")
    page.wait_for_timeout(450)
    closed1 = drawer()
    row["diag"] = "ok" if (not closed0["open"] and closed0["expanded"] == "false" and closed0["h"] < 2
                           and opened["open"] and opened["expanded"] == "true" and opened["h"] > 40
                           and not closed1["open"] and closed1["h"] < 2) else f"bad: {[closed0, opened, closed1]}"

    # ---- motion: the redesign's key transitions sit in the 200-300 ms band
    row["durations_ms"] = page.evaluate("""() => { const ms = (el, p) => { if (!el) return null;
            const s = getComputedStyle(el)[p].split(',').map(x => parseFloat(x) * (x.trim().endsWith('ms') ? 1 : 1000));
            return Math.max(...s); };
        return { drawer: ms(document.querySelector('.fx-drawer-body'), 'transitionDuration'),
                 stage: ms(document.querySelector('#result'), 'animationDuration') }; }""")

    # ---- table <-> canvas (desktop: the canvas is large enough to hit a 1-px wire reliably)
    if desktop and rows:
        target = rows[min(1, len(rows) - 1)]
        wid = int(target.get_attribute("data-wire"))
        target.click()
        row["table_to_canvas"] = page.evaluate("window.wfViewer.selected") == wid
        page.evaluate("window.wfViewer.reset()")
        page.wait_for_timeout(300)
        other = page.evaluate("""() => {
            const v = window.wfViewer, r = v.wrap.getBoundingClientRect();
            for (const w of v.wires) {
              if (w.index === v.selected || !(w.centerline && w.centerline.length)) continue;
              const [x, y] = w.centerline[Math.floor(w.centerline.length / 2)];
              const sx = r.left + x * v.view.s + v.view.x, sy = r.top + y * v.view.s + v.view.y;
              if (sx > r.left + 5 && sx < r.right - 5 && sy > r.top + 5 && sy < r.bottom - 5) return {i: w.index, x: sx, y: sy};
            }
            return null; }""")
        if other:
            page.mouse.click(other["x"], other["y"])
            page.wait_for_timeout(200)
            hit = page.evaluate("window.wfViewer.selected")
            sel = page.evaluate("(document.querySelector('tr[aria-selected=true]')||{}).dataset?.wire")
            row["canvas_to_table"] = hit is not None and sel is not None and int(sel) == hit
    row["horizontal_overflow"] = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    # the spans list must fit its panel: no sideways scrolling that would clip the reason / next step
    row["spans_overflow_px"] = page.evaluate(
        "(() => { const s = document.querySelector('#fx-scroll'); return s ? s.scrollWidth - s.clientWidth : 0; })()")
    # filtering must still hide rows ([hidden] has to win over any display rule on rows)
    if page.query_selector('[data-filter="none"]:not(:disabled)'):
        page.click('[data-filter="none"]')
        row["filter_hides_rows"] = page.evaluate("""() => [...document.querySelectorAll('tr[data-wire]')]
            .filter(tr => tr.dataset.group !== 'none').every(tr => tr.getBoundingClientRect().height === 0)""")
        page.click('[data-filter="all"]')
    page.evaluate("window.wfViewer.reset(); scrollTo(0, 0)")
    page.wait_for_timeout(150)
    return row


def _motion_ok(d: dict) -> bool:
    return all(v is not None and 200 <= v <= 300 for v in d.values())


# ---------------------------------------------------------------------------------------------- mock scenarios

def base_result(request, base: str) -> dict:
    """A genuine /result payload: the bundled synthetic sample analysed with its reference polygons."""
    up = request.post(f"{base}/api/analysis/upload", headers={"origin": base}, multipart={
        "file": {"name": "synthetic-pole.jpg", "mimeType": "image/jpeg", "buffer": (SAMPLE / "synthetic-pole.jpg").read_bytes()},
        "labels": {"name": "synthetic-pole.txt", "mimeType": "text/plain", "buffer": (SAMPLE / "synthetic-pole.txt").read_bytes()},
        "pole_code": "SYN-SAMPLE"})
    assert up.ok, up.text()
    aid = up.json()["id"]
    for _ in range(240):
        if request.get(f"{base}/api/analysis/{aid}").json()["status"] != "processing":
            break
        time.sleep(0.25)
    res = request.get(f"{base}/api/analysis/{aid}/result")
    assert res.ok, res.text()
    return res.json()


def wire_mask_png(r: dict) -> bytes:
    """An indexed-colour stand-in for WireNet's class mask: every span's polygon, in its class index."""
    from PIL import Image, ImageDraw

    from app.core.config import CLASSES
    w, h = r["image"]["width"], r["image"]["height"]
    img = Image.new("P", (w, h), 0)
    pal = [0, 0, 0, 255, 120, 60, 80, 170, 255, 200, 200, 200, 90, 200, 120] + [0] * (768 - 15)
    img.putpalette(pal)
    d = ImageDraw.Draw(img)
    for wire in r["wires"]:
        k = CLASSES.index(wire["class"]) + 1 if wire["class"] in CLASSES else 1
        for poly in wire.get("mask_polygons") or []:
            if len(poly) >= 3:
                d.polygon([tuple(p) for p in poly], fill=k)
    img.info["transparency"] = 0
    b = io.BytesIO()
    img.save(b, "PNG", transparency=0)
    return b.getvalue()


def _set_levels(r: dict, levels: dict[int, tuple[str, float]]) -> None:
    for w in r["wires"]:
        if w["index"] in levels:
            w["level"], w["crs"] = levels[w["index"]]
    sag = r["hazard"]["factors"]["sag"]
    for lv in ("low", "medium", "high"):
        sag[lv] = sorted(w["index"] for w in r["wires"] if w.get("level") == lv)
    sag["scored_spans"] = sum(1 for w in r["wires"] if w.get("crs") is not None)


def scenarios(base: dict) -> dict[str, tuple[dict, str]]:
    """(result payload, expected pill) per scenario, all derived from one genuine result."""
    b = copy.deepcopy(base)
    b["wire_mask"] = {"url": MASK_URL, "wire_pixels": 48211, "span_coverage": 0.91,
                      "pixels": {"power-distribution-lines": 30120, "telecom-cable": 18091, "Vegetation": 0}}
    b["telemetry"] = {**(b.get("telemetry") or {}), "model": "WireNet-R34 (mocked UI check)", "inference_s": 2.29}
    out = {}

    r = copy.deepcopy(b)
    r["hazard"]["verdict"] = "AT RISK - HIGH"
    _set_levels(r, {4: ("high", 7.91), 5: ("high", 8.62)})
    r["hazard"]["reasons"] = ["AT RISK - HIGH: sag in the high CRS band on 2 of 5 measurable spans (spans 4, 5)"]
    out["hazard"] = (r, "HIGH RISK FOUND")

    out["review"] = (copy.deepcopy(b), "MODERATE RISK FOUND")  # the sample's own verdict: AT RISK - MODERATE

    r = copy.deepcopy(b)
    r["hazard"]["verdict"] = "NOT AT RISK"
    _set_levels(r, {3: ("low", 1.7), 4: ("low", 1.9), 5: ("low", 2.2)})
    r["hazard"]["reasons"] = ["NOT AT RISK: no excess sag corroborated across 5 measurable spans"]
    r["suppressed_spans"] = [
        {"wire_class": "telecom-cable", "confidence": 0.62, "bbox": [210.0, 640.0, 1010.0, 702.0],
         "polygons": [[[210, 640], [1010, 690], [1010, 702], [210, 652]]], "ground_share": 0.94,
         "reason_code": "ground_structure",
         "reason": "Lies on the road or pavement: a kerb, manhole or pavement edge, not an overhead wire"},
        {"wire_class": "power-distribution-lines", "confidence": 0.55, "bbox": [520.0, 700.0, 760.0, 780.0],
         "polygons": [[[520, 700], [760, 700], [760, 780], [520, 780]]], "ground_share": 0.81,
         "reason_code": "ground_structure",
         "reason": "Lies on the road or pavement: a kerb, manhole or pavement edge, not an overhead wire"}]
    r["flags"] = [*r["flags"], "ground_structures_suppressed"]
    out["not_at_risk_suppressed"] = (r, "NO HIGH RISK FOUND")

    r = copy.deepcopy(b)
    r["hazard"]["verdict"] = "INCONCLUSIVE - RETAKE PHOTO"
    r["hazard"]["reasons"] = ["INCONCLUSIVE - RETAKE PHOTO: 3 of 5 spans could not be measured"]
    w, h = r["image"]["width"], r["image"]["height"]
    for wire in r["wires"]:
        if wire["index"] in (3, 4, 5):
            wire.update(status="fit_failed", fail_reason="chord_below_precision_gate", crs=None, level=None,
                        sag_ratio=None, reason_code="chord_below_precision_gate",
                        reason="Chord under 100 px: too short to measure sag reliably",
                        action=f"Upload the full-resolution photo (this one is {min(w, h)} px on its short side), "
                               "or move closer so the span looks longer")
    _set_levels(r, {1: ("low", 1.09), 2: ("low", 2.08)})
    r["quality"] = {**(r.get("quality") or {}), "grade": "D", "reliability": 0.41,
                    "reason": "Most spans are too short in this photo to measure",
                    "advice": "Retake the photo: frame the pole and both spans against the sky"}
    r["retake_guidance"] = (f"3 of 5 spans could not be measured. Best next photo: upload the full-resolution photo "
                            f"(this one is {min(w, h)} px on its short side), or move closer so the span looks longer.")
    out["retake"] = (r, "PHOTO NOT CLEAR ENOUGH")
    return out


class MockApi:
    """Playwright route handler: serves one scenario per page at /api/*; everything else goes to the server."""

    def __init__(self, base: str, payload: dict, mask: bytes, hold_polls: int = 2):
        self.base, self.payload, self.mask, self.hold, self.polls = base, payload, mask, hold_polls, 0
        self.id = payload["id"]

    def __call__(self, route):
        req, path = route.request, re.sub(r"^https?://[^/]+", "", route.request.url).split("?")[0]
        js = lambda body, status=200: route.fulfill(status=status, content_type="application/json", body=json.dumps(body))  # noqa: E731
        if path == "/api/health":
            return js({"status": "ok", "mode": "field", "model": {"ready": True}, "queue_depth": 0})
        if path == "/api/analysis/upload" and req.method == "POST":
            return js({"id": self.id, "status": "processing"}, 202)
        if path == f"/api/analysis/{self.id}":
            self.polls += 1
            done = self.polls > self.hold
            return js({"id": self.id, "status": self.payload["status"] if done else "processing"})
        if path == f"/api/analysis/{self.id}/result":
            return js(self.payload)
        if path == MASK_URL:
            return route.fulfill(status=200, content_type="image/png", body=self.mask)
        if path == f"/api/reports/{self.id}/pdf":
            return route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n%mock\n")
        return route.continue_()


def run_mock(browser, base: str, shots: Path) -> list[dict]:
    ctx0 = browser.new_context()
    real = base_result(ctx0.request, base)
    ctx0.close()
    mask = wire_mask_png(real)
    results = []
    for name, (payload, pill) in scenarios(real).items():
        for theme in ("dark", "light"):
            for vp_name, vp in (("desktop", DESKTOP), ("narrow", NARROW)):
                desktop = True  # both are desktop browser windows; there is no phone layout (v2.14)
                ctx = browser.new_context(viewport=vp, device_scale_factor=1)
                ctx.add_init_script(f"try {{ localStorage.setItem('wf-field-theme', '{theme}'); }} catch {{}}")
                p = copy.deepcopy(payload)
                p["id"] = f"{abs(hash((name, theme, vp_name))) % (1 << 64):016x}" * 2
                api = MockApi(base, p, mask, hold_polls=2)
                ctx.route(re.compile(r".*/(api|mock)/.*"), api)
                page = ctx.new_page()
                errors = _errors(page)
                page.goto(f"{base}/workspace")
                page.wait_for_function("document.documentElement.dataset.server === 'field'", timeout=15_000)
                page.set_input_files('input[name="file"]', str(SAMPLE / "synthetic-pole.jpg"))
                page.fill('input[name="inspection_name"]', "Mock inspection")
                page.click("#submit")
                # shimmer skeleton while processing
                page.wait_for_selector("#working:not([hidden])", timeout=10_000)
                shimmer = page.evaluate("""() => {
                    const names = document.getAnimations().filter(a => a.playState === 'running')
                        .map(a => a.animationName).filter(Boolean);
                    return { names: [...new Set(names)], busy: document.querySelector('#working').getAttribute('aria-busy'),
                             skeletons: document.querySelectorAll('#working .sk').length }; }""")
                if name == "hazard" and theme == "dark":
                    page.screenshot(path=str(shots / f"loader_{vp_name}.png"))
                row = check_result(page, desktop)
                html_theme = page.evaluate("document.documentElement.dataset.theme")
                bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
                page.screenshot(path=str(shots / f"{name}_{theme}_{vp_name}.png"), full_page=True)
                checks = {
                    "no_errors": not errors,
                    "shimmer": "fx-shimmer" in shimmer["names"] and shimmer["busy"] == "true" and shimmer["skeletons"] > 0,
                    "pill": row["pill"].upper() == pill,
                    "theme": html_theme == theme,
                    "risk_summary_three_levels": len(row["kpis"]) == 3 and "not assessed" not in " ".join(row["kpis"]).lower(),
                    "no_model_status_shown": "model ready" not in page.inner_text("body").lower(),
                    "layers": row["layers"] == "ok",
                    "zoom_fit_reset": row["zoom"] == "ok",
                    "detail_shows_reason": row["detail_shows_reason"] is not False,
                    "next_action": bool(row["next_action"]),
                    "views": all(v == "ok" for v in row["views"].values()),
                    "opacity": row["opacity"] == "ok",
                    "diag": row["diag"] == "ok",
                    "reasons": row["unmeasured_without_reason"] == 0,
                    "motion_200_300ms": _motion_ok(row["durations_ms"]),
                    "no_horizontal_scroll": not row["horizontal_overflow"],
                    "spans_table_fits": row["spans_overflow_px"] <= 1,
                }
                if "filter_hides_rows" in row:
                    checks["filter_hides_rows"] = row["filter_hides_rows"]
                if desktop and row["table_rows"]:
                    checks["table_to_canvas"] = bool(row.get("table_to_canvas"))
                    checks["canvas_to_table"] = bool(row.get("canvas_to_table", True))
                if name == "retake":
                    checks["retake_is_first_step"] = row["next_action"].startswith("Retake the photo")
                    checks["span_actions_shown"] = row["actions_shown"] >= 3
                if name == "not_at_risk_suppressed":
                    checks["not_a_certification"] = "not a safety inspection" in row["assessment"]
                    checks["suppressed_listed"] = ("ground structures ignored" in row["diag_text"].lower()
                                                   and "94% on road / pavement" in row["diag_text"])
                    checks["suppressed_count"] = row["diag_text"].count("on road / pavement") == 2
                rec = {"scenario": name, "theme": theme, "viewport": f"{vp['width']}x{vp['height']}",
                       "pill": row["pill"], "title": row["title"], "next_action": row["next_action"], "kpis": row["kpis"],
                       "views": row["views"], "durations_ms": row["durations_ms"], "shimmer": shimmer,
                       "body_background": bg, "errors": errors, "checks": checks, "pass": all(checks.values())}
                results.append(rec)
                print(json.dumps({k: rec[k] for k in ("scenario", "theme", "viewport", "pill", "pass")}
                                 | {"failed": [k for k, v in checks.items() if not v]}, ensure_ascii=False), flush=True)
                ctx.close()
    # the two themes must actually differ (both resolved, one token set each)
    bgs = {r["theme"]: r["body_background"] for r in results}
    results.append({"scenario": "theme tokens", "dark_bg": bgs.get("dark"), "light_bg": bgs.get("light"),
                    "pass": bool(bgs.get("dark")) and bgs.get("dark") != bgs.get("light")})
    return results


# ---------------------------------------------------------------------------------------------- real photos

def run_real(browser, base: str, shots: Path, photos: list[Path], name: str | None) -> list[dict]:
    results = []
    ctx = browser.new_context(viewport=DESKTOP, device_scale_factor=1)
    for ph in photos:
        page = ctx.new_page()
        errors = _errors(page)
        page.goto(f"{base}/workspace")
        page.set_input_files('input[name="file"]', str(ph))
        label = f"{name or 'UI check'} · {ph.stem}"
        page.fill('input[name="inspection_name"]', label)
        page.fill('input[name="city"]', "Test City")
        page.fill('input[name="barangay"]', "Test Barangay")
        page.click("#submit")
        row = check_result(page, True)
        row["name_saved"] = row["heading"] == label and ph.name in row["subline"] and "Test Barangay, Test City" in row["subline"]
        href = page.get_attribute("#pdf", "href")
        resp = page.request.get(base + href)
        row["pdf_ok"] = resp.status == 200 and resp.body()[:4] == b"%PDF"
        page.screenshot(path=str(shots / f"{ph.stem}.png"), full_page=True)
        row.update(photo=ph.name, errors=errors)
        row["pass"] = bool(not errors and row["pdf_ok"] and row["name_saved"] and row["unmeasured_without_reason"] == 0
                           and all(v in ("ok", "disabled") for v in row["views"].values())
                           and row["opacity"] == "ok" and row["diag"] == "ok" and row["layers"] == "ok" and row["zoom"] == "ok"
                           and row["detail_shows_reason"] is not False
                           and (row["table_rows"] == 0 or (row.get("table_to_canvas") and row.get("canvas_to_table", True))))
        results.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "diag_text"}), flush=True)
        page.close()
    if results:  # narrowest supported desktop window, last result
        mob = browser.new_context(viewport=NARROW, device_scale_factor=1)
        mp = mob.new_page()
        merr = _errors(mp)
        mp.goto(results[-1]["url"])
        mp.wait_for_selector("#verdict-pill", timeout=60_000)
        overflow = mp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        mp.screenshot(path=str(shots / "narrow.png"), full_page=True)
        results.append({"photo": "narrow window (1024 px)", "horizontal_overflow": overflow, "errors": merr,
                        "pass": not overflow and not merr})
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--shots", type=Path, default=ROOT / "runs" / "ui_shots")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--name", default=None, help="inspection name prefix for real-photo runs")
    ap.add_argument("--mock", action="store_true", help="mocked field scenarios; no detection model needed")
    ap.add_argument("--channel", default="chrome", help="Playwright browser channel (chrome, msedge, chromium)")
    ap.add_argument("photos", nargs="*", type=Path)
    a = ap.parse_args()
    a.shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=None if a.channel == "chromium" else a.channel, headless=True)
        if a.mock:
            results = run_mock(browser, a.base.rstrip("/"), a.shots)
        else:
            photos = a.photos or sorted((ROOT / "runs" / "demo_samples").glob("*"))
            results = run_real(browser, a.base.rstrip("/"), a.shots, photos, a.name)
        version = browser.version
        browser.close()
    ok = bool(results) and all(r["pass"] for r in results)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps({"all_pass": ok, "mode": "mock" if a.mock else "real", "browser": f"{a.channel} {version}",
                                     "n_checks": len(results), "results": results}, indent=1, ensure_ascii=False),
                         encoding="utf-8")
    print("ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
