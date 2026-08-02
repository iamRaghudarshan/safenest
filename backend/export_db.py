"""Copy the whole the app database into a single portable SQLite file.

Used by the bundler so an existing MySQL install can move to another machine — the
resulting .db file needs no database server at all. Safe to run against a live
system: it only reads from the source.

    python export_db.py                      # -> data/finmate.db
    python export_db.py path/to/finmate.db   # explicit target

Re-running overwrites the target file.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, insert, select  # noqa: E402

from app import models  # noqa: F401,E402  — registers every table on Base.metadata
from app.config import BACKEND_DIR, settings  # noqa: E402
from app.database import Base, engine as source  # noqa: E402

# Rows are copied in batches so a large gallery doesn't build one enormous INSERT.
BATCH = 500


def export(target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    # A stale journal from an interrupted run would resurrect deleted rows.
    for suffix in ("-wal", "-shm"):
        side = target.with_name(target.name + suffix)
        if side.exists():
            side.unlink()

    dest = create_engine(f"sqlite:///{target.as_posix()}")
    Base.metadata.create_all(bind=dest)

    counts: dict[str, int] = {}
    with source.connect() as src, dest.begin() as dst:
        for table in Base.metadata.sorted_tables:
            total = 0
            result = src.execute(select(table))
            while True:
                chunk = result.fetchmany(BATCH)
                if not chunk:
                    break
                dst.execute(insert(table), [dict(r._mapping) for r in chunk])
                total += len(chunk)
            counts[table.name] = total
    dest.dispose()
    return counts


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else BACKEND_DIR / "data" / "finmate.db"
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()

    print(f"  source : {settings.db_engine} ({settings.db_name if not settings.is_sqlite else settings.sqlite_path})")
    print(f"  target : {target}")
    if settings.is_sqlite and settings.sqlite_path.resolve() == target.resolve():
        print("\n  Source and target are the same file — nothing to do.")
        return 1
    print()

    try:
        counts = export(target)
    except Exception as exc:
        print(f"  FAILED: {exc}")
        return 1

    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n:
            print(f"    {name:<22} {n:>7,} rows")
    empty = [n for n, c in counts.items() if not c]
    if empty:
        print(f"    ({len(empty)} empty tables created: {', '.join(sorted(empty))})")
    size = target.stat().st_size
    print(f"\n  Done — {sum(counts.values()):,} rows, {size / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
