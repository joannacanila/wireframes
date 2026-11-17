"""SQLAlchemy engine and session management.

SQLite enforces foreign keys only when ``PRAGMA foreign_keys = ON`` is issued on each
connection, so it is set in a ``connect`` event for every pooled connection, and
verified at startup (``assert_foreign_keys``). WAL mode lets the API read while the
worker writes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON")
    cur.execute("PRAGMA journal_mode = WAL")
    cur.execute("PRAGMA synchronous = NORMAL")
    cur.execute("PRAGMA busy_timeout = 5000")
    cur.close()


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if is_sqlite else {},
        future=True,
    )
    if is_sqlite:
        event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def assert_foreign_keys(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        if conn.execute(text("PRAGMA foreign_keys")).scalar() != 1:
            raise RuntimeError("SQLite foreign key enforcement is not active")


def _sql_default(col) -> str | None:
    """A literal SQL DEFAULT for an added column: the server default, else the ORM's scalar default."""
    import json as _json
    if col.server_default is not None and hasattr(col.server_default, "arg"):
        arg = col.server_default.arg
        return arg if isinstance(arg, str) and arg.startswith("'") else f"'{arg}'" if isinstance(arg, str) else str(arg)
    d = getattr(col.default, "arg", None) if col.default is not None else None
    if callable(d):  # e.g. default=list / dict for JSON columns (SQLAlchemy passes a context argument)
        try:
            d = d(None)
        except Exception:
            return None
        if not isinstance(d, (dict, list, str, int, float, bool)):
            return None
    if d is None:
        return None
    if isinstance(d, bool):
        return "1" if d else "0"
    if isinstance(d, (int, float)):
        return repr(d)
    if isinstance(d, (dict, list)):
        d = _json.dumps(d)
    return "'" + str(d).replace("'", "''") + "'"


def add_missing_columns(conn) -> list[str]:
    """ALTER TABLE ... ADD COLUMN for every ORM column missing from an existing SQLite table.

    Existing rows get the column's default (server default, or the ORM's scalar default). A NOT NULL
    column without any default cannot be added in place and raises, at startup, with its name,
    instead of failing every request later. Returns the added "table.column" names."""
    from sqlalchemy.schema import CreateColumn

    from app.models import orm  # noqa: F401  (register tables)
    added = []
    for table in Base.metadata.sorted_tables:
        have = {r[1] for r in conn.execute(text(f'PRAGMA table_info("{table.name}")'))}
        if not have:
            continue  # created by create_all
        for col in table.columns:
            if col.name in have:
                continue
            spec = str(CreateColumn(col).compile(dialect=conn.dialect))
            spec = spec.replace(" NOT NULL", "")  # re-added below with a default
            default = _sql_default(col)
            if not col.nullable:
                if default is None:
                    raise RuntimeError(f"cannot migrate {table.name}.{col.name}: NOT NULL without a default")
                spec += f" NOT NULL DEFAULT {default}"
            elif default is not None:
                spec += f" DEFAULT {default}"
            conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN {spec}'))
            added.append(f"{table.name}.{col.name}")
    return added


