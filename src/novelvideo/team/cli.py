"""Explicit team database provisioning: python -m novelvideo.team.cli."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import sqlite3
import stat

import psycopg
from psycopg import sql

from novelvideo.team.store import TeamStore


def map_path(value: str, path_maps: list[str]) -> str:
    mappings = []
    for item in path_maps:
        old, separator, new = item.partition("=")
        if not separator or not Path(old).is_absolute() or not Path(new).is_absolute():
            raise ValueError("Path maps must be absolute OLD=NEW prefixes")
        mappings.append((Path(old), Path(new)))
    for old, new in sorted(mappings, key=lambda pair: len(pair[0].parts), reverse=True):
        try:
            relative = Path(value).relative_to(old)
        except ValueError:
            continue
        return str(new / relative)
    return value


def migrate_ce(store: TeamStore, source: Path, owner: str, path_maps: list[str] | None = None) -> int:
    """Atomically import registry metadata only; never move or overwrite project files."""
    user = store.get_user(username=owner)
    if not user or not user["enabled"] or user["role"] != "admin":
        raise ValueError("Migration owner must be an enabled administrator")
    if not source.is_file():
        raise ValueError("CE registry does not exist")
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM projects")]
    imported = 0
    with store.connect() as db:
        db.execute("LOCK TABLE team_projects IN SHARE ROW EXCLUSIVE MODE")
        for row in rows:
            row.update(owner_type="user", owner_id=user["id"], home_node_id="local")
            for key in ("output_dir", "state_dir", "runtime_dir"):
                row[key] = map_path(row[key], path_maps or [])
            existing = db.execute("SELECT * FROM team_projects WHERE id=%s OR (owner_id=%s AND name=%s)", (row["id"], user["id"], row["name"])).fetchall()
            if existing:
                if len(existing) != 1 or existing[0] != row:
                    raise ValueError(f"Migration conflict for project {row['id']}; no projects imported")
                continue
            try:
                db.execute(sql.SQL("INSERT INTO team_projects ({}) VALUES ({})").format(
                    sql.SQL(",").join(map(sql.Identifier, row)),
                    sql.SQL(",").join(sql.Placeholder() for _ in row)), list(row.values()))
            except psycopg.IntegrityError as exc:
                raise ValueError(f"Migration conflict for project {row['id']}") from exc
            imported += 1
    return imported


def read_password_file(path: Path) -> str:
    # Open first with O_NOFOLLOW, then inspect the same descriptor to avoid symlink races.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Password file must be a regular file owned by this user with mode 0600")
        password = stream.read(1024).rstrip("\r\n")
    return password


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    admin = commands.add_parser("create-admin")
    admin.add_argument("--username", required=True)
    admin.add_argument("--password-file", type=Path)
    migration = commands.add_parser("migrate-ce")
    migration.add_argument("--owner", required=True)
    migration.add_argument("--source", type=Path)
    migration.add_argument("--path-map", action="append", default=[], metavar="OLD=NEW", help="Translate an absolute directory prefix; repeatable, does not move files")
    args = parser.parse_args(argv)
    if os.environ.get("ST_EDITION") != "team" or os.environ.get("ST_CONTROL_PLANE_DSN"):
        parser.error("Set ST_EDITION=team and ST_TEAM_DATABASE_URL; ST_CONTROL_PLANE_DSN must be unset")
    store = TeamStore()
    store.init_db()
    try:
        if args.command == "create-admin":
            password = read_password_file(args.password_file) if args.password_file else getpass.getpass("New password (12–128 characters): ")
            if not args.password_file and password != getpass.getpass("Confirm password: "):
                raise ValueError("Passwords do not match")
            user = store.create_user(args.username, password, "admin")
            print(f"Administrator created: {user['username']}")
        elif args.command == "migrate-ce":
            from novelvideo import config
            source = args.source or Path(config.STATE_DIR) / "local" / "projects.db"
            print(f"Imported {migrate_ce(store, source, args.owner, args.path_map)} projects; IDs preserved, files unchanged")
        else:
            print("Team database initialized")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
