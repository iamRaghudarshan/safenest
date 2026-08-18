"""Habit tracking — build a habit, tick it each day, watch the streak grow.

The same shape as todos, with two things todos does not need: a habit is judged
against a GOAL (every day, certain weekdays, or a number of days a week), and its
history is the point of the feature, so a `habit_logs` row records each day it was
done. Streaks and the completion rate are computed from those logs on read rather
than stored, so a back-dated tick or an untick recomputes correctly with no field
to keep in step.

A "measured" habit (target_count > 1 with a unit — 8 glasses, 30 minutes) counts as
done for the day only once the day's logged total reaches the target; a plain habit
needs a single tick. A "quit" habit tracks the same way — a done day is one stayed
clean — only the wording differs, which is a client concern.
"""
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import ist
from ..database import get_db
from ..helpers import audit, changes, snapshot, to_dict
from ..models import Habit, HabitLog, User
from ..security import guard

router = APIRouter(prefix="/api/habits", tags=["habits"])

# Everything a client may set. Anything else in the body is ignored, not refused
# — the same rule every record router follows.
FIELDS = ["name", "icon", "color", "kind", "goal_type", "weekdays",
          "target_count", "unit", "weekly_target", "reminder_time", "note",
          "archived", "sort_order"]


def _weekday_set(habit: Habit) -> set[int]:
    """ISO weekday numbers (Mon=1 … Sun=7) the goal applies on. Empty CSV = all."""
    raw = (habit.weekdays or "").strip()
    if not raw:
        return {1, 2, 3, 4, 5, 6, 7}
    out = {int(p) for p in raw.split(",") if p.strip().isdigit() and 1 <= int(p) <= 7}
    return out or {1, 2, 3, 4, 5, 6, 7}


def _active_on(habit: Habit, d: date) -> bool:
    """Does the goal apply on this day? Only 'weekdays' habits skip any day."""
    if habit.goal_type == "weekdays":
        return d.isoweekday() in _weekday_set(habit)
    return True


def _monday(d: date) -> date:
    """The Monday that opens d's week. Week maths is done on Monday dates rather
    than isocalendar() so the year boundary never needs a special case."""
    return d - timedelta(days=d.isoweekday() - 1)


def _daily_streaks(habit: Habit, done: dict[date, bool], today: date) -> tuple[int, int]:
    """Current and best run of done days, skipping days the goal does not apply."""
    def is_done(d: date) -> bool:
        return _active_on(habit, d) and done.get(d, False)

    # Current: walk back from today. An active-but-not-yet-done TODAY must not break
    # the streak — the day is not over — so start from yesterday when today is open.
    cur = 0
    d = today
    if _active_on(habit, today) and not done.get(today, False):
        d = today - timedelta(days=1)
    for _ in range(3660):                       # ~10 years is plenty of a bound
        if _active_on(habit, d):
            if is_done(d):
                cur += 1
            else:
                break
        d -= timedelta(days=1)

    # Best: scan from the first logged day to today, counting active-day runs.
    best = run = 0
    if done:
        d = min(done)
        while d <= today:
            if _active_on(habit, d):
                run = run + 1 if done.get(d, False) else 0
                best = max(best, run)
            d += timedelta(days=1)
    return cur, max(best, cur)


def _weekly_streaks(habit: Habit, done: dict[date, bool], today: date) -> tuple[int, int]:
    """For an X-times-a-week goal, a streak is counted in weeks that met the target."""
    target = max(1, habit.weekly_target or 1)
    per_week: dict[date, int] = defaultdict(int)
    for d, ok in done.items():
        if ok:
            per_week[_monday(d)] += 1

    def met(mon: date) -> bool:
        return per_week.get(mon, 0) >= target

    this_mon = _monday(today)
    # The current week is still in progress, so a not-yet-met week does not break it.
    cur = 0
    mon = this_mon if met(this_mon) else this_mon - timedelta(days=7)
    for _ in range(520):
        if met(mon):
            cur += 1
            mon -= timedelta(days=7)
        else:
            break

    best = run = 0
    if per_week:
        mon = min(per_week)
        while mon <= this_mon:
            run = run + 1 if met(mon) else 0
            best = max(best, run)
            mon += timedelta(days=7)
    return cur, max(best, cur)


