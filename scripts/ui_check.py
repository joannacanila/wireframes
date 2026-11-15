"""Headless-browser check of the field screener (v2.11), against a RUNNING server.

    python -m scripts.ui_check --base http://127.0.0.1:8000 --shots runs/ui_shots \
        --out docs/thesis/results/ui_check_v211.json [photos ...]

For every photo (default: runs/demo_samples/*): open /workspace in Chrome (Playwright), upload through
the form, wait for the result, then verify
  * no console error and no uncaught page error,
  * the verdict, KPI tiles and the inspected-spans table render,
  * all four layer toggles switch (aria-pressed) and the full-mask toggle loads the mask image,
  * table -> canvas: clicking a row selects that wire in the viewer,
  * canvas -> table: clicking a wire's own pixel on the canvas selects its row,
  * the Report PDF link returns a PDF,
and save desktop screenshots (plus one phone-width screenshot).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def check_photo(page, base: str, photo: Path, shots: Path, pole_code: str | None) -> dict:
    errors: list[str] = []
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(f"{base}/workspace")
    page.set_input_files('input[name="file"]', str(photo))
    if pole_code:
        page.fill('input[name="pole_code"]', pole_code)
    page.click("#submit")
    page.wait_for_selector(".fx-verdict", timeout=120_000)
    page.wait_for_function("window.wfViewer && window.wfViewer.image", timeout=30_000)
    row = {"photo": photo.name, "url": page.url}
    row["verdict"] = page.inner_text(".fx-verdict h2")
    row["verdict_text"] = page.inner_text(".fx-verdict p")
    row["kpis"] = [k.inner_text().replace("\n", " ") for k in page.query_selector_all(".fx-kpi")]
    row["retake_banner"] = page.query_selector(".fx-banner") is not None
    row["hazards"] = [x.inner_text() for x in page.query_selector_all(".fx-hazards li")]
    rows = page.query_selector_all("tr[data-wire]")
    row["table_rows"] = len(rows)
    row["unmeasured_without_reason"] = page.evaluate(
        "[...document.querySelectorAll('tr[data-group=none][data-wire]')].filter(tr => !tr.querySelector('.fx-reason')).length")
    # layer toggles
    toggles = {}
    for layer in ("spans", "wiremask", "vegetation", "photo"):
        b = page.query_selector(f'[data-layer="{layer}"]')
        if b.is_disabled():
            toggles[layer] = "disabled"
            continue
        before = b.get_attribute("aria-pressed")
        b.click()
        after = b.get_attribute("aria-pressed")
        toggles[layer] = "ok" if before != after else "no-change"
        if layer == "wiremask":
            page.wait_for_function("window.wfViewer.wireMask !== null", timeout=15_000)
            page.screenshot(path=str(shots / f"{photo.stem}_fullmask.png"), full_page=False)
        b.click()  # restore
    row["toggles"] = toggles
    # table -> canvas
    if rows:
        target = rows[min(1, len(rows) - 1)]
        wid = int(target.get_attribute("data-wire"))
        target.click()
        row["table_to_canvas"] = page.evaluate("window.wfViewer.selected") == wid
        # canvas -> table: click a pixel of another wire, found from its centreline / mask in viewer coordinates
        other = page.evaluate("""() => {
            const v = window.wfViewer, r = v.wrap.getBoundingClientRect();
            for (const w of v.wires) {
              if (w.index === v.selected) continue;
              const pts = (w.centerline && w.centerline.length) ? w.centerline : null;
              if (!pts) continue;
              const [x, y] = pts[Math.floor(pts.length / 2)];
              const sx = r.left + x * v.view.s + v.view.x, sy = r.top + y * v.view.s + v.view.y;
              if (sx > r.left + 5 && sx < r.right - 5 && sy > r.top + 5 && sy < r.bottom - 5) return {i: w.index, x: sx, y: sy};
            }
            return null; }""")
        if other:
            page.evaluate("window.wfViewer.reset()")
            page.wait_for_timeout(300)
            other = page.evaluate(f"""() => {{
                const v = window.wfViewer, r = v.wrap.getBoundingClientRect(), w = v.wires.find(w => w.index === {other['i']});
                const [x, y] = w.centerline[Math.floor(w.centerline.length / 2)];
                return {{i: w.index, x: r.left + x * v.view.s + v.view.x, y: r.top + y * v.view.s + v.view.y}}; }}""")
            page.mouse.click(other["x"], other["y"])
            page.wait_for_timeout(200)
            hit = page.evaluate("window.wfViewer.selected")
            sel_row = page.evaluate("(document.querySelector('tr[aria-selected=true]')||{}).dataset?.wire")
            row["canvas_to_table"] = hit is not None and sel_row is not None and int(sel_row) == hit
            row["canvas_click_wire"] = {"expected": other["i"], "selected": hit}
    # PDF link
    href = page.get_attribute("#pdf", "href")
    resp = page.request.get(base + href)
    row["pdf_ok"] = resp.status == 200 and resp.body()[:4] == b"%PDF"
    page.evaluate("window.wfViewer.reset()")
    page.screenshot(path=str(shots / f"{photo.stem}.png"), full_page=True)
    row["errors"] = errors
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--shots", type=Path, default=ROOT / "runs" / "ui_shots")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--pole-code", default=None)
    ap.add_argument("photos", nargs="*", type=Path)
    a = ap.parse_args()
    photos = a.photos or sorted((ROOT / "runs" / "demo_samples").glob("*"))
    a.shots.mkdir(parents=True, exist_ok=True)
    results, ok = [], True
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        for ph in photos:
            page = ctx.new_page()
            r = check_photo(page, a.base, ph, a.shots, a.pole_code)
            good = (not r["errors"] and r.get("pdf_ok") and r["unmeasured_without_reason"] == 0
                    and all(v in ("ok", "disabled") for v in r["toggles"].values())
                    and (r["table_rows"] == 0 or (r.get("table_to_canvas") and r.get("canvas_to_table", True))))
            r["pass"] = bool(good)
            ok &= bool(good)
            results.append(r)
            print(json.dumps({k: v for k, v in r.items() if k not in ("verdict_text",)}), flush=True)
            page.close()
        # phone-width rendering of the last result
        mob = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True)
        mp = mob.new_page()
        merr = []
        mp.on("pageerror", lambda e: merr.append(str(e)))
        mp.goto(results[-1]["url"])
        mp.wait_for_selector(".fx-verdict", timeout=60_000)
        overflow = mp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        mp.screenshot(path=str(a.shots / "phone.png"), full_page=True)
        results.append({"photo": "phone-width (390 px)", "horizontal_overflow": overflow, "errors": merr,
                        "pass": not overflow and not merr})
        ok &= not overflow and not merr
        print(json.dumps(results[-1]), flush=True)
        browser.close()
    if a.out:
        a.out.write_text(json.dumps({"all_pass": ok, "results": results}, indent=1))
    print("ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
