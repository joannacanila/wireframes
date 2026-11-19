"""S14 provider and reporting-contact directory.

Built-in rows are seeded once (by ``seed_key``) and then belong to the administrators: an edited or deleted seed row is
never re-created or overwritten. Every row states its source and how it was verified:

  verified          an official source (law text, government site) confirms the channel and, where given, the area
  secondary_source  only press or third-party pages were found; shown to the public with "confirm on the official site"
  unverified        no reliable channel found; administrators see it as a task, the public never does

Suggestions are matched on the inspection's city / municipality (from its validated barangay, PSGC) and are labelled as
suggestions: a wire's predicted type cannot establish which company owns it, and coverage is not ownership.
Research for the seed rows: 18 Nov 2026 (S14); see docs/thesis/RESEARCH_AND_DECISION_REGISTER.md D-30.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import CONTACT_TYPES, VERIFICATION, Contact  # noqa: F401  (re-exported)

TODAY = "2026-11-18"
DASMA = "City of Dasmariñas"

SEED: list[dict[str, Any]] = [
    {"seed_key": "ph-911", "organization": "Emergency 911 National Hotline", "org_type": "emergency",
     "service_area": "Nationwide", "cities": ["national"], "phone": "911", "url": None,
     "assistance": "Life-threatening emergencies: a fallen or burning wire, fire, electric shock, injury.",
     "instructions": "Call 911 first if a wire is down, sparking or burning, or anyone is hurt. Calls are free.",
     "source_url": "https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/5/83090",
     "source_note": "Executive Order No. 56 s. 2018 (911 replaces Patrol 117; DILG; free calls).",
     "verification": "verified", "last_verified": TODAY},
    {"seed_key": "meralco", "organization": "Meralco (Manila Electric Company)", "org_type": "electric_utility",
     "service_area": "Franchise area incl. the City of Dasmariñas and the other Cavite cities named in RA 12146",
     "cities": ["City of Bacoor", "City of Carmona", "City of Cavite", DASMA, "City of General Trias",
                "City of Imus", "City of Tagaytay", "City of Trece Martires"],
     "phone": "16211", "url": "https://meralco.com.ph",
     "assistance": "Electric distribution lines: low-hanging or damaged power lines, poles, outages.",
     "instructions": "Report the pole location and share the WIREFRAME report. The distribution area is confirmed by "
                     "law; the hotline number is from press reports, so confirm it on Meralco's website.",
     "source_url": "https://lawphil.net/statutes/repacts/ra2025/ra_12146_2025.html",
     "source_note": "Area: Republic Act No. 12146 (2025) franchise. Hotline 16211: press reports (e.g. Philstar, "
                    "23 Dec 2024), not read on meralco.com.ph.",
     "verification": "secondary_source", "last_verified": TODAY},
    {"seed_key": "pldt", "organization": "PLDT", "org_type": "telecom_provider", "service_area": "Nationwide",
     "cities": ["national"], "phone": "171 (PLDT/Smart); (02) 8888-171 from other networks",
     "url": "https://www.pldthome.com/support-myhome",
     "assistance": "PLDT telephone and fiber cables.",
     "instructions": "Only if the cable is marked PLDT or you know it is theirs. Confirm the number on PLDT's site.",
     "source_url": "https://www.pldthome.com/news-media/2024/06/13/pldt-makes-customer-calls-easier-with-cebuano-language-option",
     "source_note": "171 from third-party guides and a PLDT news page; the contact page itself was not read.",
     "verification": "secondary_source", "last_verified": TODAY},
    {"seed_key": "globe", "organization": "Globe Telecom", "org_type": "telecom_provider", "service_area": "Nationwide",
     "cities": ["national"], "phone": "211 (Globe mobile); (02) 7795-0211 from other networks",
     "url": "https://www.globe.com.ph",
     "assistance": "Globe / Globe At Home cables.",
     "instructions": "Only if the cable is marked Globe or you know it is theirs. Confirm the number on Globe's site.",
     "source_url": "https://www.expatfocus.com/?p=26776", "source_note": "Third-party page only.",
     "verification": "secondary_source", "last_verified": TODAY},
    {"seed_key": "converge", "organization": "Converge ICT", "org_type": "telecom_provider", "service_area": "Nationwide",
     "cities": ["national"], "phone": None, "url": None, "assistance": "Converge fiber cables.",
     "instructions": "No reporting channel has been verified yet. Add the official channel and its source.",
     "source_url": None, "source_note": "No official reporting channel found as of 18 Nov 2026.",
     "verification": "unverified", "last_verified": None},
    {"seed_key": "dasma-city", "organization": "City Government of Dasmariñas", "org_type": "city_government",
     "service_area": "City of Dasmariñas", "cities": [DASMA], "phone": None, "url": None,
     "assistance": "City engineering and disaster-risk-reduction offices.",
     "instructions": "Add the city hall / CDRRMO channel from the city's "
                     "official site and its source.",
     "source_url": None, "source_note": "No official city contact page found as of 18 Nov 2026.",
     "verification": "unverified", "last_verified": None},
]

# --------------------------------------------------------------------------- S18 provider research (19 Nov 2026)
# Only what an official page showed is recorded as verified; every row names the page. Coverage basis:
#   documented_area     a law or the provider documents the area (e.g. a franchise)
#   branch_office       the provider's official site lists a branch for the town (street-level coverage unpublished)
#   provincial_listing  the official site lists the province only
#   national_license    a nationwide operator; availability in a given barangay is NOT established
S18 = "2026-11-19"
FB = "https://fiberblaze.ph/"
FB_NOTE = ("FIBERBLAZE official site (Locations, read 19 Nov 2026) lists this branch and number. Street-level coverage "
           "is not published, so service at a given address is not confirmed.")


def _fb(key: str, city: str, phone: str, name: str, reg: str | None = None, prev: list[str] | None = None,
        src: str = FB) -> dict[str, Any]:
    return {"seed_key": key, "organization": name, "org_type": "telecom_provider", "category": "catv",
            "registered_name": reg, "previous_names": prev or [], "service_area": f"FIBERBLAZE branch: {city}",
            "cities": [city], "phone": phone, "url": "https://fiberblaze.ph/Support.html",
            "reporting_url": "https://fiberblaze.ph/Support.html",
            "assistance": "Local cable TV and fiber internet cables (FIBERBLAZE).",
            "instructions": "Only if the cable is marked FIBERBLAZE or you know it is theirs. Calls only on the mobile "
                            "number.", "source_url": src, "source_note": FB_NOTE, "source_type": "official_site",
            "coverage_basis": "branch_office", "verification": "verified", "last_verified": S18}


S18_SEED: list[dict[str, Any]] = [
    _fb("fiberblaze-dasma", DASMA, "0919-058-1111", "FIBERBLAZE Dasmariñas", "Dasca Cable Services, Inc."),
    _fb("fiberblaze-imus", "City of Imus", "0919-058-1111", "FIBERBLAZE Imus", None, ["Meridian Cable TV, Inc."],
        "https://fiberblaze.ph/FiberblazeImus"),
    _fb("fiberblaze-silang", "Silang", "0927-707-6028", "FIBERBLAZE Silang"),
    _fb("fiberblaze-gma", "Gen. Mariano Alvarez", "0916-296-2437", "FIBERBLAZE General Mariano Alvarez"),
    _fb("fiberblaze-gentri", "City of General Trias", "0969-609-2590", "FIBERBLAZE General Trias"),
    _fb("fiberblaze-trece", "City of Trece Martires", "0916-296-2435", "FIBERBLAZE Trece Martires"),
    _fb("fiberblaze-tanza", "Tanza", "0916-296-2435", "FIBERBLAZE Tanza"),
    _fb("fiberblaze-naic", "Naic", "0961-268-9461", "FIBERBLAZE Naic"),
    {"seed_key": "streamtech", "organization": "Streamtech (formerly Planet Cable)", "org_type": "telecom_provider",
     "category": "broadband", "registered_name": "Streamtech Systems Technologies, Inc.", "previous_names": ["Planet Cable"],
     "service_area": "Listed provinces include Cavite (no town list published)", "cities": ["province:Cavite"],
     "phone": "0919-057-8888", "url": "https://streamtech.com.ph/support/",
     "reporting_url": "https://streamtech.com.ph/support/",
     "assistance": "Streamtech / Planet Cable fiber and cable TV lines.",
     "instructions": "Only if the cable is marked Streamtech or Planet Cable, or you know it is theirs.",
     "source_url": "https://streamtech.com.ph/", "source_type": "official_site", "coverage_basis": "provincial_listing",
     "source_note": "Official site (read 19 Nov 2026): customer care hotline, history (Planet Cable, est. 2000, Cavite "
                    "and Laguna) and a province-level location list including Cavite. No town-level coverage.",
     "verification": "verified", "last_verified": S18},
    # unverified: the official page could not be read on 19 Nov 2026. Administrators see these as tasks; the public
    # never does. No number or URL is recorded until one is read on an official page.
    *({"seed_key": k, "organization": n, "org_type": "telecom_provider", "category": cat, "registered_name": None,
       "service_area": "Nationwide operator; local coverage not verified", "cities": ["national"], "phone": None,
       "url": None, "assistance": a, "instructions": "Add the official reporting channel and its source.",
       "source_url": None, "source_type": None, "coverage_basis": "national_license",
       "source_note": note, "verification": "unverified", "last_verified": None}
      for k, n, cat, a, note in (
          ("dito", "DITO Telecommunity", "telecom", "DITO mobile and fiber network.",
           "dito.ph/contact-us returned 404 on 19 Nov 2026; coverage checker not read."),
          ("smart", "Smart Communications", "telecom", "Smart mobile network and Smart Broadband.",
           "smart.com.ph/contact-us returned no content on 19 Nov 2026."),
          ("sky", "SKY (SkyCable)", "catv", "SKY cable TV and broadband.",
           "mysky.com.ph returned 403 on 19 Nov 2026; current operating status not confirmed."),
          ("eastern", "Eastern Communications", "telecom", "Eastern Communications lines.",
           "Official site not read; current corporate identity to be confirmed."),
          ("innove", "Innove Communications", "telecom", "Innove (Globe group) lines.",
           "Official site not read; current corporate identity to be confirmed."))),
]
# corrections to rows seeded in S14, applied only while the row is still exactly as seeded (never over an edit)
S18_UPDATES: dict[str, dict[str, Any]] = {
    "meralco": {"category": "electricity", "phone": "(02) 16211", "email": "customercare@meralco.com.ph",
                "url": "https://www.meralco.com.ph/contact-us",
                "reporting_url": "https://mymeralco.com.ph/guest-dashboard/brownout",
                "registered_name": "Manila Electric Company", "source_type": "law",
                "coverage_basis": "documented_area", "verification": "verified", "last_verified": S18,
                "instructions": "Report the pole location and share the WIREFRAME report.",
                "source_note": "Area: Republic Act No. 12146 (2025) franchise. Hotline (02) 16211 and e-mail read on "
                               "meralco.com.ph/contact-us on 19 Nov 2026."},
    "converge": {"category": "broadband", "phone": "+63 2 8667 0850",
                 "url": "https://www.convergeict.com/support/customer-support",
                 "reporting_url": "https://www.convergeict.com/support/customer-support",
                 "registered_name": "Converge ICT Solutions, Inc.", "source_type": "official_site",
                 "coverage_basis": "national_license", "verification": "verified", "last_verified": S18,
                 "source_url": "https://www.convergeict.com/contact-us/",
                 "instructions": "Only if the cable is marked Converge or you know it is theirs.",
                 "source_note": "Customer Experience hotline read on convergeict.com/contact-us on 19 Nov 2026. "
                                "Coverage at a given address is not published there."},
    "pldt": {"category": "telecom", "coverage_basis": "national_license", "source_type": "press"},
    "globe": {"category": "telecom", "coverage_basis": "national_license", "source_type": "third_party"},
    "ph-911": {"category": "emergency", "source_type": "law"},
    "dasma-city": {"category": "government"},
}
CATEGORY_LABEL = {"electricity": "Electricity distributors", "telecom": "Telecommunications and telephone",
                  "broadband": "Fiber and broadband", "catv": "Cable TV and local cable operators",
                  "other_infra": "Other infrastructure and pole-attachment operators", "emergency": "Emergency",
                  "government": "Local government"}
CATEGORY_ORDER = ["electricity", "telecom", "broadband", "catv", "other_infra"]
# which provider categories can be relevant to a detected conductor type (never proof of ownership)
WIRE_CATEGORIES = {"power-distribution-lines": {"electricity"}, "telecom-cable": {"telecom", "broadband", "catv"}}
ORG_CATEGORY = {"emergency": "emergency", "electric_utility": "electricity", "telecom_provider": "telecom",
                "barangay": "government", "city_government": "government", "other": "other_infra"}


def category_of(c: Contact) -> str:
    return c.category or ORG_CATEGORY.get(c.org_type, "other_infra")


def upgrade_seed(s: Session) -> int:
    """S18: add the researched rows (by seed_key, once) and correct S14 seed rows that nobody has edited."""
    from app.services import audit
    n = 0
    have = {c.seed_key: c for c in s.scalars(select(Contact).where(Contact.seed_key.is_not(None))).all()}
    for row in S18_SEED:
        if row["seed_key"] not in have:
            s.add(Contact(**row, updated_by="System (S18 directory research)"))
            n += 1
    for key, upd in S18_UPDATES.items():
        c = have.get(key)
        if c is None or c.updated_by not in ("System (initial directory)", "System (S14 seed)"):  # unedited only
            continue
        before = {k: getattr(c, k) for k in upd}
        for k, v in upd.items():
            setattr(c, k, v)
        c.updated_by = "System (S18 directory research)"
        audit.record(s, "System", "provider.verified", None, None, contact=c.organization, previous=before, new=upd)
        n += 1
    return n


# Always offered, needs no number: the barangay is the first office for a local concern.
BARANGAY_GUIDANCE = {"organization": "Your barangay hall", "org_type": "barangay",
                     "assistance": "Local concerns about poles and wires in the barangay; it can coordinate with the "
                                   "utility and the city.",
                     "instructions": "Visit or call your barangay hall and share the report.", "verification": None}


def seed(s: Session) -> int:
    have = set(s.scalars(select(Contact.seed_key).where(Contact.seed_key.is_not(None))).all())
    n = 0
    for row in SEED:
        if row["seed_key"] not in have:
            s.add(Contact(**row, updated_by="System (initial directory)"))
            n += 1
    return n


# S18 (Phase 15): the directory's geographic scope. The thesis places the study at De La Salle University Dasmariñas and
# the surrounding Cavite corridor municipalities but does not list them; the project owner kept the municipalities
# already in the directory (19 Nov 2026). UNCONFIRMED: replace with the thesis sampling list when it is available.
STUDY_AREA = ("City of Dasmariñas", "City of Imus", "Silang", "Gen. Mariano Alvarez", "City of General Trias",
              "City of Trece Martires", "Tanza", "Naic")
DIRECTORY_UPDATED = "November 19, 2026"


def area_label(c: Contact) -> str:
    """What the Area column says: only the coverage the recorded source supports, never a wider area. A national
    licence or a province listing is not shown as coverage of a town."""
    cities = [x for x in (c.cities or []) if x != "national" and not x.startswith("province:")]
    provinces = [x.split(":", 1)[1] for x in (c.cities or []) if x.startswith("province:")]
    if c.org_type == "emergency" and "national" in (c.cities or []):
        return "Nationwide emergency hotline"
    if cities:
        return ", ".join(cities)
    if provinces:
        return f"{', '.join(provinces)}: province listed; towns not published"
    return "Coverage in the study area not published"


def view(c: Contact, staff: bool = False) -> dict[str, Any]:
    out = {"id": c.id, "organization": c.organization, "org_type": c.org_type, "service_area": c.service_area,
           "phone": c.phone, "url": c.url, "assistance": c.assistance, "instructions": c.instructions,
           "verification": c.verification, "last_verified": c.last_verified, "source_url": c.source_url,
           # S18
           "category": category_of(c), "category_label": CATEGORY_LABEL.get(category_of(c)),
           "registered_name": c.registered_name, "previous_names": c.previous_names or [], "email": c.email,
           "reporting_url": c.reporting_url, "source_type": c.source_type, "coverage_basis": c.coverage_basis,
           "area_label": area_label(c)}
    if staff:
        out |= {"cities": c.cities, "barangays": c.barangays, "source_note": c.source_note,
                "admin_notes": c.admin_notes, "active": c.active,
                "seed_key": c.seed_key, "updated_by": c.updated_by,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None}
    return out


def city_key(name: str | None) -> str:
    """One key for every way a city or municipality is written: "City of Dasmariñas", "Dasmariñas City",
    "dasmarinas", "Municipality of Rosario" all reduce to the bare place name (accents and case removed)."""
    from app.services import barangay as brgy
    t = re.sub(r"(?i)^\s*(city|municipality)\s+of\s+", "", name or "")
    t = re.sub(r"(?i)\s+(city|municipality)\s*$", "", t)
    return brgy._key(t)


def city_of(barangay: str | None, city: str | None) -> str | None:
    """The PSGC city / municipality of the inspection: from its validated barangay, else from the typed city."""
    from app.services import barangay as brgy
    ck = city_key(city) if city else ""
    if barangay:
        name = brgy.resolve(barangay, city)
        hits = brgy._index()[0].get(brgy._key(name)) if name else None
        if hits:
            if ck and len(hits) > 1:
                for h in hits:
                    if ck == city_key(h["city"]):
                        return h["city"]
            return hits[0]["city"]
    if ck:
        for r in brgy._index()[1]:
            if city_key(r["city"]) == ck:
                return r["city"]
    return None


def location_check(barangay: str | None, city: str | None) -> dict[str, Any]:
    """S16: does the typed city agree with the barangay's PSGC city? A disagreement is reported for review, never
    corrected silently (a typed or verified value is not overwritten by a lookup)."""
    from app.services import barangay as brgy
    if not (barangay and city):
        return {"consistent": None}
    name = brgy.resolve(barangay, city)
    hits = brgy._index()[0].get(brgy._key(name)) if name else None
    if not hits:
        return {"consistent": None}
    ck = city_key(city)
    if any(city_key(h["city"]) == ck for h in hits):
        return {"consistent": True}
    return {"consistent": False, "barangay": name, "barangay_cities": sorted({h["city"] for h in hits}), "city": city,
            "message": f"Barangay {name} is in {', '.join(sorted({h['city'] for h in hits}))}, not {city}."}


ORDER = {"emergency": 0, "barangay": 1, "electric_utility": 2, "telecom_provider": 3, "city_government": 4, "other": 5}


# Which organisations deal with which wire type (a wire's predicted type; never proof of who owns it)
TYPE_ORGS = {"power-distribution-lines": "electric_utility", "telecom-cable": "telecom_provider"}


PROVINCE = "Cavite"  # the PSGC list shipped with WIREFRAME (app/data/barangays_cavite.json) covers this province


def _covers(c: Contact, pk: str, barangay: str | None) -> bool:
    for x in c.cities or []:
        if x == "national":
            continue
        if x.startswith("province:"):
            if pk and city_key(x.split(":", 1)[1]) == city_key(PROVINCE):
                return True
        elif pk and city_key(x) == pk:
            return not (c.barangays and barangay and barangay not in c.barangays)
    return False


RELATION_TEXT = {
    "confirmed_responsible": "Confirmed responsible provider",
    "serves_area": "Provider serving this area",
    "province_listed": "Lists this province as a service area",
    "nationwide": "Nationwide provider; local availability not verified",
}


def providers(s: Session, barangay: str | None, city: str | None, wire_classes: set[str] | None = None,
              pole_id: int | None = None) -> dict[str, Any]:
    """S18: location-aware provider directory, grouped by category. Coverage is never ownership: a provider is
    ``confirmed_responsible`` only through a PoleAttachment entered from an authoritative record."""
    from app.models.orm import PoleAttachment
    place = city_of(barangay, city)
    pk = city_key(place) if place else ""
    rows = s.scalars(select(Contact).where(Contact.active.is_(True),
                                           Contact.verification.in_(("verified", "secondary_source")))).all()
    confirmed = {}
    if pole_id is not None:
        for pa in s.scalars(select(PoleAttachment).where(PoleAttachment.pole_id == pole_id)).all():
            confirmed[pa.contact_id] = {"record_type": pa.record_type, "record_reference": pa.record_reference,
                                        "wire_class": pa.wire_class, "verified_by": pa.verified_by,
                                        "verified_at": pa.verified_at.isoformat()}
    wanted = set().union(*(WIRE_CATEGORIES.get(k, set()) for k in (wire_classes or set()))) if wire_classes else set()
    unidentified = bool(wire_classes) and any(k not in WIRE_CATEGORIES for k in wire_classes)
    items = []
    for c in rows:
        cat = category_of(c)
        if cat in ("emergency", "government"):
            continue
        if c.id in confirmed:
            rel = "confirmed_responsible"
        elif _covers(c, pk, barangay):
            rel = "province_listed" if all(x.startswith("province:") for x in c.cities if x != "national") \
                else "serves_area"
        elif "national" in (c.cities or []):
            rel = "nationwide"
        else:
            continue
        items.append(view(c) | {"relation": rel, "relation_text": RELATION_TEXT[rel],
                                "relevant": not wanted or cat in wanted, "confirmed": confirmed.get(c.id)})
    rank = {"confirmed_responsible": 0, "serves_area": 1, "province_listed": 2, "nationwide": 3}
    items.sort(key=lambda x: (rank[x["relation"]], not x["relevant"], x["organization"]))
    local = [x for x in items if x["relation"] != "nationwide"]
    groups = []
    for cat in CATEGORY_ORDER:
        g = [x for x in local if x["category"] == cat]
        if g:
            groups.append({"category": cat, "label": CATEGORY_LABEL[cat], "relevant": not wanted or cat in wanted,
                           "items": g})
    groups.sort(key=lambda g: not g["relevant"])
    if not place:
        gap = "The location could not be matched to a city or municipality, so only nationwide providers are shown."
    elif not local:
        gap = f"No provider with documented coverage for {place} is in the directory yet. That does not mean none " \
              "operates there."
    else:
        gap = None
    return {"place": {"barangay": barangay, "city": place, "province": PROVINCE if place else None,
                      "resolved": bool(place), "input_city": city},
            "groups": groups, "nationwide": [x for x in items if x["relation"] == "nationwide"],
            "relevant_categories": sorted(wanted), "unidentified_wires": unidentified, "gap": gap,
            "ownership_note": "Area coverage shows which companies operate here, not who owns a particular wire. "
                              "Ownership is confirmed only from an asset record or attachment agreement."}


def suggest(s: Session, barangay: str | None, city: str | None, wire_classes: set[str] | None = None) -> dict[str, Any]:
    """S16: the one contact resolution used by the result page, the PDF and the admin preview. Public-safe (verified
    and secondary-source rows only). Matches on the PSGC city of the inspection (any spelling of it), marks contacts
    that fit the wire types in the photo, and says plainly when the location itself is missing."""
    place = city_of(barangay, city)
    rows = s.scalars(select(Contact).where(Contact.active.is_(True),
                                           Contact.verification.in_(("verified", "secondary_source")))).all()
    pk = city_key(place) if place else ""
    local, national = [], []
    for c in rows:
        if pk and any(city_key(x) == pk for x in (c.cities or []) if x != "national"):
            if c.barangays and barangay and barangay not in c.barangays:
                continue
            local.append(c)
        elif "national" in (c.cities or []):
            national.append(c)
    wanted = {TYPE_ORGS[k] for k in (wire_classes or set()) if k in TYPE_ORGS}

    def relevant(c) -> bool:
        return c.org_type in ("emergency", "barangay", "city_government") or not wanted or c.org_type in wanted

    picked = sorted(local + national, key=lambda c: (ORDER.get(c.org_type, 9), not relevant(c), c.organization))
    out = [view(c) | {"match": "area" if c in local else "national", "relevant": relevant(c)} for c in picked]
    out.insert(1 if out and out[0]["org_type"] == "emergency" else 0,
               {**BARANGAY_GUIDANCE, "barangay": barangay, "match": "always", "relevant": True})
    has_utility = any(c.org_type == "electric_utility" for c in local)
    if not place:
        gap = "Add the barangay or city to the inspection to see the electric utility and local offices for it."
    elif not has_utility:
        gap = f"The directory has no electric utility listed for {place} yet."
    else:
        gap = ""
    return {"place": {"barangay": barangay, "city": place, "resolved": bool(place)}, "contacts": out,
            "location": location_check(barangay, city),
            "note": ("Suggested contacts for this location. A listed company is a suggestion, not confirmation of who "
                     "owns a wire: the photo shows a wire's likely type, not its operator. " + gap).strip()}


def for_analysis(s: Session, a) -> dict[str, Any]:
    """The contacts for one inspection: every view (page, PDF, citizen assistant) calls this, never its own match."""
    kinds = {d.wire_class for d in a.detections if d.status != "service_drop"}
    out = suggest(s, a.pole.barangay, a.city, kinds)
    uncertain = any("class_uncertain" in (d.flags_json or []) for d in a.detections)
    out["providers"] = providers(s, a.pole.barangay, a.city, kinds | ({"uncertain"} if uncertain else set()),
                                 a.pole_id)
    return out


# --------------------------------------------------------------------------- admin validation

PHONE = re.compile(r"^[0-9+()\-\s/;,.:A-Za-z]{2,80}$")


class ContactError(ValueError):
    pass


def validate(data: dict[str, Any]) -> dict[str, Any]:
    d = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}
    if not d.get("organization"):
        raise ContactError("Organization is required.")
    if d.get("org_type") not in CONTACT_TYPES:
        raise ContactError(f"Type must be one of: {', '.join(CONTACT_TYPES)}.")
    if d.get("verification") not in VERIFICATION:
        raise ContactError(f"Verification must be one of: {', '.join(VERIFICATION)}.")
    if d.get("phone") and (not PHONE.match(d["phone"]) or not re.search(r"\d{3}", d["phone"])):
        raise ContactError("The phone number does not look like a number.")
    for k in ("url", "source_url", "reporting_url"):
        if d.get(k) and not re.match(r"^https://[^\s/$.?#].[^\s]*$", d[k]):
            raise ContactError(f"{'Reporting URL' if k == 'url' else 'Source URL'} must be an https:// address.")
    if d["verification"] != "unverified":
        if not (d.get("phone") or d.get("url")):
            raise ContactError("A verified contact needs a phone number or a reporting URL.")
        if not d.get("source_url"):
            raise ContactError("A verified or secondary-source contact needs the source URL it was checked against.")
        d["last_verified"] = d.get("last_verified") or date.today().isoformat()
    if d.get("last_verified") and not re.match(r"^\d{4}-\d{2}-\d{2}$", d["last_verified"]):
        raise ContactError("Last verified must be YYYY-MM-DD.")
    d["cities"] = [c for c in (d.get("cities") or []) if isinstance(c, str) and c.strip()]
    d["barangays"] = [b for b in (d.get("barangays") or []) if isinstance(b, str) and b.strip()]
    if not d["cities"]:
        raise ContactError('Add at least one city / municipality, or "national".')
    return d


def duplicate(s: Session, d: dict, exclude_id: int | None = None) -> Contact | None:
    key = re.sub(r"\W+", "", d["organization"].lower())
    for c in s.scalars(select(Contact)).all():
        if c.id != exclude_id and re.sub(r"\W+", "", c.organization.lower()) == key and set(c.cities) & set(d["cities"]):
            return c
    return None
