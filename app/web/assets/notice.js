// S17 (Part 5.4): the ONE canonical Important Notice, one component for every page. <wf-notice variant="..."> renders:
//   full      the notice in full (results page footer, inspection sheets, the assistant)
//   brief     an "Important Notice" toggle that reveals the same text (dashboard, map)
//   landing   a small indicator that opens the same text in a dialog
//   upload    photo guidance only (what the result depends on); not a second general disclaimer
// app/core/notice.py holds the identical text for PDFs and CSVs (a test keeps them equal). No other wording.

export const NOTICE_TITLE = "Important Notice";
export const NOTICE_TEXT = "WIREFRAME is an image-based decision-support tool designed to help document visible pole and wire conditions. Its results are preliminary and may contain errors or limitations. Findings should be reviewed by qualified professionals and must not replace an on-site inspection or applicable safety assessment.";

const LEAD = {
  brief: "Statuses come from image-based screening.",
  upload: "Results depend on the photo: image quality, how clearly the wires are visible and how the span is framed.",
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
    if (v === "landing") {
      // S15: a small indicator instead of a paragraph. Hover or keyboard focus shows the notice as a
      // preview; a click or tap (phones) opens the full approved notice. Both texts are unchanged.
      this.innerHTML = `<button type="button" class="wf-notice-more wf-notice-chip" aria-haspopup="dialog" aria-controls="${id}" aria-describedby="${id}-tip">${ICON}<span>Important Notice</span></button>
        <span class="wf-notice-tip" role="tooltip" id="${id}-tip">${NOTICE_TEXT}</span>`;
      const dlg = document.createElement("dialog");
      dlg.className = "wf-notice-dialog"; dlg.id = id; dlg.setAttribute("aria-labelledby", `${id}-t`);
      dlg.innerHTML = `<div class="wf-notice-dialog-body"><h2 id="${id}-t">${NOTICE_TITLE}</h2><p>${NOTICE_TEXT}</p></div>
        <form method="dialog"><button class="wf-notice-close" type="submit" autofocus>Close</button></form>`;
      document.body.append(dlg);
      const btn = this.querySelector(".wf-notice-more");
      this.addEventListener("keydown", (e) => { if (e.key === "Escape") this.querySelector(".wf-notice-tip").classList.add("off"); });
      btn.addEventListener("blur", () => this.querySelector(".wf-notice-tip").classList.remove("off"));
      btn.addEventListener("click", () => dlg.showModal());
      dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); }); // click on the backdrop closes it
      dlg.addEventListener("close", () => btn.focus());
      return;
    }
    this.innerHTML = `${ICON}<div><p>${LEAD[v] || LEAD.brief} <button type="button" class="wf-notice-more" aria-expanded="false" aria-controls="${id}">Important Notice</button></p>
      <p class="wf-notice-full" id="${id}" hidden><b>${NOTICE_TITLE}.</b> ${NOTICE_TEXT}</p></div>`;
    const btn = this.querySelector(".wf-notice-more");
    btn.addEventListener("click", () => {
      const open = btn.getAttribute("aria-expanded") !== "true";
      btn.setAttribute("aria-expanded", String(open));
      btn.textContent = open ? "Hide the notice" : "Important Notice";
      this.querySelector(".wf-notice-full").hidden = !open;
    });
  }
}
if (!customElements.get("wf-notice")) customElements.define("wf-notice", WfNotice);