def _stats(habit: Habit, logs: list[HabitLog]) -> dict:
    """Everything a card needs: today's progress, streaks, a 30-day rate, a week strip."""
    today = ist.today()
    totals: dict[date, int] = defaultdict(int)
    for lg in logs:
        if lg.log_date is not None:
            totals[lg.log_date] += (lg.count or 0)

    need = max(1, habit.target_count or 1)
    done = {d: c >= need for d, c in totals.items()}

    if habit.goal_type == "weekly":
        cur, best = _weekly_streaks(habit, done, today)
    else:
        cur, best = _daily_streaks(habit, done, today)

    active = complete = 0
    for i in range(30):
        d = today - timedelta(days=i)
        if _active_on(habit, d):
            active += 1
            complete += 1 if done.get(d, False) else 0
    rate = round(complete / active * 100) if active else 0

    week = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        week.append({
            "date": d.strftime("%Y-%m-%d"),
            "active": _active_on(habit, d),
            "done": done.get(d, False),
            "count": totals.get(d, 0),
        })

    return {
        "target": need,
        "today_count": totals.get(today, 0),
        "done_today": done.get(today, False),
        "active_today": _active_on(habit, today),
        "current_streak": cur,
        "best_streak": best,
        "rate30": rate,
        "week": week,
    }


def _present(habit: Habit, logs: list[HabitLog]) -> dict:
    d = to_dict(habit)
    d.update(_stats(habit, logs))
    return d


def _apply(t: Habit, body: dict) -> None:
    """Copy the allowed fields off the body. Empty strings are kept (they clear
    weekdays/unit/note), except reminder_time which becomes NULL so the scheduler
    ignores it rather than trying to ring an empty time."""
    for f in FIELDS:
        if f not in body:
            continue
        v = body[f]
        if f == "name":
            if not str(v or "").strip():
                raise HTTPException(422, "Name is required")
            v = str(v).strip()
        setattr(t, f, v)
    if (t.reminder_time or "") == "":
        t.reminder_time = None


@router.get("")
def index(archived: int = 0, user: User = Depends(guard("habits", "view")),
          db: Session = Depends(get_db)):
    rows = (db.query(Habit)
            .filter(Habit.user_id == user.id, Habit.archived == (1 if archived else 0))
            .order_by(Habit.sort_order.asc(), Habit.id.asc()).all())
    # One log query for the lot, grouped in memory — a household's history is small.
    logs: dict[int, list[HabitLog]] = defaultdict(list)
    if rows:
        ids = [r.id for r in rows]
        for lg in (db.query(HabitLog)
                   .filter(HabitLog.user_id == user.id, HabitLog.habit_id.in_(ids)).all()):
            logs[lg.habit_id].append(lg)
    return {"items": [_present(r, logs.get(r.id, [])) for r in rows]}


@router.post("")
def create(body: dict = Body(...), user: User = Depends(guard("habits", "create")),
           db: Session = Depends(get_db)):
    if not str(body.get("name") or "").strip():
        raise HTTPException(422, "Name is required")
    now = ist.now()
    t = Habit(user_id=user.id, created_at=now, updated_at=now)
    _apply(t, body)
    db.add(t); db.commit(); db.refresh(t)
    audit(db, user.id, "create", "habit", t.id, {"label": t.name})
    return {"item": _present(t, [])}


@router.put("/{id}")
def update(id: int, body: dict = Body(...), user: User = Depends(guard("habits", "edit")),
           db: Session = Depends(get_db)):
    t = db.query(Habit).filter(Habit.id == id, Habit.user_id == user.id).first()
    if not t:
        raise HTTPException(404, "habit not found")
    before = snapshot(t)
    changing_time = "reminder_time" in body and body.get("reminder_time") != t.reminder_time
    _apply(t, body)
    # A moved (or cleared) alarm must be allowed to ring again today — same reason
    # the reminders module clears notified_on when its time is edited.
    if changing_time:
        t.notified_on = None
    t.updated_at = ist.now(); db.commit(); db.refresh(t)
    logs = db.query(HabitLog).filter(HabitLog.habit_id == id, HabitLog.user_id == user.id).all()
    audit(db, user.id, "update", "habit", id,
          {"label": t.name, "changes": changes(before, snapshot(t))})
    return {"item": _present(t, logs)}


