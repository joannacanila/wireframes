/** S18: facts shown while a photo is processed. A controlled, reviewed pool: each fact is tied to a source in the
 *  thesis reference list (docs/thesis/WIREFRAME_THESIS_REFERENCE.md section 19) or to a primary source named here.
 *  No statistic, no claim about this photo and no claim about the system's accuracy.
 *
 *  Order: a fresh shuffle for every processing session (Fisher-Yates), so no fact repeats until all have been shown and
 *  there is no fixed loop. The id of the last fact shown is kept on this device only (no personal data) so the next
 *  session does not open with it. */
export const FACTS = [
  { id: "catenary", text: "A cable hanging freely under its own weight between two supports takes the shape of a curve called a catenary.",
    source: "Irvine, H. M. (1981). Cable Structures. MIT Press." },
  { id: "catena", text: "“Catenary” comes from the Latin catena, meaning chain.",
    source: "Merriam-Webster Dictionary, “catenary”, etymology." },
  { id: "tension", text: "How much a span sags depends on its tension and its weight per metre: the tighter the wire, the smaller the sag.",
    source: "Kampik et al. (2024). Sag and tension calculations for high-voltage overhead line conductors. Energies 17(12), 2967." },
  { id: "erb", text: "In 1995 the Energy Regulatory Board set the sag of wires on electric utility poles at no more than 3% of the distance between poles.",
    source: "ERB Resolution No. 95-21, Section 19 (Poles, Sag of Wires), 3 August 1995." },
  { id: "itu", text: "There is no single sag limit for aerial fiber cable: each cable's sag follows from its tension rating, the span and the loading.",
    source: "ITU-T Recommendation L.102 (11/2025), clauses 7.2.2.1, 7.3.10 and 8.2.6." },
  { id: "photo-sag", text: "Researchers have measured power-line sag from smartphone photos by combining the image with the phone's accelerometer.",
    source: "Safdarinezhad et al. (2022). Measurement 199, 111493." },
  { id: "thinning", text: "To follow each wire, the photo's wire pixels are thinned to a one-pixel centre line with an algorithm published in 1984.",
    source: "Zhang, T. Y., and Suen, C. Y. (1984). Communications of the ACM 27(3), 236-239." },
  { id: "clahe", text: "Dull, low-contrast photos are evened out with adaptive histogram equalization (CLAHE), a method from 1994.",
    source: "Zuiderveld, K. (1994). Contrast limited adaptive histogram equalization. Graphics Gems IV." },
  { id: "psgc", text: "Barangay names here come from the Philippine Standard Geographic Code, the official list of places kept by the Philippine Statistics Authority.",
    source: "Philippine Statistics Authority, PSGC (psa.gov.ph/classification/psgc)." },
  { id: "911", text: "911 is the Philippines' national emergency hotline, for example for a fallen wire that is sparking or burning.",
    source: "Executive Order No. 56, s. 2018 (national 911 emergency hotline)." },
];

const LAST = "wf-fact-last";

/** A new, shuffled order for one processing session; never starts with the fact the previous session ended on. */
export function factOrder(pool = FACTS, rand = Math.random) {
  const a = pool.slice();
  for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  let last = null;
  try { last = localStorage.getItem(LAST); } catch { /* storage unavailable */ }
  if (a.length > 1 && a[0].id === last) a.push(a.shift());
  return a;
}

export function rememberFact(f) { try { localStorage.setItem(LAST, f.id); } catch { /* storage unavailable */ } }
