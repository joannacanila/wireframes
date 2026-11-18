"""End-to-end browser check of a RUNNING WIREFRAME server (headless Chrome via Playwright).

    WIREFRAME_E2E_USER=admin WIREFRAME_E2E_PASSWORD=... python -m scripts.e2e_check --base http://127.0.0.1:8000 \
        --photo path/to/pole.jpg --out runs/e2e

Use a test server (a throw-away WIREFRAME_DATA_DIR), not production data: the check uploads one photo. The upload is
marked as a test record at the end, so it never counts in the dashboard, lists, map or exports.

Workflow checked: Field Screener upload -> processing -> result page (S12: summary and KPI bar against the API, image
controls, inventory tabs / quick filters / search / collapsed group, synchronised selection, the configured three-zone
meter, Measurement details, Methodology & Limitations, banner, Retake, New Inspection, narrower windows) -> PDF download and its counts against the API result -> admin sign-in -> Dashboard,
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
        kpi = dict(zip(pg.locator(".rx-kpi dt").all_inner_texts(), (int(x) for x in pg.locator(".rx-kpi dd.n").all_inner_texts()), strict=False))
        kpi = {k.title(): v for k, v in kpi.items()}
        assessed = sum(1 for w in wires if w.get("status") == "scored")
        dc = (api.get("diagnosis") or {}).get("counts") or {}
        pc = api.get("comparison") or {}
        if assessed:
            check(kpi.get("Assessed") == assessed, f"page counts {kpi} match the API ({assessed} assessed)")
        else:  # S4: no validated reference -> no severity anywhere, measurements only
            check(dc and kpi.get("Measured") == dc["measured"] and kpi.get("Visible Part Only") == dc["measured_partial"]
                  and kpi.get("Not Measured") == dc["not_measured"] and kpi.get("Assessed") == 0, f"KPI bar {kpi} matches diagnosis")
            summ = pg.inner_text(".rx-summary")
            sl = api.get("summary_lines") or []
            check(len(sl) >= 2 and all(line in summ for line in sl), "summary sentences are the server's (same as the PDF)")
            if dc.get("measured", 0) + dc.get("measured_partial", 0):
                shown = pg.evaluate("() => window.wfViewer ? window.wfViewer._shown().length : -1")
                check(shown == dc["measured"] + dc["measured_partial"], f"measured wires drawn on the photo ({shown})")
            check(api["hazard"]["verdict"] != "NOT AT RISK" and not api["hazard"]["verdict"].startswith("AT RISK"),
                  f"no unsupported pole verdict: {api['hazard']['verdict']}")
            check(all(w.get("reason_code") in ("no_validated_reference", "endpoint_unavailable", "class_uncertain")
                      for w in wires if w.get("status") == "not_assessed"), "not-assessed reasons are S4 codes")
        check(not any(w.get("status") != "scored" and w.get("level") for w in wires), "no severity on an unassessed wire")
        check(pg.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), "no horizontal page scroll at 1440 px")
        hdr = pg.evaluate("() => { const a = document.querySelector('.rx-actions').getBoundingClientRect(); return a.right > innerWidth * 0.6; }")
        check(hdr, "header actions sit on the right")
        top = pg.evaluate("() => document.querySelector('.rx-photo').getBoundingClientRect().top")
        check(top < 900, f"the viewer starts inside the first 1440 x 900 viewport ({top:.0f} px)")

        # image controls
        for v in ("all", "photo", "mask", "both", "problem"):
            b = pg.locator(f"[data-view={v}]")
            if b.is_enabled():
                b.click(); pg.wait_for_timeout(80)
                check(b.get_attribute("aria-checked") == "true", f"view {v} selects")
        z0 = pg.inner_text("#zoom-pct"); pg.click("[data-zoom=in]"); pg.wait_for_timeout(200); z1 = pg.inner_text("#zoom-pct")
        check(z0 != z1, f"zoom in updates the zoom readout ({z0} -> {z1})")
        pg.click("#opts-btn"); check(pg.locator("#opts").is_visible(), "Layers menu opens")
        pg.fill("#opacity", "40"); pg.dispatch_event("#opacity", "input")
        check(pg.inner_text("#opts output") == "40%", "overlay opacity changes")
        pg.click("[data-zoom=reset]"); pg.wait_for_timeout(250)
        check(pg.inner_text("#opts output") == "100%" and pg.get_attribute("[data-view=problem]", "aria-checked") == "true",
              "Reset restores the default view, opacity and zoom")
        pg.mouse.click(5, 300)  # closes the menu

        # inventory: tabs, quick filters with counts, search, clear, collapsed group, selection kept across filters
        meas = [w for w in wires if w.get("sag_ratio") is not None and w.get("status") in ("scored", "not_assessed")
                and (w.get("fit") or {}).get("endpoints")]
        unmeas = [w for w in wires if w not in meas]
        flat = [w for w in meas if (w.get("states") or {}).get("within_resolution")]
        chip = lambda k: int(pg.inner_text(f"[data-chip={k}] b") or -1)  # noqa: E731
        check(f"Measured {len(meas)}" in pg.inner_text("[data-tab=m]") and f"Not measured {len(unmeas)}" in pg.inner_text("[data-tab=u]"),
              "inventory tabs count measured / not measured")
        check(pg.locator("#inventory tbody tr[data-wire]").count() == len(meas), "every measured wire has a row (none dropped)")
        if dc:
            check(chip("full") == dc["measured"] and chip("clipped") == dc["measured_partial"] and chip("all") == len(meas),
                  f"quick-filter counts reconcile with the KPI bar (full {chip('full')}, clipped {chip('clipped')})")
        notable = sum(1 for w in meas if w["sag_ratio"] > 0.02)
        check(chip("notable") == notable, f"Notable Sag > 2% browse filter counts {notable}")
        if flat:
            check(f"Show {len(flat)} wire" in pg.inner_text("#grp-btn") and pg.locator("#inv-flat").is_hidden(),
                  f"{len(flat)} wires with no resolvable sag are collapsed, not removed")
            pg.click("#grp-btn"); check(pg.locator("#inv-flat").is_visible() and pg.locator("#inv-flat tr").count() == len(flat), "the collapsed group expands")
        if meas:
            w1 = max(meas, key=lambda w: w["sag_ratio"])
            y0 = pg.evaluate("() => scrollY")
            pg.evaluate(f"() => document.querySelector(`#inventory tr[data-wire='{w1['index']}']`).click()"); pg.wait_for_timeout(250)
            check(pg.evaluate("() => window.wfViewer.selected") == w1["index"] and str(w1["index"]) in pg.inner_text("#span-detail .rx-wire-head")
                  and pg.get_attribute(f"#inventory tr[data-wire='{w1['index']}']", "aria-selected") == "true",
                  "table, viewer and inspector share the selection")
            check(abs(pg.evaluate("() => scrollY") - y0) < 2, "selecting a row does not scroll the page")
            excl = "clipped" if (w1.get("states") or {}).get("coverage") == "full_span" else "full"
            pg.click(f"[data-chip={excl}]"); pg.wait_for_timeout(100)
            check(pg.evaluate("() => window.wfViewer.selected") == w1["index"] and pg.locator("#inv-hint").is_visible()
                  and str(w1["index"]) in pg.inner_text("#span-detail .rx-wire-head"), "the selection survives a filter that hides it")
            rows = pg.locator("#inventory tbody tr[data-wire]:visible").all_inner_texts()
            want = "Edge Clipped" if excl == "clipped" else "Full Span"
            check(rows and all(want in t for t in rows), f"quick filter {excl} shows only {want} rows")
            pg.click("#inv-clear"); pg.wait_for_timeout(100)
            check(pg.get_attribute("[data-chip=all]", "aria-pressed") == "true" and pg.locator("#inv-clear").is_hidden(), "Clear filters resets")
            sid = f"SP-{w1['index']:03d}"
            pg.fill("#inv-q", sid); pg.wait_for_timeout(100)
            check(pg.locator("#inventory tbody tr[data-wire]:visible").count() == 1, f"search finds {sid}")
            pg.fill("#inv-q", ""); pg.dispatch_event("#inv-q", "input")
            # a selection made on the photo reveals its row (switching tab, clearing filters, opening the group)
            target = (unmeas or flat or meas)[0]
            pg.click("[data-chip=notable]") if chip("notable") else None
            pg.evaluate(f"() => window.wfViewer.onSelect({target['index']})"); pg.wait_for_timeout(250)
            check(pg.locator(f"#inventory tr[data-wire='{target['index']}']").is_visible()
                  and pg.get_attribute(f"#inventory tr[data-wire='{target['index']}']", "aria-selected") == "true",
                  f"a wire picked on the photo is revealed and selected in the inventory ({target['index']})")
            pg.click("[data-tab=m]")

        # the meter (D-28): power lines on the configured three zones, other types in their colour with no comparison
        scale = pc.get("meter_scale_max") or 0.1

        def meter_of(w):
            tab = "m" if w in meas else "u"  # the row lives in its own tab
            pg.evaluate(f"() => {{ document.querySelector('[data-tab={tab}]').click(); const t = document.querySelector('#inventory tr[data-wire=\"{w['index']}\"]'); t.closest('tbody').hidden = false; t.click(); }}")
            pg.wait_for_timeout(200)
            return pg.evaluate("""() => { const m = document.querySelector('#span-detail .rx-meter'); if (!m) return null;
              const d = m.querySelector('.rx-meter-dot'); return { cls: m.className, zones: m.querySelectorAll('.rx-meter-track .z').length,
              ref: !!m.querySelector('.rx-meter-ref'), dot: d ? parseFloat(d.style.left) : null,
              legend: m.querySelector('.rx-zones')?.innerText || '', card: document.querySelector('#span-detail').innerText }; }""")
        seen = []
        pick = sorted(meas, key=lambda w: -w["sag_ratio"])
        pick = [*pick[:2], *[w for w in pick[2:] if (w.get("comparison") or {}).get("position")][:1]]
        for w in pick if not assessed else []:
            m = meter_of(w)
            c = w.get("comparison") or {}
            want = min(100, w["sag_ratio"] / scale * 100)
            seen.append(m and m["dot"])
            if c.get("position"):
                check(bool(m) and f"cmp {c['position']}" in m["cls"] and m["zones"] == 3 and m["ref"] and abs(m["dot"] - want) < 0.5
                      and "Reference-tolerance region" in m["legend"] and "not severity classes" in m["legend"] and "Critical" not in m["card"]
                      and "Reference\n" in m["card"] and "Difference" in m["card"],
                      f"power line {w['index']}: three configured zones, 3% tick, dot {m and m['dot']:.1f}% (expected {want:.1f}%), {c['position']}")
            else:
                check(bool(m) and "type" in m["cls"] and m["zones"] == 0 and not m["ref"] and abs(m["dot"] - want) < 0.5
                      and "No applicable comparison" in m["card"] and m["card"].count("Unassessed (No Validated Reference)") == 1
                      and "Difference" not in m["card"],
                      f"{w['class']} {w['index']}: colourful meter, no comparison, one status, dot {m and m['dot']:.1f}%")
        check(len(set(seen)) == len(seen), f"sag meter follows the selected wire ({seen})")
        if pick and not assessed:
            pg.click("#span-detail .rx-md summary")
            check("Traced fraction" in pg.inner_text("#span-detail .rx-md") and "Pixel resolution" in pg.inner_text("#span-detail .rx-md"),
                  "Measurement details accordion opens")
        if unmeas and [w for w in unmeas if w.get("status") != "service_drop"]:
            m = meter_of([w for w in unmeas if w.get("status") != "service_drop"][0])
            check(bool(m) and "empty" in m["cls"] and m["dot"] is None, "empty meter for an unmeasured wire")

        # methodology accordion, banner
        check(pg.locator("#methodology").get_attribute("open") is None, "Methodology & Limitations is folded by default")
        pg.click("#methodology > summary")
        body = pg.inner_text("#methodology")
        check(len(api.get("methodology") or []) == 11 and all(t["title"] in body for t in api["methodology"])
              and pg.locator("#methodology wf-notice").count() == 1, "Methodology & Limitations holds the 11 topics and the notice")
        if pg.locator("#banner-x").count():
            pg.click("#banner-x")
            pg.reload(); pg.wait_for_selector(".rx-summary"); pg.wait_for_timeout(800)
            check(pg.locator("#rx-banner").count() == 0, "dismissed banner stays dismissed for this result")
        pg.screenshot(path=str(a.out / "result_s12.png"), full_page=True)
        if meas and not assessed:
            # the lit three-zone state of an ASSESSED wire, as drawn when the server sends severity bands: a synthetic
            # response routed to this page only (rendering check; nothing is stored)
            w0 = max(meas, key=lambda w: w["sag_ratio"]); lim = w0["sag_ratio"] * 0.8
            fake = {**api, "wires": [{**w, "status": "scored", "level": "high", "delta_lim": lim, "crs": 10.0,
                                      "sag_bands": {"minor_from": lim * 0.5, "critical_from": lim, "critical_inclusive": True}}
                                     if w is w0 else w for w in wires]}
            lit = ctx.new_page()
            lit.route(f"**/api/analysis/{aid}/result", lambda route: route.fulfill(json=fake))
            lit.goto(f"{a.base}/workspace/result/{aid}"); lit.wait_for_selector("#span-detail .rx-meter", timeout=30_000)
            z = lit.evaluate("""() => [...document.querySelectorAll('#span-detail .rx-meter-track .z')].map(e => [e.className, e.getBoundingClientRect().width])""")
            check([c for c, _ in z] == ["z z-low", "z z-medium", "z z-high"] and all(wd > 0 for _, wd in z),
                  f"an assessed wire's meter draws its three zones ({[round(wd) for _, wd in z]} px)")
            lit.close()
        # narrower desktop windows: no horizontal scroll
        for vw in (1280, 1024, 800):
            pg.set_viewport_size({"width": vw, "height": 800}); pg.wait_for_timeout(300)
            check(pg.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), f"no horizontal page scroll at {vw} px")
        pg.set_viewport_size({"width": 1440, "height": 900})
        # actions: Retake opens the form linked to this result; New Inspection returns to an empty form
        pg.click("#retake"); pg.wait_for_selector("#retake-note", timeout=10_000)
        check(pg.locator("#retake-note").is_visible(), "Retake photo opens the upload form linked to this result")
        pg.goto(f"{a.base}/workspace/result/{aid}"); pg.wait_for_selector(".rx-summary")
        check(pg.get_attribute("#pdf", "href") == f"/api/reports/{aid}/pdf", "Download PDF Report links the report")
        pg.click("#again"); pg.wait_for_timeout(400)
        check(pg.url.endswith("/workspace") and pg.locator("#idle").is_visible(), "New Inspection opens an empty form")

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
                k = (api.get("comparison") or {}).get("counts") or {}
                check("3.0% provisional, power lines" in text and "Measured Spans (not assessed)" in text
                      and ((f"{k['above']} above the reference, {k['at']} in the reference-tolerance region" if sum(k.values()) else "so none is compared") in text),
                      "PDF shows measurements, no severity, the power-line reference and the page's comparison counts")
                check("Figure 1. Measured wires (not assessed)" in text and "5.1 What the Analysis Found" in text,
                      "PDF draws measured wires and explains the analysis (S5)")
                check(bool(api.get("summary_lines")) and all(x in text for x in api["summary_lines"]) and "5.0 Methodology and Limitations" in text
                      and text.index("2.0 Recommended Field Actions") < text.index("3.0 Annotated Photographic Evidence"),
                      "PDF follows the page: same summary sentences, actions after the summary, one methodology section")
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