@router.post("/{id}/check")
def check(id: int, body: dict = Body(default={}), user: User = Depends(guard("habits", "edit")),
          db: Session = Depends(get_db)):
    """Record (or clear) a day's progress. `date` defaults to today; `count` to the
    habit's target — so a plain tap on a simple habit marks it done, and a measured
    habit can be set to any total. A count of 0 removes the day entirely (an untick).

    One row per habit per day: this upserts rather than appending, so tapping twice
    does not double-count and the calendar has a single source of truth per date.
    """
    t = db.query(Habit).filter(Habit.id == id, Habit.user_id == user.id).first()
    if not t:
        raise HTTPException(404, "habit not found")
    when = body.get("date") or ist.today().strftime("%Y-%m-%d")
    # Default a bare tap to the full target so a simple habit reads as done at once.
    raw = body.get("count")
    count = int(raw) if raw is not None else max(1, t.target_count or 1)

    row = (db.query(HabitLog)
           .filter(HabitLog.habit_id == id, HabitLog.user_id == user.id,
                   HabitLog.log_date == when).first())
    if count <= 0:
        if row:
            db.delete(row)
    elif row:
        row.count = count
    else:
        db.add(HabitLog(user_id=user.id, habit_id=id, log_date=when,
                        count=count, created_at=ist.now()))
    db.commit()
    logs = db.query(HabitLog).filter(HabitLog.habit_id == id, HabitLog.user_id == user.id).all()
    audit(db, user.id, "log", "habit", id, {"label": t.name, "date": when, "count": count})
    return {"item": _present(t, logs)}


@router.get("/{id}/history")
def history(id: int, frm: str = "", to: str = "",
            user: User = Depends(guard("habits", "view")), db: Session = Depends(get_db)):
    """The dated check-ins for the calendar view. Defaults to the last ~13 weeks."""
    t = db.query(Habit).filter(Habit.id == id, Habit.user_id == user.id).first()
    if not t:
        raise HTTPException(404, "habit not found")
    today = ist.today()
    start = frm or (today - timedelta(days=97)).strftime("%Y-%m-%d")
    end = to or today.strftime("%Y-%m-%d")
    rows = (db.query(HabitLog)
            .filter(HabitLog.habit_id == id, HabitLog.user_id == user.id,
                    HabitLog.log_date >= start, HabitLog.log_date <= end)
            .order_by(HabitLog.log_date.asc()).all())
    need = max(1, t.target_count or 1)
    days = [{"date": r.log_date.strftime("%Y-%m-%d"), "count": r.count or 0,
             "done": (r.count or 0) >= need} for r in rows if r.log_date]
    return {"days": days, "target": need}


@router.post("/{id}/archive")
def archive(id: int, user: User = Depends(guard("habits", "edit")),
            db: Session = Depends(get_db)):
    t = db.query(Habit).filter(Habit.id == id, Habit.user_id == user.id).first()
    if not t:
        raise HTTPException(404, "habit not found")
    t.archived = 0 if t.archived else 1
    t.updated_at = ist.now(); db.commit()
    audit(db, user.id, "archive" if t.archived else "unarchive", "habit", id, {"label": t.name})
    return {"id": id, "archived": t.archived}


@router.delete("/{id}")
def delete(id: int, user: User = Depends(guard("habits", "delete")),
           db: Session = Depends(get_db)):
    t = db.query(Habit).filter(Habit.id == id, Habit.user_id == user.id).first()
    if not t:
        raise HTTPException(404, "habit not found")
    label = t.name
    # The history goes with it — the logs are meaningless without the habit and
    # would otherwise linger as orphans the way photo_vectors once did.
    db.query(HabitLog).filter(HabitLog.habit_id == id, HabitLog.user_id == user.id).delete()
    db.delete(t); db.commit()
    audit(db, user.id, "delete", "habit", id, {"label": label})
    return {"deleted": id}
