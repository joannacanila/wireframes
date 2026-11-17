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
        pg.wait_for_selector(".fx-summary", timeout=180_000)
        pg.wait_for_timeout(2000)
        pg.screenshot(path=str(a.out / "result.png"), full_page=True)
        title = pg.inner_text("#rx-title")
        check(bool(title.strip()) and bool(pg.inner_text("#verdict-pill").strip()), f"result summary header: {title!r}")
        api = pg.request.get(f"{a.base}/api/analysis/{aid}/result").json()
        wires = api.get("wires") or []
        n = {k: sum(1 for w in wires if w.get("level") == k) for k in ("high", "medium", "low")}
        counts = {t.strip(): int(v) for t, v in zip(pg.locator(".fx-counts dt").all_inner_texts(), pg.locator(".fx-counts dd").all_inner_texts(), strict=False)}
        assessed = sum(1 for w in wires if w.get("status") == "scored")
        measured = [w for w in wires if w.get("sag_ratio") is not None and w.get("status") in ("scored", "not_assessed")]
        if assessed:
            check(counts.get("Critical") == n["high"] and counts.get("Minor sag") == n["medium"]
                  and counts.get("Within reference") == n["low"], f"page counts {counts} match API {n}")
        else:  # S4: no validated reference -> no severity anywhere, measurements only
            dc = (api.get("diagnosis") or {}).get("counts") or {}
            check(dc and counts.get("Measured") == dc["measured"] and counts.get("Visible part only") == dc["measured_partial"]
                  and counts.get("Not measured") == dc["not_measured"] and dc["assessed"] == 0
                  and not {"Critical", "Minor sag", "Within reference"} & set(counts), f"page counts {counts} match diagnosis (S5)")
            check(f"{dc['assessed']} assessed for severity" in pg.inner_text(".fx-coverage"), "coverage line states 0 assessed")
            check(api["hazard"]["verdict"] != "NOT AT RISK" and not api["hazard"]["verdict"].startswith("AT RISK"),
                  f"no unsupported pole verdict: {api['hazard']['verdict']}")
            check(all(w.get("reason_code") in ("no_validated_reference", "endpoint_unavailable", "class_uncertain")
                      for w in wires if w.get("status") == "not_assessed"), "not-assessed reasons are S4 codes")
        check(not any(w.get("status") != "scored" and w.get("level") for w in wires), "no severity on an unassessed wire")

        # S6 (register D-24): the restored v2.18 result page. Every measured wire is drawn and listed whether or not a
        # severity exists, one is selected with its measurement and diagnosis, and every control does what it says.
        js = lambda e: pg.evaluate(e)  # noqa: E731
        shown = js("() => window.wfViewer._shown().map(w => w.index)")
        check(len(shown) == len(wires) and all(w["index"] in shown for w in measured), f"every wire drawn ({len(shown)} of {len(wires)})")
        check(js("() => { const L = window.wfViewer.layers; return L.overlays && L.masks && L.catenary && L.sag; }"),
              "Wires view draws masks, fitted curves and the sag measurement")
        check(pg.locator("tr[data-wire]").count() == len(wires), "every wire section in the table")
        rows = {int(r.get_attribute("data-wire")): r.inner_text() for r in pg.locator("tr[data-wire]").all()}
        check(all("Could not be measured" not in rows[w["index"]] for w in measured), "measured rows never say 'Could not be measured'")
        if measured:
            sel = js("() => window.wfViewer.selected")
            check(sel is not None, f"a wire is selected on opening ({sel})")
            if not assessed:
                check(pg.locator("#span-detail .fx-diag-wire li").count() >= 3 and "Image sag ratio" in pg.inner_text("#span-detail"),
                      "selected wire shows its measurement and diagnosis")
                check(pg.locator(".rx-diag").count() == 1 and "What would complete" in pg.inner_text(".rx-diag"),
                      "'What the analysis found' section present")
            pg.click("#span-detail .fx-more summary")
            check(pg.locator("#span-detail .fx-more[open] .fx-dl div").count() >= 3, "Measurement details expands")
            other = next((w["index"] for w in measured if w["index"] != sel), None)
            if other is not None:
                pg.click(f'tr[data-wire="{other}"]'); pg.wait_for_timeout(300)
                check(js("() => window.wfViewer.selected") == other and f">{other}<" in pg.inner_html("#span-detail header"),
                      "clicking a table row selects that wire")
        check(pg.locator("[data-view=mask]").count() == 1 and pg.locator("[data-view=overlay]").count() == 1, "Mask and Both views present")
        if api.get("wire_mask"):
            pg.click("[data-view=overlay]")
            check(js("() => window.wfViewer.layers.wiremask && window.wfViewer.layers.masks"), "Both view shows mask and wires")
        pg.click("[data-view=photo]")
        check(js("() => !window.wfViewer.layers.overlays"), "Original view hides the overlay")
        pg.click("#layers-btn"); pg.check('#layers-menu [data-l="skeleton"]')
        check(js("() => window.wfViewer.layers.skeleton && window.wfViewer.layers.overlays"), "Layers menu switches a layer")
        pg.keyboard.press("Escape")
        pg.locator("#opacity").fill("40")
        check(pg.inner_text("#opacity-wrap output") == "40%", "overlay opacity slider")
        before = pg.inner_text("#zoom-pct"); pg.click('[data-zoom="in"]'); pg.wait_for_timeout(200)
        check(pg.inner_text("#zoom-pct") != before, "zoom in changes the zoom level")
        pg.click('[data-zoom="reset"]'); pg.wait_for_timeout(200)
        check(js("() => window.wfViewer.layers.masks && !window.wfViewer.layers.skeleton && window.wfViewer.selected == null")
              and pg.inner_text("#opacity-wrap output") == "100%", "Reset restores the default view")
        for f in ("measured", "partial", "unmeasured", "high", "medium", "low"):
            b = pg.locator(f'[data-filter="{f}"]')
            if b.count() and not b.is_disabled():
                b.click()
                vis = pg.locator("tr[data-wire]:not([hidden])").count()
                want = pg.locator(f'tr[data-group="{f}"]').count()
                check(vis == want and vis == int(b.inner_text().split()[-1]), f"filter '{f}' shows its {want} rows")
        pg.click('[data-filter="all"]')
        lim = pg.inner_text("#limits")
        check("Photo limitations" in lim and pg.locator("#next-action li").count() >= 1, "photo limitations and next steps present")

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
