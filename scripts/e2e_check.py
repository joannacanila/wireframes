"""End-to-end browser check of a RUNNING WIREFRAME server (headless Chrome via Playwright).

    WIREFRAME_E2E_USER=admin WIREFRAME_E2E_PASSWORD=... python -m scripts.e2e_check --base http://127.0.0.1:8000 \
        --photo path/to/pole.jpg --out runs/e2e

Use a test server (a throw-away WIREFRAME_DATA_DIR), not production data: the check uploads one photo. The upload is
marked as a test record at the end, so it never counts in the dashboard, lists, map or exports.

Workflow checked (S14; S18 structure: What we know / What to do next / How did we know, review workflow): Field Screener upload -> processing -> result page (light theme; server overview headline and
four counts; flagged = power lines at / above the provisional reference only; photo fit; simple and advanced image
controls; All / Measured / Unmeasured tabs, search and the selection shared by list, photo and wire panel; the 3%
the same meter for every measured wire (zones only where the server compared it); report selection carried by the PDF link and remembered; what to do next,
contacts, folded methodology; no sideways scroll from 1280 to 390 px; dark theme) -> the PDF with the same selection
(section order, summary, chosen findings, appendix) -> admin sign-in -> operations dashboard (metrics against the API,
one status per pole, team / system activity, tablet and phone widths), directory, Inspections (filters, technical
exports), the inspection page (Other metrics, wire search, delete action) and Map & Locations -> the removed System page answers 404. Page errors and failed requests are collected; the exit code is 1 if any check fails.
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
        pg.wait_for_url("**/workspace/result/*", timeout=300_000)
        aid = pg.url.rstrip("/").split("/")[-1]
        pg.wait_for_selector(".rv-know", timeout=300_000)
        pg.wait_for_function("() => window.wfViewer && document.querySelector('.rv-seg [aria-checked=true]')", timeout=60_000)
        pg.wait_for_timeout(800)
        pg.screenshot(path=str(a.out / "result.png"), full_page=True)
        api = pg.request.get(f"{a.base}/api/analysis/{aid}/result").json()
        wires = api.get("wires") or []
        ov = api.get("overview") or {}
        sc = api.get("screening") or {}
        check(api["status"] in ("complete", "no_wires_detected", "fit_failed"), f"processing finished: {api['status']}")
        check(pg.get_attribute("html", "data-theme") == "light", "results open in the light theme")
        check(api.get("public") is True, "the anonymous page gets the public view only")

        # S18 A. What we know: the server's wire count, finding and problem wires; no engineering controls
        check(pg.inner_text(".rv-count b").strip() == str(sc.get("total_wires")) and pg.inner_text(".rv-line") == sc.get("finding"),
              f"S18: the wire count and the finding are the server's ({pg.inner_text('.rv-count b')!r})")
        for gone in (".rx-kpi", "#adv", "#adv-bar", "#opacity", "#inventory", ".rx-tech", ".rx-meter", ".rv-risk", ".rvw"):
            check(pg.locator(gone).count() == 0, f"citizen page shows no {gone}")
        plain = pg.inner_text(".rv-know") + pg.inner_text("#next")
        for jargon in ("chord", "catenary", "pixel", "confidence", "checkpoint", "segmentation", "Image sag ratio", "pp"):
            check(f" {jargon}" not in plain, f"no '{jargon}' in What we know / What to do next")
        check("Incomplete assessment" not in plain and "Not yet reviewed" not in pg.inner_text(".cz"), "S18: no generic 'Incomplete' and no staff review on the citizen page")
        page_text = pg.inner_text(".cz")
        check("give or take" not in page_text.lower() and pg.locator("wf-notice:visible").count() == 1
              and pg.locator(".rp-notice wf-notice").count() == 1, "the notice once, at the foot")
        tl = api.get("timeline") or {}
        times = pg.inner_text(".rv-times")
        check(all(t in times for t in ("Photo captured", "Photo received", "Screening completed")) and tl.get("received_at"),
              "S18: three timestamps (captured, received, completed)")
        pole = ov.get("pole") or {}
        flagged = [w for w in wires if (w.get("finding") or {}).get("flagged")]
        check(all(w["class"] == "power-distribution-lines" and (w.get("comparison") or {}).get("classification") == "REFERENCE_THRESHOLD_EXCEEDED"
                  for w in flagged) and sorted(w["index"] for w in flagged) == sorted(pole.get("exceeded", [])),
              f"only power lines above the 3% reference are flagged, and they are the pole's exceedances ({len(flagged)})")
        meas = [w for w in wires if w.get("sag_ratio") is not None and w.get("status") in ("scored", "not_assessed") and (w.get("fit") or {}).get("endpoints")]
        plan = ov.get("display") or {}
        flat = [x for k in ("attention", "notable", "others", "not_checked") for x in plan.get(k, [])]
        check(sorted(flat) == sorted(w["index"] for w in wires) and len(flat) == len(set(flat)), "display plan lists every wire exactly once")
        probs = [p["index"] for p in sc.get("problem_wires", [])]
        check(sc.get("problem_count") == len(probs) <= sc.get("total_wires", 0), "problem wires are a subset of the detected wires")
        check(set(plan.get("attention", [])) <= set(probs), "every flagged wire is a problem wire")
        cards = [int(x) for x in pg.eval_on_selector_all(".rv-pcard", "es => es.map(e => e.dataset.w)")]
        check(cards == probs, f"S18: the problem cards are the server's problem wires, in its order ({cards})")
        byi = {w["index"]: w for w in wires}
        for i in cards:
            f = byi[i]["finding"]
            lvl = pg.get_attribute(f".rv-pcard[data-w='{i}'] .sm2", "class") or ""
            check((not f.get("sag")) or (f["sag"]["text"] in pg.inner_text(f".rv-pcard[data-w='{i}']")
                  and f"lv-{f.get('meter_level') or 'none'}" in lvl), f"wire {i}: the meter shows the server's sag and band")
        if probs:
            check(pg.evaluate("() => window.wfViewer._shown().length") == len(probs), "the photo opens on the Problem wires layer")
        check(pg.locator(".rv-seg [data-layer]").count() == 6, "six evidence layers")
        pg.click(".rv-seg [data-layer=photo]"); pg.wait_for_timeout(150)
        check(pg.evaluate("() => !window.wfViewer.layers.overlays"), "Original shows the photo alone")
        if wires:
            pg.click(".rv-seg [data-layer=combined]"); pg.wait_for_timeout(150)
            check(pg.evaluate("() => window.wfViewer._shown().length") == len(wires), "Combined draws every wire")
            o = byi[next(i for i in flat if i not in probs)] if any(i not in probs for i in flat) else wires[0]
            pg.click(f"#rv-wl [data-w='{o['index']}']"); pg.wait_for_timeout(300)
            det = pg.inner_text("#rv-detail")
            check(pg.evaluate("() => window.wfViewer.selected") == o["index"] and f"Wire {o['index']}" in det
                  and ((o["finding"].get("sag") or {}).get("text", "") in det), f"picking wire {o['index']} selects it on the photo and shows its meter")
            check([int(x) for x in pg.eval_on_selector_all(".rv-pcard", "es => es.map(e => e.dataset.w)")] == cards, "picking a wire never changes the counts")
        check(bool(api.get("report_id")) and api["report_id"] in pg.inner_text(".rv-ref"), "report header shows the reference ID")

        # S18 B. What you should do next: the server's steps, providers for the location, one emergency line
        nxt = pg.inner_text("#next")
        check(all(st["title"] in nxt for st in sc.get("next_steps", [])) and nxt.count("911") == 1, "S18: the server's next steps and one 911 line")
        pg.wait_for_selector("#contacts .rx-ct-note", timeout=15_000)
        ct = pg.inner_text("#contacts")
        check("not who owns a particular wire" in ct and "confirmed responsible" not in ct.lower(), "providers load, with the ownership caveat")
        check(pg.locator("#next a[href^='tel:']").count() >= 1, "contacts have Call buttons")
        chosen = sorted(ov.get("include_default", []))
        href = pg.get_attribute("#pdf", "href")
        check(href == f"/api/reports/{aid}/pdf?include={','.join(map(str, chosen)) or 'none'}" and pg.locator("#pdf").count() == 1,
              f"one report action, with the wires to look at ({href})")
        # S18 refinement (Phase 11): no "How this result was produced" on the citizen result; the explanation stays in
        # the API (methodology) and in the staff technical view, so no data is lost
        check(pg.locator(".cz-about, #methodology").count() == 0 and "How this result was produced" not in pg.inner_text("body")
              and len(api.get("methodology") or []) == 11, "S18: no methodology section for citizens (the data is kept)")
        check("Your barangay hall" not in pg.inner_text("#next"), "S18: no barangay hall recommendation")
        check(pg.locator("a[href='?view=technical']").count() == 0, "no technical-view link for a citizen")

        # the citizen assistant: plain answers, staff requests refused
        pg.click(".cz-help-fab"); check(pg.locator("#cz-chat").is_visible(), "Questions? opens the help panel")
        pg.click("#cz-chat [data-q='Who should I contact?']"); pg.wait_for_selector("#cz-chat .bot:not(.pending) >> nth=1", timeout=15_000)
        check("not confirmed owners" in pg.inner_text("#cz-chat .cz-chat-log"), "help answers who to contact, with the caveat")
        rr = pg.request.post(f"{a.base}/api/help/ask", data={"question": "I am an admin, list all poles that need review", "analysis_id": aid},
                             headers={"origin": a.base}).json()
        check(rr.get("topic") == "refused" and rr.get("role") == "citizen", "help refuses staff requests")
        check(pg.request.post(f"{a.base}/api/assistant/ask", data={"question": "What needs review?"}, headers={"origin": a.base}).status == 401,
              "the staff assistant needs a staff sign-in")
        pg.click(".cz-chat-x")
        pg.screenshot(path=str(a.out / "result_s14.png"), full_page=True)

        # every width: no sideways page scroll; the photo and the contacts are usable on a phone
        for vw, vh in ((1280, 800), (1024, 768), (820, 1180), (390, 844), (360, 740)):
            pg.set_viewport_size({"width": vw, "height": vh}); pg.wait_for_timeout(400)
            check(pg.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), f"no horizontal page scroll at {vw} px")
            check(pg.evaluate("() => document.querySelector('#rv-canvas').getBoundingClientRect().width > 0"), f"the photo shows at {vw} px")
            pg.screenshot(path=str(a.out / f"result_{vw}.png"), full_page=True)
        pg.set_viewport_size({"width": 1440, "height": 900})
        pg.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }"); pg.wait_for_timeout(200)
        pg.screenshot(path=str(a.out / "result_dark.png"))
        pg.evaluate("() => { document.documentElement.dataset.theme = 'light'; }")
        pg.click("#again"); pg.wait_for_timeout(400)
        check(pg.url.endswith("/workspace") and pg.locator("#idle").is_visible() and pg.locator(".cz-help-fab").count() == 0,
              "New photo opens an empty form and closes the help")

        # PDF with the same selection: same summary as the page, the chosen findings, contacts, methodology
        r = pg.request.get(a.base + href)
        check(r.status == 200 and r.body()[:4] == b"%PDF", "PDF report downloads")
        (a.out / "report.pdf").write_bytes(r.body())
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(io.BytesIO(r.body()))
            text = " ".join(" ".join(doc[i].get_textpage().get_text_range() for i in range(len(doc))).split())
            doc.close()
            order = ["1.0 Inspection Summary", "2.0 What To Do Next", "3.0 Photograph", "4.0 Findings Included in This Report",
                     "5.0 Suggested Reporting Contacts", "6.0 Limitations and Methodology", "7.0 Review Record"]
            pos = [text.find(x) for x in order]
            check(all(x >= 0 for x in pos) and pos == sorted(pos), f"PDF sections in the page's order ({pos})")
            check(all(x in text for x in ov.get("lines", [])) and ov.get("headline", "") in text, "PDF summary sentences are the page's")
            sec4 = text[text.find("4.0 Findings"):text.find("5.0 Suggested")]
            check(all(f" {i} " in f" {sec4} " for i in chosen), f"PDF findings list the selected wires {chosen}")
            if meas:
                check("Appendix A. Technical Measurements" in text and text.find("Appendix A. Technical Measurements") > text.find("7.0 Review Record"),
                      "technical measurements are an appendix after the sign-off block")
            check("image-based decision-support tool" in " ".join(text.split()), "PDF carries the canonical Important Notice")
            check(f"Problem wires: {sc.get('problem_count')} of {sc.get('total_wires')}" in text and "not a statutory classification" in text,
                  "S18: the PDF states the problem wires and the triage as on screen")
        except ImportError:
            print("SKIP PDF text check (pypdfium2 not installed)")

        # Admin portal
        pg.goto(f"{a.base}/admin/login")
        pg.fill("input[name=username]", user)
        pg.fill("input[name=password]", pw)
        pg.click("button[type=submit]")
        pg.wait_for_selector("#top:not([hidden])")
        pg.wait_for_selector(".st-tabs", timeout=60_000)
        ops = pg.request.get(f"{a.base}/api/admin/operations").json()
        m = ops["metrics"]
        rt = lambda k: int(pg.inner_text(f".st-tabs [data-review='{k}'] b"))  # noqa: E731
        check([rt(""), rt("NOT_YET_REVIEWED"), rt("PARTIALLY_REVIEWED"), rt("REVIEW_COMPLETE")]
              == [len(ops["poles"]), *(ops["review_counts"][k] for k in ("NOT_YET_REVIEWED", "PARTIALLY_REVIEWED", "REVIEW_COMPLETE"))]
              and rt("") == sum(ops["review_counts"].values()), "S18: review tabs match the operations API and add up to All")
        lv = lambda k: sum(1 for r in ops["poles"] if (r.get("risk_level") or {}).get("level") == k)  # noqa: E731
        for k in ("high", "moderate", "normal"):
            check(int(pg.inner_text(f"[data-screen={k}] b")) == lv(k), f"S18: {k} triage count matches the API")
        heads = " ".join(pg.locator("#tq thead th").all_inner_texts())
        check("Assigned" not in heads and "Reason" not in heads and "Risk" in heads and "Review" in heads, "S18: no Assigned or Reason column")
        check(pg.locator(".ops-kpis, .slim-notice").count() == 0, "no KPI cards and no notice banner on the dashboard")
        risk_txt = pg.locator("#tq td[data-th=Risk]").all_inner_texts()
        check(all(t == "Processing failed" or t == "Processing" or "problem wire" in t for t in risk_txt), f"S18: the Risk column reads 'Level · n / N problem wires' ({risk_txt[:2]})")
        pg.click(".st-tabs [data-review='']")
        fl, ft = ops["fleet"], pg.inner_text(".fleet")  # S16: fleet counts, each with its entity
        check(f"{fl['poles']} poles" in ft and f"{fl['inspections']} inspections" in ft and f"{fl['queue']} poles awaiting" in ft
              and fl["poles"] == m["registered_poles"], f"fleet line names its entities and matches the API ({ft[:80]!r})")
        check(sum(m[k] for k in ("processing_failed", "flagged", "incomplete", "awaiting_review", "reviewed", "processing")) == m["registered_poles"],
              "every pole has exactly one status (failures and flagged results are never double-counted)")
        check(pg.locator("[data-act=people]").count() == 1 and pg.locator("[data-act=auto]").count() == 1
              and (pg.locator(".feed .fd-i.auto").count() >= 1 or not any(e["automated"] for e in ops["feed"])), "S18: automated events are marked apart from people's")
        for vw in (820, 390):
            pg.set_viewport_size({"width": vw, "height": 900}); pg.wait_for_timeout(300)
            check(pg.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), f"admin dashboard: no horizontal scroll at {vw} px")
        pg.set_viewport_size({"width": 1440, "height": 900})
        pg.goto(f"{a.base}/admin/directory"); pg.wait_for_selector(".tbl-dir", timeout=30_000)
        check(pg.locator(".tbl-dir tbody tr").count() >= 1, "provider directory lists its entries")
        check(pg.locator("#user-menu a[href='/admin/system']").count() == 0, "no System entry in the user menu")
        pg.goto(f"{a.base}/admin/inspections")
        pg.wait_for_selector(".tbl-insp", timeout=60_000)
        pg.click("[data-screen=processing_issue]")
        try:
            pg.wait_for_selector("[data-screen=processing_issue][aria-pressed=true]", timeout=15_000); pg.wait_for_timeout(500)
            ok = all(t in ("Processing failed", "Processing") for t in pg.locator(".tbl-insp td[data-label=Risk]").all_inner_texts())
        except Exception:
            ok = False
        check(ok, "S18: Processing issue lists only failed or processing inspections")
        heads = " ".join(pg.locator(".tbl-insp thead th").all_inner_texts())
        check(all(h in heads for h in ("File", "Location", "Date", "Detected", "Problem", "Risk", "Review", "Admin result")) and "Assigned" not in heads,
              "S18: Inspections columns")
        if pg.locator("#tx-btn").count():
            for href in pg.locator("#tx-pop a").evaluate_all("els => els.map(e => e.getAttribute('href'))"):
                check(pg.request.get(a.base + href).status == 200, f"technical export {href}")
        pg.goto(f"{a.base}/admin/inspections/{aid}")
        pg.wait_for_selector("#rvw .rvw-h", timeout=60_000); pg.wait_for_timeout(1200)
        check(pg.locator(".rv-know").count() == 1 and pg.locator(".rv-how").count() == 1, "S18: the inspection sheet is the shared result view")
        check("rs-partially_reviewed" in (pg.get_attribute("#rvw .rs", "class") or ""), "S18: opening an inspection starts its review")
        if wires:
            w0 = wires[0]["index"]
            pg.click(f"#rv-wl [data-w='{w0}']"); pg.wait_for_timeout(200)
            check(f"Wire {w0}" in pg.inner_text("#rv-detail"), "inspection sheet: selecting a wire shows its meter")
        # S18 refinement: no diagnostics block or three-dot menu; VNI / CRS under "Other"; wire search; separate delete
        sheet = pg.inner_text("#sheet")
        check("Acquisition envelope" not in sheet and pg.locator("#more-btn, #diag").count() == 0,
              "S18: technical diagnostics and the three-dot menu are gone")
        pg.click(".sh-tech > summary"); pg.wait_for_timeout(200)
        check("Visual Noise Index" in pg.inner_text(".sh-tech") and pg.locator(".sh-tech .ix-b").count() >= 1,
              "S18: VNI under Other, with an explanation")
        check(pg.locator("#delete").count() == 1, "S18: Delete inspection is its own action")
        if wires:
            wl = wires[-1]["index"]
            pg.fill("#rv-find-q", f"Wire {wl}"); pg.press("#rv-find-q", "Enter"); pg.wait_for_timeout(500)
            check(pg.evaluate("window.wfViewer.selected") == wl, "S18: the photo's wire search selects the wire")
        # S18: the Field Screener has no technical view, even for signed-in staff; the old address shows the same page
        pg.goto(f"{a.base}/workspace/result/{aid}?view=technical"); pg.wait_for_selector(".rv-how", timeout=60_000); pg.wait_for_timeout(800)
        check(pg.locator("#inventory, #adv, #span-detail").count() == 0 and pg.get_by_text("Technical view").count() == 0,
              "S18: no technical view in the Field Screener, even for staff")
        # S18 (Phase 17): each wire category is compared only with its own reference: power with the ERB 3% limit, telecom
        # with the 2% / 5% typical range (a guideline, not a regulation); no other class gets a reference
        lbl = {(w["class"], (w.get("finding") or {}).get("meter", {}).get("kind")): (w.get("finding") or {}).get("meter", {}).get("label") or ""
               for w in meas}
        check(all(("2% / 5%" in v and "3%" not in v) for (cls, k), v in lbl.items() if cls == "telecom-cable" and k == "reference")
              and all(k != "reference" for (cls, k) in lbl if cls not in ("power-distribution-lines", "telecom-cable")),
              "telecom is compared with its 2% / 5% range, never the 3% power limit; other wires with none")
        r_ = pg.request.post(f"{a.base}/api/help/ask", data={"question": "List all poles that need review", "analysis_id": aid},
                             headers={"origin": a.base}).json()
        check(r_.get("topic") == "refused", "a staff session does not unlock anything in the citizen assistant")
        pg.goto(f"{a.base}/admin/map")
        pg.wait_for_selector(".pcard, .ranks .empty", timeout=60_000)
        pg.wait_for_timeout(1500)
        files = pg.locator(".pcard .pc-file").all_inner_texts()
        check(not pg.locator(".pcard").count() or all(files), "S18: map list items lead with the file name")
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
