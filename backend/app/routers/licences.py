"""Issuing and withdrawing licences for copies of FinMate given to other people.

Three audiences, three prefixes, and the split matters:

  /api/licences        publisher, admin only — issue, extend, withdraw.
  /api/licence/check   public, no authentication — a customer's copy asking
                       whether its own licence still stands. It answers about
                       one key id at a time and reveals nothing else, because
                       anyone on the internet can call it.
  /api/licence         the local copy's own state, for its own screens.
"""
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import cftunnel, ist, licensing, weburl
from ..config import settings
from ..database import get_db
from ..helpers import audit
from ..models import Broadcast, BroadcastReceipt, License, User, UserModule
from ..ratelimit import rate_limit
from ..security import check_password_strength, hash_password, get_current_user, require_admin
from .auth import ALL_MODULES

router = APIRouter(prefix="/api/licences", tags=["licences"])
public = APIRouter(prefix="/api/licence", tags=["licence"])

APP_VERSION = "2.0"
MAX_DAYS = 3650          # ten years; anything longer is a typo, not a decision


def _days(body: dict) -> int | None:
    """Validity in days, or None for a licence that never expires.

    `int(body.get("days") or 30)` reads naturally and is wrong: 0 is falsy, so
    asking for a zero-day licence quietly issues a thirty-day one. Absent and
    zero have to be told apart explicitly.
    """
    # Perpetual is its own flag rather than days=0 or a huge number: "sold
    # outright" is a different thing from "valid for a very long time", and the
    # licence says so in as many words.
    if body.get("perpetual") in (True, "true", "1", 1):
        return None
    raw = body.get("days")
    if raw is None or raw == "":
        return 30
    try:
        days = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(422, "Validity must be a number of days")
    if not 1 <= days <= MAX_DAYS:
        raise HTTPException(422, f"Validity must be between 1 and {MAX_DAYS} days")
    return days


MAX_SEATS = 50           # a household, not a company; beyond this is a typo


def _seats(body: dict) -> int:
    """How many sign-ins the customer may create. 0 = unlimited.

    Same falsy-zero care as _days(): here 0 is a real and meaningful answer, so
    "not stated" has to be distinguished from "no limit" rather than collapsed.
    """
    if body.get("unlimited_seats") in (True, "true", "1", 1):
        return licensing.UNLIMITED_SEATS
    raw = body.get("seats")
    if raw is None or raw == "":
        return 1
    try:
        seats = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(422, "People allowed must be a number")
    if seats == licensing.UNLIMITED_SEATS:
        return seats
    if not 1 <= seats <= MAX_SEATS:
        raise HTTPException(422, f"People allowed must be between 1 and {MAX_SEATS}")
    return seats


def _require_publisher():
    if not settings.is_publisher:
        from .branding import app_name
        raise HTTPException(
            409, f"This copy of {app_name()} cannot issue licences — "
                 "it has no signing key.")


def _row(lic: License) -> dict:
    """One licence, with its live state worked out rather than stored.

    Storing "expired" would mean something has to notice the day it turns, and
    nothing here runs on a schedule. Computing it on read cannot drift.
    """
    payload = licensing.parse(lic.token or "", settings.license_public_key_hex)
    state = licensing.evaluate(payload, revoked=bool(lic.revoked_at))
    if lic.suspended_at and not lic.revoked_at and state.get("state") not in ("invalid", "missing"):
        state = {**state, "state": "suspended"}
    return {
        "id": lic.id,
        "key_id": lic.key_id,
        "name": lic.name,
        "email": lic.email,
        "role": lic.role,
        "note": lic.note,
        "issued_on": ist.fmt(lic.issued_on, with_time=False),
        "expires_on": ist.fmt(lic.expires_on, with_time=False) if lic.expires_on else None,
        "days_left": state.get("days_left"),
        # Read from the signed token, not the row: the token is what the
        # customer's copy actually enforces, and a row edited by hand must not be
        # able to disagree with it.
        "perpetual": bool(payload.get("perpetual")),
        "seats": licensing.seats_allowed(payload),
        "state": state.get("state"),
        "revoked_at": ist.fmt(lic.revoked_at),
        "revoke_reason": lic.revoke_reason,
        "bundle_at": ist.fmt(lic.bundle_at),
        "created_at": ist.fmt(lic.created_at),
        "hostname": lic.hostname,
        "url": f"https://{lic.hostname}" if lic.hostname else None,
        "hosted": bool(lic.hostname and lic.tunnel_id),
        "suspended": bool(lic.suspended_at),
        "suspended_at": ist.fmt(lic.suspended_at),
        "suspend_reason": lic.suspend_reason,
        # What the copy last told us about itself.
        "last_seen": ist.fmt(lic.last_seen_at),
        "last_ip": lic.last_ip,
        "last_platform": lic.last_platform,
        "last_os": lic.last_os,
        "last_version": lic.last_version,
        "last_hostname": lic.last_hostname,
        "checkins": int(lic.checkins or 0),
    }


