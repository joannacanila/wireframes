"""End-to-end browser check of a RUNNING WIREFRAME server (headless Chrome via Playwright).

    WIREFRAME_E2E_USER=admin WIREFRAME_E2E_PASSWORD=... python -m scripts.e2e_check --base http://127.0.0.1:8000 \
        --photo path/to/pole.jpg --out runs/e2e

Use a test server (a throw-away WIREFRAME_DATA_DIR), not production data: the check uploads one photo. The upload is
marked as a test record at the end, so it never counts in the dashboard, lists, map or exports.

Workflow checked: Field Screener upload -> processing -> result page (summary, wire list, severity labels, location
block when GPS exists) -> PDF download and its counts against the API result -> admin sign-in -> Dashboard,
Inspections (filters, technical exports), the inspection page (diagnostics) and Map & Dispatch -> the removed
System page answers 404. Page errors and failed requests are collected; the exit code is 1 if any check fails.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--photo", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("runs/e2e"))
    a = ap.parse_args()
    user, pw = os.environ.get("WIREFRAME_E2E_USER"), os.environ.get("WIREFRAME_E2E_PASSWORD")
    if not (user and pw):
        sys.exit("set WIREFRAME_E2E_USER and WIREFRAME_E2E_PASSWORD (an administrator on the test server)")
    from playwright.sync_api import sync_playwright
    a.out.mkdir(parents=True, exist_ok=True)
    fails, errors = [], []

    def check(ok: bool, what: str) -> None:
        print(("PASS " if ok else "FAIL ") + what)
        if not ok:
            fails.append(what)

    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome", headless=True)
        ctx = br.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
        pg.on("response", lambda r: r.status >= 500 and errors.append(f"HTTP {r.status} {r.url}"))

        # Field Screener: anonymous upload
        pg.goto(f"{a.base}/workspace")
        pg.set_input_files("#file", str(a.photo))
        pg.click("#submit")
        pg.wait_for_url("**/workspace/result/*", timeout=180_000)
        aid = pg.url.rstrip("/").split("/")[-1]
        pg.wait_for_selector(".rx-summary", timeout=180_000)
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(a.out / "result.png"), full_page=True)
        title = pg.inner_text("#rx-title")
        check(title.startswith("Inspection summary"), f"result summary header: {title!r}")
        api = pg.request.get(f"{a.base}/api/analysis/{aid}/result").json()
        wires = api.get("wires") or []
        n = {k: sum(1 for w in wires if w.get("level") == k) for k in ("high", "medium", "low")}
        counts = {t: int(v) for t, v in zip(pg.locator(".rx-counts dt").all_inner_texts(), pg.locator(".rx-counts dd").all_inner_texts(), strict=False)}
        assessed = sum(1 for w in wires if w.get("status") == "scored")
        if assessed:
            check(counts.get("Critical") == n["high"] and counts.get("Minor sag") == n["medium"]
                  and counts.get("Within reference") == n["low"], f"page counts {counts} match API {n}")
        else:  # S4: no validated reference -> no severity anywhere, measurements only
            dc = (api.get("diagnosis") or {}).get("counts") or {}
            check(dc and counts.get("Measured") == dc["measured"] and counts.get("Visible part only") == dc["measured_partial"]
                  and counts.get("Not measured") == dc["not_measured"] and counts.get("Assessed") == 0
                  and not {"Critical", "Minor sag", "Within reference"} & set(counts), f"page counts {counts} match diagnosis (S5)")
            # S5: the diagnostic view: measured wires drawn, one selected with its explanation
            if dc.get("measured", 0) + dc.get("measured_partial", 0):
                shown = pg.evaluate("() => window.wfViewer ? window.wfViewer._shown().length : -1")
                check(shown == dc["measured"] + dc["measured_partial"], f"measured wires drawn on the photo ({shown})")
                check(pg.locator("#span-detail .rx-diag-wire li").count() >= 3, "selected wire shows its diagnosis")
                check(pg.locator(".rx-diag").count() == 1 and "What would complete" in pg.inner_text(".rx-diag"),
                      "'What the analysis found' section present")
            check(api["hazard"]["verdict"] != "NOT AT RISK" and not api["hazard"]["verdict"].startswith("AT RISK"),
                  f"no unsupported pole verdict: {api['hazard']['verdict']}")
            check(all(w.get("reason_code") in ("no_validated_reference", "endpoint_unavailable", "class_uncertain")
                      for w in wires if w.get("status") == "not_assessed"), "not-assessed reasons are S4 codes")
        check(not any(w.get("status") != "scored" and w.get("level") for w in wires), "no severity on an unassessed wire")
        check(pg.locator("[data-view=mask]").count() == 1, "Mask view present")

        # S7: the v2.21 sag meter (register D-25). Without a validated reference it is unlit: no zones, the dot at the
        # wire's image ratio on one scale per photo; an unmeasured wire gets an empty track; it follows the selection.
        meas = [w for w in wires if w.get("sag_ratio") is not None and w.get("status") in ("scored", "not_assessed")
                and (w.get("fit") or {}).get("endpoints")]
        unmeas = [w for w in wires if w.get("sag_ratio") is None and w.get("status") not in ("service_drop", "scored")]
        if meas and not assessed:
            scale = max(w["sag_ratio"] + (w.get("sag_ratio_resolution") or 0) for w in meas) * 1.3

            def meter_of(w):
                pg.evaluate(f"() => document.querySelector('li[data-wire=\"{w['index']}\"]').click()")
                pg.wait_for_timeout(250)
                return pg.evaluate("""() => { const m = document.querySelector('#span-detail .rx-meter'); if (!m) return null;
                  const d = m.querySelector('.rx-meter-dot'); return { cls: m.className, zones: m.querySelectorAll('.rx-meter-track .z').length,
                  ref: !!m.querySelector('.rx-meter-ref'), dot: d ? parseFloat(d.style.left) : null,
                  key: m.querySelector('.rx-meter-key').innerText, legend: m.querySelector('.rx-zones')?.innerText || '' }; }""")
            seen = []
            for w in sorted(meas, key=lambda w: -w["sag_ratio"])[:2]:
                m = meter_of(w)
                want = min(100, w["sag_ratio"] / scale * 100)
                seen.append(m and m["dot"])
                check(bool(m) and "unlit" in m["cls"] and m["zones"] == 0 and not m["ref"] and abs(m["dot"] - want) < 0.5
                      and f"{w['sag_ratio'] * 100:.1f}%" in m["key"] and "Zones not defined" in m["legend"],
                      f"sag meter unlit for wire {w['index']}: dot {m and m['dot']:.1f}% (expected {want:.1f}%), no zones, no reference")
            check(len(set(seen)) == 2, f"sag meter follows the selected wire ({seen})")
            if unmeas:
                m = meter_of(unmeas[0])
                check(bool(m) and "empty" in m["cls"] and m["dot"] is None and "Not measured" in m["key"],
                      f"sag meter empty for unmeasured wire {unmeas[0]['index']}")
            # the lit three-zone state, as drawn when the server sends a reference: a synthetic response for this page
            # only (rendering check, not a reference; nothing is stored)
            w0 = max(meas, key=lambda w: w["sag_ratio"]); lim = w0["sag_ratio"] * 0.8
            fake = {**api, "wires": [{**w, "status": "scored", "level": "high", "delta_lim": lim, "crs": 10.0,
                                      "sag_bands": {"minor_from": lim * 0.5, "critical_from": lim, "critical_inclusive": True}}
                                     if w is w0 else w for w in wires]}
            lit = ctx.new_page()
            lit.route(f"**/api/analysis/{aid}/result", lambda route: route.fulfill(json=fake))
            lit.goto(f"{a.base}/workspace/result/{aid}"); lit.wait_for_selector("#span-detail .rx-meter", timeout=30_000)
            z = lit.evaluate("""() => [...document.querySelectorAll('#span-detail .rx-meter-track .z')].map(e => [e.className, e.getBoundingClientRect().width])""")
            check([c for c, _ in z] == ["z z-low", "z z-medium", "z z-high"] and all(wd > 0 for _, wd in z)
                  and lit.locator("#span-detail .rx-meter-ref").count() == 1 and lit.locator("#span-detail .rx-zones.unlit").count() == 0 and lit.locator("#span-detail .rx-zones").count() == 1,
                  f"sag meter draws its three zones when a reference is supplied ({[round(wd) for _, wd in z]} px)")
            lit.screenshot(path=str(a.out / "meter_lit_synthetic.png"), full_page=False)
            lit.close()
            meter_of(max(meas, key=lambda w: w["sag_ratio"]))

        # PDF: same counts as the API
        r = pg.request.get(f"{a.base}/api/reports/{aid}/pdf")
        check(r.status == 200 and r.body()[:4] == b"%PDF", "PDF report downloads")
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(io.BytesIO(r.body()))
            text = " ".join(" ".join(doc[i].get_textpage().get_text_range() for i in range(len(doc))).split())
            doc.close()
            if assessed:
                check(f"{n['high']} Critical" in text and f"{n['medium']} Minor sag" in text, "PDF summary counts match the API")
            else:
                check("none validated" in text and "Measured Spans (not assessed)" in text, "PDF shows measurements, no severity (S4)")
                check("Figure 1. Measured wires (not assessed)" in text and "4.1 What the Analysis Found" in text,
                      "PDF draws measured wires and explains the analysis (S5)")
            check("preliminary visual screening" in text.lower(), "PDF carries the decision-support notice")
        except ImportError:
            print("SKIP PDF text check (pypdfium2 not installed)")

        # Admin portal
        pg.goto(f"{a.base}/admin/login")
        pg.fill("input[name=username]", user)
        pg.fill("input[name=password]", pw)
        pg.click("button[type=submit]")
        pg.wait_for_selector("#top:not([hidden])")
        pg.wait_for_selector(".kpis", timeout=60_000)
        dash = pg.inner_text("main")
        check("Assessment incomplete" in dash, "dashboard labels INCONCLUSIVE poles 'Assessment incomplete'")
        check(pg.locator("#user-menu a[href='/admin/system']").count() == 0, "no System entry in the user menu")
        pg.goto(f"{a.base}/admin/inspections")
        pg.wait_for_selector(".tbl-insp", timeout=60_000)
        pg.click("[data-tab=failed]")
        try:
            pg.wait_for_selector("[data-tab=failed][aria-selected=true]", timeout=15_000)
            ok = all(t in ("Processing failed", "Processing") for t in pg.locator(".tbl-insp td[data-label=Risk]").all_inner_texts())
        except Exception:
            ok = False
        check(ok, "Failed tab lists only failed or processing inspections")
        if pg.locator("#tx-btn").count():
            for href in pg.locator("#tx-pop a").evaluate_all("els => els.map(e => e.getAttribute('href'))"):
                check(pg.request.get(a.base + href).status == 200, f"technical export {href}")
        pg.goto(f"{a.base}/admin/inspections/{aid}")
        pg.wait_for_selector(".findings, .fail-card", timeout=60_000)
        if pg.locator("#diag summary").count():
            pg.click("#diag summary")
            check(pg.locator(".mcard").count() >= 1 or "No metric" in pg.inner_text("#diag"), "technical diagnostics open")
        pg.goto(f"{a.base}/admin/map")
        pg.wait_for_selector(".pcard, .ranks .empty", timeout=60_000)
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(a.out / "map.png"))
        check(pg.locator(".base-seg [data-base]").count() == 3, "base map switch (Light, Dark, Satellite)")
        check("Assessment incomplete" in pg.inner_text(".map-legend") and "Not assessed" not in pg.inner_text(".map-legend"),
              "map legend uses 'Assessment incomplete'")
        check(pg.request.get(f"{a.base}/admin/system").status == 404, "/admin/system answers 404")

        # keep the check out of operational records
        r = pg.request.put(f"{a.base}/api/admin/inspections/{aid}/record-kind", data={"record_kind": "test"},
                           headers={"origin": a.base})
        check(r.status == 200, "uploaded check photo marked as a test record")
        br.close()
    for e in errors:
        print("ERROR", e)
    print(f"{len(fails)} failed check(s), {len(errors)} page error(s)")
    return 1 if fails or errors else 0


if __name__ == "__main__":
    sys.exit(main())
