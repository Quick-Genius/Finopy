"""Apply db/migrations/*.sql in order, once each, as the owner role. `python -m app.migrate`."""
import pathlib
import sys

import psycopg

from . import config

MIG_DIR = pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations"
if not MIG_DIR.exists():
    MIG_DIR = pathlib.Path("/db/migrations")


def run() -> None:
    with psycopg.connect(config.OWNER_DATABASE_URL, autocommit=False) as conn:
        conn.execute("create table if not exists schema_migrations (name text primary key, applied_at timestamptz default now())")
        done = {r[0] for r in conn.execute("select name from schema_migrations")}
        for f in sorted(MIG_DIR.glob("*.sql")):
            if f.name in done:
                continue
            conn.execute(f.read_text())
            conn.execute("insert into schema_migrations (name) values (%s)", (f.name,))
            print(f"applied {f.name}")
        conn.commit()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # fail loudly so compose does not start the API on a half-migrated DB
        print(f"migration failed: {e}", file=sys.stderr)
        sys.exit(1)
