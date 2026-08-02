"""Date expressions that work on both MySQL and SQLite.

The app normally runs on MySQL, but the portable bundle ships with SQLite so a copy
of the app can run on any machine without a database server. The two disagree on
every date function that matters here, so the queries go through these helpers
instead of naming a vendor's function directly.

    MySQL                              SQLite
    DATE_FORMAT(col, '%Y-%m')          strftime('%Y-%m', col)
    YEAR(col)                          CAST(strftime('%Y', col) AS INTEGER)
    MONTH(col)                         CAST(strftime('%m', col) AS INTEGER)

Each helper returns a SQLAlchemy expression that renders correctly for whichever
dialect is compiling it, so callers never branch on the backend themselves.
"""
from sqlalchemy import Integer, cast, func
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement
from sqlalchemy.types import String


class date_fmt(FunctionElement):
    """Format a date column with a strftime-style pattern (e.g. '%Y-%m')."""
    type = String()
    inherit_cache = True
    name = "date_fmt"


@compiles(date_fmt)
def _date_fmt_default(element, compiler, **kw):
    # ANSI-ish fallback; MySQL is the only dialect that reaches this in practice.
    col, fmt = list(element.clauses)
    return compiler.process(func.date_format(col, fmt), **kw)


@compiles(date_fmt, "sqlite")
def _date_fmt_sqlite(element, compiler, **kw):
    col, fmt = list(element.clauses)
    return compiler.process(func.strftime(fmt, col), **kw)


def ym(col):
    """'YYYY-MM' for grouping or filtering by month."""
    return date_fmt(col, "%Y-%m")


def md(col):
    """'MM-DD' — the 'on this day' key, ignoring the year."""
    return date_fmt(col, "%m-%d")


def year_of(col):
    """Four-digit year as an integer."""
    return cast(date_fmt(col, "%Y"), Integer)


def month_of(col):
    """Month number (1-12) as an integer."""
    return cast(date_fmt(col, "%m"), Integer)