def migrate(engine: Engine) -> None:
    """In-place upgrades of databases created by earlier versions (SQLite; no-ops when current).

    * every column the ORM declares but an existing table lacks is added (``add_missing_columns``;
      v2.10). Earlier versions handled one column by name (analyses.vegetation_json), so a database
      created before the dual portals (analyses.submitted_via) failed every upload with HTTP 500.
    * detections whose CHECK constraint still lists the old class names are rebuilt with the
      current constraint, and stored class names are renamed (fiber_optic_drop -> telecom-cable,
      dead_wire -> abandoned-wire, ...). Scores are kept as computed.
    * v2.9: the same rebuild adds the ``service_drop`` detection status to the constraint.
    * v2.21: and the ``not_assessed`` status (endpoint unavailable, uncertain type, no reference for the class).
    * v2.19: reviews are rebuilt to allow the ``flagged`` decision; analyses gain ``record_kind`` (operational /
      test), backfilled for older rows; admin users gain ``role`` (existing accounts stay administrators). A copy
      of the database is written to ``backups/`` before any of these changes.
    """
    if engine.dialect.name != "sqlite":
        return
    from app.core.config import LEGACY_CLASS_NAMES
    from app.models.orm import Detection, Review
    from app.services.records import backfill

    with engine.connect() as conn:
        missing = _missing_columns(conn)
        det = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='detections'")).scalar()
        rev = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='reviews'")).scalar()
    rebuild_det = bool(det) and (any(f"'{old}'" in det for old in LEGACY_CLASS_NAMES) or "'service_drop'" not in det
                                 or "'not_assessed'" not in det)  # v2.21: measured but not assessed
    rebuild_rev = bool(rev) and "'flagged'" not in rev  # v2.19: "flag for further inspection"
    if missing or rebuild_det or rebuild_rev:
        backup_before_migration(engine)
    with engine.begin() as conn:
        add_missing_columns(conn)
    if rebuild_det:
        case = "CASE wire_class " + " ".join(f"WHEN '{o}' THEN '{n}'" for o, n in LEGACY_CLASS_NAMES.items()) \
            + " ELSE wire_class END"
        _rebuild(engine, Detection.__table__, {"wire_class": case})
    if rebuild_rev:
        _rebuild(engine, Review.__table__, {})
    with engine.begin() as conn:
        backfill(conn)  # v2.19: operational / test classification of older rows (only rows not yet classified)


def _missing_columns(conn) -> list[str]:
    from app.models import orm  # noqa: F401
    out = []
    for table in Base.metadata.sorted_tables:
        have = {r[1] for r in conn.execute(text(f'PRAGMA table_info("{table.name}")'))}
        if have:
            out += [f"{table.name}.{c.name}" for c in table.columns if c.name not in have]
    return out


def backup_before_migration(engine: Engine) -> Path | None:
    """v2.19: a consistent copy of the database (SQLite online backup) in <data dir>/backups before any schema
    change, so every migration is recoverable. Returns the backup path (None for an in-memory database)."""
    import sqlite3
    from datetime import UTC, datetime

    db_path = engine.url.database
    if not db_path or db_path == ":memory:":
        return None
    src = Path(db_path)
    out = src.parent / "backups" / f"{src.stem}-pre-migration-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.db"
    out.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(src) as a, sqlite3.connect(out) as b:
        a.backup(b)
    return out


def _rebuild(engine: Engine, table, select_exprs: dict[str, str]) -> None:
    """Recreate ``table`` with the ORM's current DDL (CHECK constraints included) and copy every row; a column
    can be transformed on the way with an SQL expression. Foreign keys are verified before the commit."""
    from sqlalchemy.schema import CreateIndex, CreateTable

    name, legacy = table.name, f"{table.name}_legacy"
    raw = engine.raw_connection()  # foreign_keys must be switched off outside a transaction
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA foreign_keys = OFF")
        cur.execute("BEGIN")
        cur.execute(f'ALTER TABLE "{name}" RENAME TO "{legacy}"')
        for (ix,) in cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND "
                                 f"tbl_name='{legacy}' AND sql IS NOT NULL").fetchall():
            cur.execute(f'DROP INDEX "{ix}"')
        cur.execute(str(CreateTable(table).compile(dialect=engine.dialect)))
        for ix in table.indexes:
            cur.execute(str(CreateIndex(ix).compile(dialect=engine.dialect)))
        have = {r[1] for r in cur.execute(f'PRAGMA table_info("{legacy}")').fetchall()}
        names = [c.name for c in table.columns if c.name in have]
        sel = ", ".join(select_exprs.get(n, n) for n in names)
        cur.execute(f'INSERT INTO "{name}" ({", ".join(names)}) SELECT {sel} FROM "{legacy}"')
        cur.execute(f'DROP TABLE "{legacy}"')
        bad = cur.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"migration left dangling references: {bad[:3]}")
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        cur.execute("PRAGMA foreign_keys = ON")
        raw.close()


class Database:
    def __init__(self, url: str) -> None:
        self.engine = make_engine(url)
        self.SessionLocal = sessionmaker(self.engine, expire_on_commit=False, class_=Session)

    def create_all(self) -> None:
        from app.models import orm  # noqa: F401  (register tables)

        Base.metadata.create_all(self.engine)
        migrate(self.engine)
        assert_foreign_keys(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
