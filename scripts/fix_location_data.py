"""v2.21 controlled correction of stored barangays and a check of stored coordinates.

    python -m scripts.fix_location_data            # dry run: lists what would change
    python -m scripts.fix_location_data --apply    # backs up the database, then applies

Barangays: a stored value that matches an official barangay under another spelling is rewritten to the official
name ("anabu 2-d" -> "Anabu II-D"); a value that is not a barangay at all (a person's name, "Test Barangay") is
cleared, so the pole shows as Unassigned. Every change is written to the audit trail with the old value, so nothing
is lost. Coordinates are only reported, never changed or deleted: duplicates are legitimate (GPS precision), and an
unusable point needs a person to re-pin the pole.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import Database
from app.models.orm import Pole, PoleLocation
from app.services import audit, geo
from app.services.barangay import resolve


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="change the database (a backup is made first)")
    args = ap.parse_args()
    st = get_settings()
    url = st.resolved_database_url
    if args.apply and url.startswith("sqlite:///"):
        src = url.removeprefix("sqlite:///")
        dst = f"{src}.before-v221-{datetime.now():%Y%m%d-%H%M%S}.bak"
        shutil.copy2(src, dst)
        print(f"backup: {dst}")
    db = Database(url)
    db.create_all()
    changes = 0
    with db.session() as s:
        for p in s.scalars(select(Pole).order_by(Pole.id)).all():
            if not p.barangay:
                continue
            official = resolve(p.barangay)
            if official == p.barangay:
                continue
            what = f"rename to {official!r}" if official else "clear (not a barangay)"
            print(f"barangay  {p.pole_code:<14} {p.barangay!r:<28} -> {what}")
            changes += 1
            if args.apply:
                audit.record(s, "System (data correction v2.21)", "barangay.set" if official else "barangay.cleared",
                             p.pole_code, None, old=p.barangay, new=official,
                             reason="not in the official barangay list" if not official else "official spelling")
                p.barangay = official
        seen: dict[tuple, list[str]] = {}
        for loc, code in s.execute(select(PoleLocation, Pole.pole_code).join(Pole, Pole.id == PoleLocation.pole_id)).all():
            bad = geo.problem(loc.latitude, loc.longitude)
            if bad:
                print(f"location  {code:<14} {loc.latitude}, {loc.longitude}: {bad} (left as is; re-pin the pole)")
            seen.setdefault((round(loc.latitude, 6), round(loc.longitude, 6)), []).append(code)
        for pt, codes in seen.items():
            if len(codes) > 1:
                print(f"shared    {pt[0]}, {pt[1]}: {', '.join(codes)} (kept; clustered on the map)")
        if not args.apply:
            s.rollback()
    print(f"{changes} barangay change(s) {'applied' if args.apply else 'found (dry run; add --apply)'}")


if __name__ == "__main__":
    main()
