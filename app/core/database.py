"""SQLAlchemy engine and session management.

SQLite enforces foreign keys only when ``PRAGMA foreign_keys = ON`` is issued on each
connection, so it is set in a ``connect`` event for every pooled connection, and
verified at startup (``assert_foreign_keys``). WAL mode lets the API read while the
worker writes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

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
    """
    if engine.dialect.name != "sqlite":
        return
    from app.core.config import LEGACY_CLASS_NAMES
    from app.models.orm import Detection

    with engine.begin() as conn:
        add_missing_columns(conn)
        ddl = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='detections'")).scalar()
    if not ddl or not (any(f"'{old}'" in ddl for old in LEGACY_CLASS_NAMES) or "'service_drop'" not in ddl):
        return
    raw = engine.raw_connection()  # foreign_keys must be switched off outside a transaction
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA foreign_keys = OFF")
        cur.execute("BEGIN")
        cur.execute("ALTER TABLE detections RENAME TO detections_legacy")
        for (name,) in cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND "
                                   "tbl_name='detections_legacy' AND sql IS NOT NULL").fetchall():
            cur.execute(f'DROP INDEX "{name}"')
        from sqlalchemy.schema import CreateIndex, CreateTable
        dialect = engine.dialect
        cur.execute(str(CreateTable(Detection.__table__).compile(dialect=dialect)))
        for ix in Detection.__table__.indexes:
            cur.execute(str(CreateIndex(ix).compile(dialect=dialect)))
        names = [c.name for c in Detection.__table__.columns]
        case = "CASE wire_class " + " ".join(f"WHEN '{o}' THEN '{n}'" for o, n in LEGACY_CLASS_NAMES.items()) \
            + " ELSE wire_class END"
        sel = ", ".join(case if n == "wire_class" else n for n in names)
        cur.execute(f"INSERT INTO detections ({', '.join(names)}) SELECT {sel} FROM detections_legacy")
        cur.execute("DROP TABLE detections_legacy")
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
