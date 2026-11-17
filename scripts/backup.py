"""Full backup, verification and restore of WIREFRAME's data directory (register D-13, S6).

    python -m scripts.backup create  --dest <dir> [--data <var dir>]
    python -m scripts.backup verify  <backup dir>
    python -m scripts.backup restore <backup dir> --to <new empty dir>

What is backed up: the SQLite database (online backup API: consistent while the server runs, WAL included), and
every file under uploads/, renders/ and reports/. Not backed up: var/backups/ and *.bak* files (earlier migration
snapshots, which are themselves backups) and the -wal/-shm side files (folded into the online copy).

Safety: a backup is written to a temporary directory inside --dest, every file is fsynced, the database copy passes
``PRAGMA integrity_check`` and ``foreign_key_check``, the SHA-256 manifest is written last, and only then is the
directory renamed to its final name (atomic on one filesystem). Any failure removes the temporary directory and exits
non-zero; nothing is ever deleted outside it. ``restore`` refuses a target that exists and is not empty, verifies the
backup before copying, and verifies the restored copy against the manifest afterwards. No retention policy is applied:
every backup is kept (no operational basis for deleting one has been given).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

DB = "wireframe.db"
DIRS = ("uploads", "renders", "reports")
MANIFEST = "manifest.json"


class BackupError(RuntimeError):
    pass


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync(p: Path) -> None:
    fd = os.open(p, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def db_check(path: Path) -> dict:
    """Integrity, foreign keys and row counts of a database file (opened read-only)."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        rows = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()
    return {"integrity": integrity, "foreign_key_violations": len(fk), "rows": rows}


def _files(root: Path) -> list[Path]:
    out = []
    for d in DIRS:
        base = root / d
        if base.is_dir():
            out += [p for p in sorted(base.rglob("*")) if p.is_file() and not p.is_symlink()]
    return out


def create(data: Path, dest: Path) -> Path:
    data, dest = data.resolve(), dest.resolve()
    if not (data / DB).is_file():
        raise BackupError(f"no database at {data / DB}")
    if dest == data or data in dest.parents:
        raise BackupError("the destination must be outside the data directory")
    dest.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    final = dest / f"wireframe-backup-{stamp}"
    if final.exists():
        raise BackupError(f"{final} already exists")
    tmp = dest / f".tmp-{final.name}-{os.getpid()}"
    tmp.mkdir()
    try:
        src = sqlite3.connect(f"file:{data / DB}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp / DB)
        try:
            src.backup(dst)
            # one self-contained file: a WAL-mode copy would grow -wal/-shm side files whenever it is read
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
            src.close()
        _fsync(tmp / DB)
        check = db_check(tmp / DB)
        if check["integrity"] != "ok" or check["foreign_key_violations"]:
            raise BackupError(f"database copy failed its checks: {check['integrity']}, {check['foreign_key_violations']} FK violations")
        entries = [{"path": DB, "bytes": (tmp / DB).stat().st_size, "sha256": _sha256(tmp / DB)}]
        for p in _files(data):
            rel = p.relative_to(data)
            out = tmp / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, out)
            _fsync(out)
            digest = _sha256(out)
            if digest != _sha256(p):
                raise BackupError(f"{rel} changed while it was copied")
            entries.append({"path": rel.as_posix(), "bytes": out.stat().st_size, "sha256": digest})
        manifest = {"format": 1, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "source": str(data),
                    "database": check, "files": len(entries), "bytes": sum(e["bytes"] for e in entries),
                    "retention": "keep all (no retention policy configured)", "entries": entries}
        (tmp / MANIFEST).write_text(json.dumps(manifest, indent=1))
        _fsync(tmp / MANIFEST)
        os.replace(tmp, final)
        _fsync(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


def _safe(rel: str) -> str:
    """A manifest path must stay inside the backup: no absolute paths, no '..' (a tampered manifest must never make
    verify read, or restore write, outside its directory)."""
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or rel != p.as_posix() or not rel:
        raise BackupError(f"unsafe path in manifest: {rel!r}")
    return rel


def verify(backup: Path) -> dict:
    m = json.loads((backup / MANIFEST).read_text())
    for e in m["entries"]:
        _safe(e["path"])
    bad = [e["path"] for e in m["entries"] if not (backup / e["path"]).is_file() or _sha256(backup / e["path"]) != e["sha256"]]
    listed = {e["path"] for e in m["entries"]} | {MANIFEST}
    extra = [p.relative_to(backup).as_posix() for p in backup.rglob("*") if p.is_file() and p.relative_to(backup).as_posix() not in listed]
    check = db_check(backup / DB)
    ok = not bad and not extra and check["integrity"] == "ok" and not check["foreign_key_violations"] and check["rows"] == m["database"]["rows"]
    return {"ok": ok, "files": len(m["entries"]), "mismatched": bad, "unlisted": extra, "database": check}


def restore(backup: Path, to: Path) -> dict:
    to = to.resolve()
    if to.exists() and any(to.iterdir()):
        raise BackupError(f"{to} exists and is not empty; restore never overwrites")
    pre = verify(backup)
    if not pre["ok"]:
        raise BackupError(f"backup failed verification: {pre}")
    m = json.loads((backup / MANIFEST).read_text())
    to.mkdir(parents=True, exist_ok=True)
    for e in m["entries"]:
        out = to / e["path"]
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / e["path"], out)
    bad = [e["path"] for e in m["entries"] if _sha256(to / e["path"]) != e["sha256"]]
    check = db_check(to / DB)
    if bad or check["integrity"] != "ok" or check["rows"] != m["database"]["rows"]:
        raise BackupError(f"restored copy does not match the backup: {bad or check}")
    return {"ok": True, "files": len(m["entries"]), "database": check}


def main(argv: list[str] | None = None) -> int:
    from app.core.config import ROOT_DIR
    ap = argparse.ArgumentParser(prog="python -m scripts.backup", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--dest", type=Path, required=True)
    c.add_argument("--data", type=Path, default=Path(os.environ.get("WIREFRAME_DATA_DIR", ROOT_DIR / "var")))
    v = sub.add_parser("verify")
    v.add_argument("backup", type=Path)
    r = sub.add_parser("restore")
    r.add_argument("backup", type=Path)
    r.add_argument("--to", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "create":
            out = create(a.data, a.dest)
            res = verify(out)
            print(json.dumps({"backup": str(out), **{k: res[k] for k in ("ok", "files")}, "rows": res["database"]["rows"]}, indent=1))
            return 0 if res["ok"] else 1
        res = verify(a.backup) if a.cmd == "verify" else restore(a.backup, a.to)
        print(json.dumps(res, indent=1))
        return 0 if res["ok"] else 1
    except BackupError as e:
        print(f"backup error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