@router.get("")
def index(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _require_publisher()
    rows = db.query(License).order_by(License.id.desc()).all()
    out = [_row(r) for r in rows]
    live = sum(1 for r in out if r["state"] in (licensing.OK, licensing.EXPIRING))
    return {"licences": out, "live": live, "total": len(out),
            "public_key": settings.license_public_key_hex,
            "hosting": {"available": settings.licence_hosting_enabled,
                        "domain": settings.licence_domain}}


@router.post("")
def create(request: Request, body: dict = Body(...),
           admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Issue a licence, optionally creating the account that goes with it.

    The two are separate on purpose. Issuing to someone who already has an
    account (a renewal) must not try to create it again, and issuing a licence
    for a machine that has not been set up yet must not require inventing a
    password today.
    """
    _require_publisher()
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    days = _days(body)
    seats = _seats(body)

    if not name:
        raise HTTPException(422, "Name is required")
    if not licensing.looks_like_email(email):
        raise HTTPException(422, "A valid email address is required")

    key_id = licensing.new_key_id()
    while db.query(License).filter(License.key_id == key_id).first():
        key_id = licensing.new_key_id()

    # Optional hosting: their own subdomain, provisioned here so the customer never
    # needs a Cloudflare account. Done before the licence row is written — if
    # Cloudflare refuses, nothing has been recorded that would need unpicking.
    host = {"hostname": None, "tunnel_id": None, "token": None}
    if body.get("hosting"):
        if not settings.licence_hosting_enabled:
            raise HTTPException(409, "Hosting is not set up on this server — add "
                                     "CF_API_TOKEN, CF_ACCOUNT_ID and CF_ZONE_ID.")
        label = (body.get("subdomain") or "").strip().lower() \
            or cftunnel.suggest_label(name, email)
        if not cftunnel.valid_label(label):
            raise HTTPException(422, "The address must be letters, numbers and hyphens only.")
        wanted = cftunnel.hostname_for(label)
        if db.query(License).filter(License.hostname == wanted,
                                    License.revoked_at.is_(None)).first():
            raise HTTPException(409, f"{wanted} is already in use by another licence.")
        try:
            host = cftunnel.provision(label)
        except cftunnel.CloudflareError as exc:
            raise HTTPException(502, f"Cloudflare: {exc}")

    token, payload = licensing.issue(
        settings.license_signing_key_hex, key_id=key_id, name=name, email=email,
        days=days, role="user", issuer=weburl.public_url(db),
        note=(body.get("note") or "").strip(), seats=seats)

    now = ist.now()
    lic = License(key_id=key_id, name=name, email=email, role="user",
                  issued_on=ist.today(),
                  # NULL expiry is what "never expires" looks like in the table.
                  expires_on=None if days is None
                  else ist.today() + ist.timedelta(days=days),
                  seats=seats,
                  note=(body.get("note") or "").strip()[:200], token=token,
                  hostname=host.get("hostname"), tunnel_id=host.get("tunnel_id"),
                  tunnel_token=host.get("token"),
                  created_by=admin.id, created_at=now, updated_at=now)
    db.add(lic)

    # Optionally create the sign-in that ships with the copy. Always a plain user:
    # the whole point is that the customer administers nothing.
    created_user = None
    if body.get("create_user"):
        password = body.get("password") or ""
        check_password_strength(password)
        if db.query(User).filter(User.email == email).first():
            raise HTTPException(409, "A user with that email already exists")
        created_user = User(name=name, email=email, password_hash=hash_password(password),
                            role="user", status="active", created_at=now, updated_at=now)
        db.add(created_user); db.flush()
        for m in ALL_MODULES:
            db.add(UserModule(user_id=created_user.id, module_key=m,
                              can_view=1, can_create=1, can_edit=1, can_delete=1))

    db.commit(); db.refresh(lic)
    audit(db, admin.id, "licence_issue", "licence", lic.id,
          # .get, not []: a perpetual licence carries no "expires" key at all, and
          # indexing it raised a KeyError that surfaced as a 500 with the licence
          # already written — issued, recorded, and reported as a server fault.
          {"label": f"{name} ({key_id})", "email": email,
           "days": days, "seats": seats,
           "expires": payload.get("expires", "never")}, request=request)
    return {**_row(lic), "token": token, "user_id": created_user.id if created_user else None}


@router.post("/{id}/extend")
def extend(id: int, request: Request, body: dict = Body(...),
           admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Re-sign for a longer run. The customer must be given the new licence file.

    A signature cannot be edited, so extending means issuing a fresh token under
    the same key id. The old one stays valid until its own expiry — there is no
    way to recall it, which is what withdrawal is for.
    """
    _require_publisher()
    lic = db.query(License).get(id)
    if not lic:
        raise HTTPException(404, "Licence not found")
    days = _days(body)

    token, payload = licensing.issue(
        settings.license_signing_key_hex, key_id=lic.key_id, name=lic.name,
        email=lic.email, days=days, role=lic.role,
        issuer=weburl.public_url(db), note=lic.note or "")
    lic.token = token
    lic.issued_on = ist.today()
    lic.expires_on = ist.today() + ist.timedelta(days=days)
    lic.revoked_at = None            # extending an withdrawn licence reinstates it
    lic.revoke_reason = None
    lic.updated_at = ist.now()
    db.commit(); db.refresh(lic)
    audit(db, admin.id, "licence_extend", "licence", lic.id,
          {"label": f"{lic.name} ({lic.key_id})", "days": days,
           "expires": payload["expires"]}, request=request)
    return {**_row(lic), "token": token}


@router.post("/{id}/revoke")
def revoke(id: int, request: Request, body: dict = Body(default={}),
           admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Withdraw a licence.

    Only bites once the customer's copy can reach this server — the signature it
    already holds stays cryptographically valid until its expiry. Withdrawal is
    therefore a same-week measure, not an instant kill switch.
    """
    _require_publisher()
    lic = db.query(License).get(id)
    if not lic:
        raise HTTPException(404, "Licence not found")
    lic.revoked_at = ist.now()
    lic.revoke_reason = (body.get("reason") or "").strip()[:200] or None
    lic.updated_at = ist.now()

    # If we host them, this is the part that bites immediately. Deleting the DNS
    # record stops their address resolving within seconds, which is the only
    # instant control here — the signed licence they hold stays cryptographically
    # valid until its expiry no matter what this row says.
    cut = {}
    if lic.hostname or lic.tunnel_id:
        try:
            cut = cftunnel.deprovision(lic.hostname or "", lic.tunnel_id or "")
            lic.tunnel_token = None          # no longer usable; do not keep it
            lic.tunnel_id = None
        except cftunnel.CloudflareError as exc:
            cut = {"error": str(exc)}

    db.commit(); db.refresh(lic)
    audit(db, admin.id, "licence_revoke", "licence", lic.id,
          {"label": f"{lic.name} ({lic.key_id})", "reason": lic.revoke_reason,
           "hosting": cut}, request=request)
    return {**_row(lic), "hosting_removed": cut}


@router.post("/{id}/suspend")
def suspend(id: int, request: Request, body: dict = Body(default={}),
            admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Stop a copy working, reversibly.

    Separate from withdrawal on purpose: an unpaid invoice is not a cancelled
    contract, and lifting a suspension must not require re-issuing a licence.
    Takes effect when that copy next reaches this server.
    """
    _require_publisher()
    lic = db.query(License).get(id)
    if not lic:
        raise HTTPException(404, "Licence not found")
    lic.suspended_at = ist.now()
    lic.suspend_reason = (body.get("reason") or "").strip()[:200] or None
    lic.updated_at = ist.now()
    db.commit(); db.refresh(lic)
    audit(db, admin.id, "licence_suspend", "licence", lic.id,
          {"label": f"{lic.name} ({lic.key_id})", "reason": lic.suspend_reason},
          request=request)
    return _row(lic)


@router.post("/{id}/unsuspend")
def unsuspend(id: int, request: Request, admin: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    _require_publisher()
    lic = db.query(License).get(id)
    if not lic:
        raise HTTPException(404, "Licence not found")
    lic.suspended_at = None
    lic.suspend_reason = None
    lic.updated_at = ist.now()
    db.commit(); db.refresh(lic)
    audit(db, admin.id, "licence_unsuspend", "licence", lic.id,
          {"label": f"{lic.name} ({lic.key_id})"}, request=request)
    return _row(lic)


@router.post("/{id}/notice")
def notice(id: int, request: Request, body: dict = Body(...),
           admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """A message for one customer rather than everyone."""
    _require_publisher()
    lic = db.query(License).get(id)
    if not lic:
        raise HTTPException(404, "Licence not found")
    title = (body.get("title") or "").strip()[:160]
    text = (body.get("body") or "").strip()[:2000]
    if not title or not text:
        raise HTTPException(422, "A title and a message are required")
    row = Broadcast(title=title, body=text, url=(body.get("url") or "").strip()[:255],
                    kind=body.get("kind") if body.get("kind") in ("news", "update", "urgent") else "news",
                    audience=f"licence:{lic.key_id}", created_by=admin.id,
                    created_at=ist.now())
    db.add(row); db.commit()
    audit(db, admin.id, "licence_notice", "licence", lic.id,
          {"label": f"{lic.name} ({lic.key_id})", "title": title}, request=request)
    return {"id": row.id, "for": lic.key_id}


@router.post("/{id}/restore")
def restore(id: int, request: Request, admin: User = Depends(require_admin),
            db: Session = Depends(get_db)):
    _require_publisher()
    lic = db.query(License).get(id)
    if not lic:
        raise HTTPException(404, "Licence not found")
    lic.revoked_at = None
    lic.revoke_reason = None
    lic.updated_at = ist.now()
    db.commit(); db.refresh(lic)
    audit(db, admin.id, "licence_restore", "licence", lic.id,
          {"label": f"{lic.name} ({lic.key_id})"}, request=request)
    return _row(lic)


@router.get("/{id}/token")
def token_of(id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """The licence file's contents, for re-sending to a customer who lost it."""
    _require_publisher()
    lic = db.query(License).get(id)
    if not lic:
        raise HTTPException(404, "Licence not found")
    return {"key_id": lic.key_id, "token": lic.token, "filename": "licence.key"}


# ---------------------------------------------------------------- announcements
@router.post("/broadcast")
def broadcast(request: Request, body: dict = Body(...),
              admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Tell everyone something — a new version, a change, anything.

    Local users are notified immediately through the normal notification path, so
    the message is in the app whether or not the push lands. Licensed copies are
    not reachable from here at all, so their copy of the message waits until they
    next check in.
    """
    title = (body.get("title") or "").strip()[:160]
    text = (body.get("body") or "").strip()[:2000]
    if not title or not text:
        raise HTTPException(422, "A title and a message are required")
    kind = body.get("kind") if body.get("kind") in ("news", "update", "urgent") else "news"
    audience = body.get("audience") if body.get("audience") in ("all", "local", "licensed") else "all"

    row = Broadcast(title=title, body=text, url=(body.get("url") or "").strip()[:255],
                    kind=kind, app_version=(body.get("app_version") or "").strip()[:20],
                    audience=audience, created_by=admin.id, created_at=ist.now())
    db.add(row); db.flush()

    sent = 0
    if audience in ("all", "local"):
        from .. import push
        for (uid,) in db.query(User.id).filter(User.status == "active").all():
            try:
                push.notify(db, uid, title, text, url=row.url or "/", kind="system")
                sent += 1
            except Exception as exc:      # one bad subscription must not stop the rest
                print(f"[broadcast] user {uid}: {exc}")
    row.delivered_local = sent
    db.commit(); db.refresh(row)

    live = db.query(License).filter(License.revoked_at.is_(None)).count()
    audit(db, admin.id, "broadcast", "broadcast", row.id,
          {"label": title, "audience": audience, "local": sent}, request=request)
    return {"id": row.id, "delivered_local": sent,
            "waiting_for": live if audience in ("all", "licensed") else 0}


@router.get("/broadcast")
def broadcast_list(limit: int = 20, admin: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """Everything sent, and — the point of this endpoint — whether it landed.

    A message nobody collected and a message everyone collected are the same row
    in `broadcasts`. The receipts are what tell them apart, so every entry carries
    who has it and who is still to get it, by name.
    """
    rows = (db.query(Broadcast).order_by(Broadcast.id.desc())
            .limit(max(1, min(int(limit or 20), 100))).all())
    if not rows:
        return {"items": []}

    # Who each message is addressed to. A licence issued after the message still
    # counts, because the announcements endpoint has no date filter and will
    # genuinely serve it to them — the status has to match the real behaviour.
    live = db.query(License).filter(License.revoked_at.is_(None)).all()
    by_key = {l.key_id: l.name for l in live}

    receipts: dict[int, list[str]] = {}
    for bid, key in (db.query(BroadcastReceipt.broadcast_id, BroadcastReceipt.key_id)
                     .filter(BroadcastReceipt.broadcast_id.in_([r.id for r in rows])).all()):
        receipts.setdefault(bid, []).append(key)

    def audience_keys(audience: str) -> list[str]:
        if audience == "local":
            return []
        if (audience or "").startswith("licence:"):
            k = audience.split(":", 1)[1]
            return [k] if k in by_key else []
        return list(by_key)               # "all" and "licensed" both reach every copy

    # A message that has since been sent again is finished: the delivery endpoint
    # serves only the newest of a lineage, so anyone still "waiting" on the old row
    # will be handed the new one instead and the old row can never complete.
    # Leaving it showing "waiting on Ashok" for ever is precisely the misleading
    # status this endpoint exists to remove.
    newest_in_lineage: dict[int, int] = {}
    for lineage, top in db.query(
            func.coalesce(Broadcast.resend_of, Broadcast.id),
            func.max(Broadcast.id)).group_by(
            func.coalesce(Broadcast.resend_of, Broadcast.id)).all():
        newest_in_lineage[int(lineage)] = int(top)

    items = []
    for r in rows:
        targets = audience_keys(r.audience or "all")
        got = [k for k in receipts.get(r.id, []) if k in by_key]
        waiting = [k for k in targets if k not in set(got)]
        top = newest_in_lineage.get(r.resend_of or r.id, r.id)
        superseded = top > r.id
        items.append({
            "id": r.id, "title": r.title, "body": r.body, "url": r.url, "kind": r.kind,
            "audience": r.audience, "app_version": r.app_version,
            "delivered_local": int(r.delivered_local or 0),
            "created_at": ist.fmt(r.created_at),
            "resend_of": r.resend_of,
            "superseded_by": top if superseded else None,
            "targets": len(targets),
            "collected": [{"key_id": k, "name": by_key.get(k, k)} for k in got],
            "waiting": [] if superseded
                       else [{"key_id": k, "name": by_key.get(k, k)} for k in waiting],
        })
    return {"items": items}


@router.post("/broadcast/{id}/resend")
def resend(id: int, request: Request, body: dict = Body(default={}),
           admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Send an existing message again.

    This has to create a NEW row rather than re-flag the old one. A customer copy
    remembers only a high-water mark — the largest message id it has stored — and
    asks for `id > that`. Anything at or below it will never be requested again,
    however it is marked here, so the only way to put a message back in front of
    someone is to give it a higher id.

    `only_waiting` narrows it to the copies that never collected the original,
    which is the usual intent: chase the ones that missed it, don't spam the rest.
    """
    original = db.query(Broadcast).get(id)
    if not original:
        raise HTTPException(404, "Message not found")

    # Resending an already-superseded row would build a second branch of the same
    # lineage, and the delivery endpoint keeps only one — so the click would appear
    # to work and change nothing. Point at the newest instead.
    lineage = original.resend_of or original.id
    top = db.query(func.max(Broadcast.id)).filter(
        func.coalesce(Broadcast.resend_of, Broadcast.id) == lineage).scalar()
    if top and top > original.id:
        raise HTTPException(409, "This one was already sent again — use the newer message")

    audience = original.audience or "all"
    if body.get("only_waiting"):
        got = {k for (k,) in db.query(BroadcastReceipt.key_id)
               .filter(BroadcastReceipt.broadcast_id == id).all()}
        live = [l.key_id for l in db.query(License).filter(License.revoked_at.is_(None)).all()]
        waiting = [k for k in live if k not in got]
        if not audience.startswith("licence:"):
            if not waiting:
                raise HTTPException(409, "Every copy has already collected this one")
            # One target can be addressed precisely; several cannot be expressed in
            # a single audience string, so those stay a broadcast to all copies.
            audience = f"licence:{waiting[0]}" if len(waiting) == 1 else "licensed"

    row = Broadcast(title=original.title, body=original.body, url=original.url,
                    kind=original.kind, app_version=original.app_version,
                    audience=audience, created_by=admin.id, created_at=ist.now(),
                    resend_of=original.resend_of or original.id)
    db.add(row); db.flush()

    sent = 0
    if audience in ("all", "local"):
        from .. import push
        for (uid,) in db.query(User.id).filter(User.status == "active").all():
            try:
                push.notify(db, uid, row.title, row.body, url=row.url or "/", kind="system")
                sent += 1
            except Exception as exc:
                print(f"[resend] user {uid}: {exc}")
    row.delivered_local = sent
    db.commit(); db.refresh(row)

    live_count = db.query(License).filter(License.revoked_at.is_(None)).count()
    audit(db, admin.id, "broadcast_resend", "broadcast", row.id,
          {"label": row.title, "resend_of": original.id, "audience": audience}, request=request)
    return {"id": row.id, "resend_of": original.id, "delivered_local": sent,
            "waiting_for": 0 if audience == "local" else
                           (1 if audience.startswith("licence:") else live_count)}


# ------------------------------------------------------------------- public API
@public.get("/announcements/{key_id}")
def announcements(request: Request, key_id: str, since: str = "",
                  db: Session = Depends(get_db)):
    """Messages waiting for one licensed copy. Called by that copy, unauthenticated.

    Keyed by licence so only a real customer sees them, and a withdrawn licence
    gets nothing. `since` is the id of the last message that copy already stored,
    which keeps this cheap to poll and stops the same notice reappearing.
    """
    rate_limit(request, "licence-news", limit=60, window=300)
    lic = db.query(License).filter(License.key_id == (key_id or "")[:16]).first()
    if not lic or lic.revoked_at:
        return {"items": [], "latest_version": APP_VERSION}

    query = db.query(Broadcast).filter(
        Broadcast.audience.in_(("all", "licensed", f"licence:{lic.key_id}")))
    try:
        after = int(since or 0)
    except (TypeError, ValueError):
        after = 0
    if after:
        query = query.filter(Broadcast.id > after)
    rows = query.order_by(Broadcast.id.asc()).limit(20).all()
    rows = _newest_of_each(rows)

    # This call is the only moment the publisher ever learns a message arrived —
    # delivery is pull-only, so there is no acknowledgement to wait for. Record it
    # here, before answering, and treat a failure as unimportant: a lost receipt
    # must never cost the customer the message itself.
    if rows:
        _record_receipts(db, lic.key_id, [r.id for r in rows])
    return {
        "items": [{"id": r.id, "title": r.title, "body": r.body, "url": r.url,
                   "kind": r.kind, "app_version": r.app_version,
                   "at": ist.fmt(r.created_at)} for r in rows],
        "latest_version": APP_VERSION,
    }


def _newest_of_each(rows: list) -> list:
    """Collapse a message and its resends down to the latest one.

    A resend has to be a new row with a higher id, because a customer copy only
    ever asks for ids above the highest it has stored. That creates a trap: a copy
    that never collected the original has BOTH the original and the resend
    pending, and would show the same announcement twice — worst for exactly the
    person the resend was chasing, who has seen it zero times, not once.

    Collapsing here rather than at send time keeps the history intact: the admin
    list still shows the original and the resend as separate entries with their own
    delivery state, while any single copy is only ever handed the latest version.
    """
    latest: dict[int, object] = {}
    for r in rows:
        lineage = r.resend_of or r.id
        current = latest.get(lineage)
        if current is None or r.id > current.id:
            latest[lineage] = r
    return sorted(latest.values(), key=lambda r: r.id)


def _record_receipts(db: Session, key_id: str, ids: list[int]) -> None:
    """Mark these messages as collected by this licence, once each."""
    try:
        already = {i for (i,) in db.query(BroadcastReceipt.broadcast_id)
                   .filter(BroadcastReceipt.key_id == key_id,
                           BroadcastReceipt.broadcast_id.in_(ids)).all()}
        now = ist.now()
        for bid in ids:
            if bid not in already:
                db.add(BroadcastReceipt(broadcast_id=bid, key_id=key_id, collected_at=now))
        db.commit()
    except Exception as exc:          # a receipt is bookkeeping, not the payload
        db.rollback()
        print(f"[announcements] receipt for {key_id}: {exc}")
@public.get("/check/{key_id}")
def check(request: Request, key_id: str, db: Session = Depends(get_db)):
    """Is this licence still good? Called by customer copies, unauthenticated.

    Answers only yes/no about withdrawal and never confirms whether a key id
    exists — an unknown id reports not-revoked, so this cannot be used to
    enumerate the customer list.
    """
    # Unauthenticated and reachable by anyone on the internet, so it needs a
    # ceiling of its own. A real copy asks once a day; this is generous for that
    # and still refuses a machine trying to grind through key ids or simply
    # hammer the database.
    rate_limit(request, "licence-check", limit=60, window=300)
    lic = db.query(License).filter(License.key_id == (key_id or "")[:16]).first()
    if lic:
        _record_checkin(request, lic, db)
    # Suspension and revocation both stop the copy; only revocation is permanent.
    blocked = bool(lic and (lic.revoked_at or lic.suspended_at))
    return {"revoked": blocked,
            "reason": (lic.suspend_reason if lic and lic.suspended_at and not lic.revoked_at
                       else None)}


def _record_checkin(request: Request, lic: License, db: Session) -> None:
    """Note that this copy is alive, and on what.

    Everything here is either already visible to the server (the IP, which every
    HTTP request carries) or sent by the copy about itself: which build, which
    operating system, the computer's name. Nothing about the customer's records
    is asked for or accepted.
    """
    q = request.query_params
    lic.last_seen_at = ist.now()
    lic.last_ip = (request.client.host if request.client else "")[:45]
    plat = (q.get("platform") or "")[:20].lower()
    if plat in ("windows", "mac", "linux"):
        lic.last_platform = plat
    if q.get("os"):
        lic.last_os = q["os"][:120]
    if q.get("version"):
        lic.last_version = q["version"][:20]
    if q.get("host"):
        lic.last_hostname = q["host"][:120]
    lic.checkins = int(lic.checkins or 0) + 1
    db.commit()


@public.get("/status")
def status(user: User = Depends(get_current_user)):
    """This installation's own licence state, for the screens that display it."""
    if not settings.licensed_mode:
        return {"licensed": False, "state": licensing.OK}
    state = licensing.status(settings.license_path, settings.license_public_key_hex)
    return {
        "licensed": True,
        "state": state.get("state"),
        "reason": state.get("reason"),
        "name": state.get("name"),
        "email": state.get("email"),
        "key_id": state.get("kid"),
        "expires_on": state.get("expires_on"),
        "days_left": state.get("days_left"),
        "blocked": licensing.is_blocked(state.get("state", "")),
        # `reports` and `reports_to` used to be returned here and listed in the
        # Profile screen, as candour about what a licensed copy sends. It read as
        # the opposite: customers saw their machine name and the supplier's address
        # next to "sent ... once a day" and took it for the app reporting on them.
        # It also put the publisher's private domain on someone else's screen. What
        # a copy validates belongs in the licence terms, not in Settings — and the
        # browser has no use for either value, so neither is sent.
    }
