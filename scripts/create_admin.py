"""Create or reset an admin account for the command centre.

    python -m scripts.create_admin engr.riazon --name "Engr. C. Riazon"

The password is read from the terminal (never from arguments or files) and must be at least
12 characters. There is no default account.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import Database
from app.core.security import MIN_PASSWORD, hash_password
from app.models.orm import AdminSession, AdminUser


def upsert_admin(db: Database, username: str, password: str, display_name: str | None = None) -> str:
    username = username.strip().lower()
    with db.session() as s:
        u = s.scalar(select(AdminUser).where(AdminUser.username == username))
        if u is None:
            s.add(AdminUser(username=username, display_name=display_name, password_hash=hash_password(password)))
            return "created"
        u.password_hash, u.active = hash_password(password), True
        if display_name:
            u.display_name = display_name
        for sess in s.scalars(select(AdminSession).where(AdminSession.user_id == u.id)):
            s.delete(sess)  # a reset signs the account out everywhere
        return "reset"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("username")
    ap.add_argument("--name", help="display name shown on reviews and reports")
    a = ap.parse_args()
    pw = getpass.getpass(f"Password for {a.username} (min {MIN_PASSWORD} chars): ")
    if pw != getpass.getpass("Repeat: "):
        sys.exit("passwords do not match")
    settings = get_settings()
    settings.ensure_dirs()
    db = Database(settings.resolved_database_url)
    db.create_all()
    try:
        print(f"admin {a.username!r} {upsert_admin(db, a.username, pw, a.name)}")
    except ValueError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
