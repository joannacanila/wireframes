// v2.19: the decision-support notice, one component for every page. <wf-notice variant="..."> renders:
//   full      the approved Important Notice (inspection results, inspection sheets, the assistant)
//   brief     one line with the notice behind a "Read the notice" toggle (dashboard, map)
//   landing   one line for the landing page
//   upload    what the result depends on, before a photo is submitted
// The wording is approved; app/core/notice.py holds the same text for PDFs and CSVs (a test keeps them equal).

export const NOTICE_TITLE = "Important Notice";
export const NOTICE_TEXT = "WIREFRAME is designed to support the screening and assessment of overhead utility wires. Its results are generated using automated computer-vision models and may contain errors, omissions, or inaccurate classifications. Findings should not be considered a definitive safety assessment or a substitute for an actual inspection. Always have results verified by a qualified professional before making engineering, maintenance, or safety-related decisions.";

const LEAD = {
  brief: "Risk levels here are automated screening results and need verification by a qualified professional.",
  landing: "WIREFRAME supports screening. It does not replace a professional inspection.",
  upload: "How reliable the result is depends on the photo: its quality, how clearly the wires can be seen and how they are framed. Automated results may be incomplete or inaccurate.",
};

const ICON = `<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8"/><path d="M10 9v5M10 6.2v.1"/></svg>`;

class WfNotice extends HTMLElement {
  connectedCallback() {
    if (this.dataset.ready) return;
    this.dataset.ready = "1";
    const v = this.getAttribute("variant") || "full";
    this.setAttribute("role", "note");
    this.setAttribute("aria-label", NOTICE_TITLE);
    this.classList.add("wf-notice", `wf-notice--${v}`);
    if (v === "full") {
      this.innerHTML = `${ICON}<div><b>${NOTICE_TITLE}</b><p>${NOTICE_TEXT}</p></div>`;
      return;
    }
    const id = `wfn-${Math.random().toString(36).slice(2, 8)}`;
    this.innerHTML = `${ICON}<div><p>${LEAD[v] || LEAD.brief} <button type="button" class="wf-notice-more" aria-expanded="false" aria-controls="${id}">Read the notice</button></p>
      <p class="wf-notice-full" id="${id}" hidden><b>${NOTICE_TITLE}.</b> ${NOTICE_TEXT}</p></div>`;
    const btn = this.querySelector(".wf-notice-more");
    btn.addEventListener("click", () => {
      const open = btn.getAttribute("aria-expanded") !== "true";
      btn.setAttribute("aria-expanded", String(open));
      btn.textContent = open ? "Hide the notice" : "Read the notice";
      this.querySelector(".wf-notice-full").hidden = !open;
    });
  }
}
if (!customElements.get("wf-notice")) customElements.define("wf-notice", WfNotice);
