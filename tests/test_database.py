"""Database integrity: PRAGMA foreign_keys, cascades, CHECK constraints."""

import sqlite3
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import Database
from app.models.orm import Analysis, Detection, Pole, PoleLocation, Report


@pytest.fixture
def db(tmp_path):
    d = Database(f"sqlite:///{tmp_path / 't.db'}")
    d.create_all()
    return d


def seed(s, status="complete"):
    p = Pole(pole_code="P-1")
    s.add(p); s.flush()
    s.add(PoleLocation(pole_id=p.id, latitude=14.3, longitude=120.9, source="manual"))
    a = Analysis(pole_id=p.id, original_filename="x.jpg", stored_name="x1.jpg", status=status,
                 width=10, height=10, config_fingerprint="abc")
    s.add(a); s.flush()
    a.detections.append(Detection(wire_index=1, wire_class="telecom-cable", confidence=0.9, status="scored",
                                  bbox_json=[0, 0, 1, 1], polygons_json=[], centerline_json=[], curve_json=[],
                                  fit_json={}, crs=4.0, level="medium"))
    a.report = Report(file_name="r.pdf", sha256="0" * 64, config_fingerprint="abc")
    s.flush()
    return p, a


def counts(db):
    with db.engine.connect() as c:
        return {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                for t in ("poles", "pole_locations", "analyses", "detections", "reports")}


def test_foreign_keys_pragma_is_on_for_every_connection(db):
    for _ in range(3):
        with db.engine.connect() as c:
            assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_raw_sql_delete_cascades_at_database_level(db, tmp_path):
    with db.session() as s:
        _, a = seed(s)
        aid = a.id
    # a plain sqlite3 connection with FK on: the cascade is in the schema, not the ORM
    con = sqlite3.connect(tmp_path / "t.db")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("DELETE FROM analyses WHERE id = ?", (aid,))
    con.commit(); con.close()
    assert counts(db) == {"poles": 1, "pole_locations": 1, "analyses": 0, "detections": 0, "reports": 0}


def test_orm_delete_pole_cascades_everything(db):
    with db.session() as s:
        p, _ = seed(s)
    with db.session() as s:
        s.delete(s.get(Pole, p.id))
    assert set(counts(db).values()) == {0}


def test_orphan_detection_rejected(db):
    with pytest.raises(IntegrityError):
        with db.session() as s:
            s.add(Detection(analysis_id="nope", wire_index=1, wire_class="abandoned-wire", confidence=0.5,
                            status="too_short", bbox_json=[], polygons_json=[], centerline_json=[],
                            curve_json=[], fit_json={}))


@pytest.mark.parametrize("field,value", [("status", "done"), ("vni", 1.5), ("vni_band", "Extreme")])
def test_analysis_check_constraints(db, field, value):
    with pytest.raises(IntegrityError):
        with db.session() as s:
            p = Pole(pole_code="P-2"); s.add(p); s.flush()
            s.add(Analysis(pole_id=p.id, original_filename="x", stored_name="y.jpg", width=1, height=1,
                           config_fingerprint="a", **{field: value}))


@pytest.mark.parametrize("kw", [
    dict(wire_class="cable", status="scored", crs=1.0, level="low"),     # unknown class
    dict(wire_class="abandoned-wire", status="scored", crs=None, level=None),  # scored without CRS
    dict(wire_class="abandoned-wire", status="fit_failed", crs=2.0, level="low"),  # CRS on a failed fit
    dict(wire_class="abandoned-wire", status="scored", crs=11.0, level="high"),    # out of 0..10
])
def test_detection_check_constraints(db, kw):
    with pytest.raises(IntegrityError):
        with db.session() as s:
            _, a = seed(s)
            a.detections.append(Detection(wire_index=2, confidence=0.5, bbox_json=[], polygons_json=[],
                                          centerline_json=[], curve_json=[], fit_json={}, **kw))


def test_one_report_per_analysis(db):
    with pytest.raises(IntegrityError):
        with db.session() as s:
            _, a = seed(s)
            s.add(Report(analysis_id=a.id, file_name="b.pdf", sha256="1" * 64, config_fingerprint="x"))


def test_datetimes_round_trip_as_utc(db):
    with db.session() as s:
        _, a = seed(s)
        aid = a.id
    with db.session() as s:
        got = s.get(Analysis, aid).created_at
    assert got.tzinfo is not None and abs((datetime.now(UTC) - got).total_seconds()) < 60
