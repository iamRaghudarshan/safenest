"""India Standard Time — the one clock this app runs on.

Every stored timestamp is IST wall-clock, regardless of where the machine running
The app happens to be. That matters now that the app can be copied to another
computer: a MacBook carried abroad would otherwise start writing rows in local
time, and the same expense list would show two different clocks depending on which
machine created each row.

Datetimes are stored NAIVE (no tzinfo) because the existing MySQL columns are
DATETIME, which has no offset. The convention is simply: naive means IST.

    from .. import ist
    ist.now()     # datetime, IST wall-clock, naive
    ist.today()   # date, IST calendar day
"""
from datetime import date, datetime, timedelta, timezone

TZ = timezone(timedelta(hours=5, minutes=30), "IST")
NAME = "Asia/Kolkata"


def now() -> datetime:
    """Current IST wall-clock, naive — matches what the DATETIME columns hold."""
    return datetime.now(TZ).replace(tzinfo=None)


def today() -> date:
    """Today's date in India. Not the host's date: a machine in another timezone
    would otherwise roll over to tomorrow hours early, or lag hours behind."""
    return now().date()


def fmt(value: datetime | date | None, with_time: bool = True) -> str | None:
    """dd-mm-yyyy, optionally with 'h:mm AM' — the format used app-wide."""
    if value is None:
        return None
    if isinstance(value, datetime):
        day = value.strftime("%d-%m-%Y")
        if not with_time:
            return day
        hour = value.hour % 12 or 12
        return f"{day}, {hour}:{value.minute:02d} {'PM' if value.hour >= 12 else 'AM'}"
    return value.strftime("%d-%m-%Y")
