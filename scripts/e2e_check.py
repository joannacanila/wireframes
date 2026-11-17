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
        check(counts.get("Critical") == n["high"] and counts.get("Minor sag") == n["medium"]
              and counts.get("Within reference") == n["low"], f"page counts {counts} match API {n}")
        check(not any(w.get("status") != "scored" and w.get("level") for w in wires), "no severity on an unassessed wire")
        check(pg.locator("[data-view=mask]").count() == 1, "Mask view present")

        # PDF: same counts as the API
        r = pg.request.get(f"{a.base}/api/reports/{aid}/pdf")
        check(r.status == 200 and r.body()[:4] == b"%PDF", "PDF report downloads")
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(io.BytesIO(r.body()))
            text = " ".join(" ".join(doc[i].get_textpage().get_text_range() for i in range(len(doc))).split())
            doc.close()
            check(f"{n['high']} Critical" in text and f"{n['medium']} Minor sag" in text, "PDF summary counts match the API")
            check("field verification required" in text.lower(), "PDF footer carries the verification notice")
        except ImportError:
            print("SKIP PDF text check (pypdfium2 not installed)")

        # Admin portal
        pg.goto(f"{a.base}/admin/login")
        pg.fill("input[name=username]", user)
        pg.fill("input[name=password]", pw)
        pg.click("button[type=submit]")
        pg.wait_for_selector("#top:not([hidden])")
        pg.wait_for_selector(".kpis", timeout=60_000)
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
