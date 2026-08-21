from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

if settings.is_sqlite:
    # SQLite runs inside this process rather than over a socket, so the MySQL pool
    # options don't apply. check_same_thread=False is required because FastAPI serves
    # requests from a thread pool and a Session may be used off the creating thread.
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(conn, _record):
        cur = conn.cursor()
        # WAL lets the daily-digest thread read while a request writes, instead of
        # the two blocking each other; FK enforcement is off by default in SQLite.
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=8000")
        # NO memory-mapped database file. This is what makes an external drive
        # survivable: with mmap on, unplugging the drive mid-run faults the mapping
        # and the process dies with a SIGBUS ("the backing vnode was force
        # unmounted") that no `except` can catch and that can corrupt the file.
        # With mmap off, the same unplug raises an ordinary "disk I/O error" that
        # is catchable, and the drive watchdog (main.py) stops the app cleanly
        # before it comes to that. Keep it 0.
        cur.execute("PRAGMA mmap_size=0")
        # Fold the WAL back into the main file often, so at any instant almost all
        # the records live in the one .db file rather than a side WAL that a yank
        # could strand.
        cur.execute("PRAGMA wal_autocheckpoint=200")
        cur.close()
else:
    engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
