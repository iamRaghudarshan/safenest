"""Account lifecycle — the counterpart to the per-user storage layout.

Organising data per user is only half the job; the other half is being able to
account for and remove all of it. Previously deleting a user dropped just the
`users` and `user_modules` rows, leaving their photos, documents (ID scans),
vault passwords and financial history behind forever — in the database and on
disk. These helpers keep that from happening, and make the cost of a deletion
visible before it is confirmed.
"""
from .models import (Album, AlbumPhoto, AuditLog, CardPayment, CreditCard, Document, Expense,
                     GalleryPhoto, Insurance, Investment, Loan, LoanPayment, Master,
                     Notification, NotificationPref, Person, PhotoFace, PhotoPerson,
                     PushSubscription, Reminder, Todo, UserModule, VaultItem)
from . import storage

# Every table keyed directly by user_id. Deleting an account has to leave nothing
# behind — including the pieces that aren't obviously "their data": the devices
# their notifications were being pushed to, and the notifications themselves.
OWNED_MODELS = [
    ("photos", GalleryPhoto), ("documents", Document), ("vault items", VaultItem),
    ("loans", Loan), ("loan payments", LoanPayment), ("cards", CreditCard),
    ("card payments", CardPayment), ("policies", Insurance), ("investments", Investment),
    ("expenses", Expense), ("reminders", Reminder), ("tasks", Todo),
    ("people", Person), ("faces", PhotoFace), ("albums", Album),
    ("custom lists", Master), ("permissions", UserModule),
    ("notifications", Notification), ("notification settings", NotificationPref),
    ("registered devices", PushSubscription),
]


def summarise(db, user_id: int) -> dict:
    """What a deletion would destroy — shown to the admin before they confirm."""
    counts = {}
    for label, model in OWNED_MODELS:
        n = db.query(model).filter(model.user_id == user_id).count()
        if n:
            counts[label] = n
    # Avatars are small but they ARE the user's files, and they get deleted too —
    # so the figure shown before confirming should include them.
    files = {m: storage.usage(m, user_id)
             for m in (storage.GALLERY, storage.DOCUMENTS, storage.AVATARS)}
    return {
        "counts": counts,
        "totalRows": sum(counts.values()),
        "files": sum(f["files"] for f in files.values()),
        "bytes": sum(f["bytes"] for f in files.values()),
    }


def purge(db, user_id: int) -> dict:
    """Remove every trace of one user's data: owned rows, join rows, stored files.
    Returns what was removed, for the audit entry. Does NOT delete the user row —
    the caller does that so it stays inside its own transaction."""
    removed = summarise(db, user_id)

    # Join rows first — they're keyed by photo/person, not user_id, so they'd be
    # left dangling if the parents went first.
    photo_ids = [i for (i,) in db.query(GalleryPhoto.id).filter(GalleryPhoto.user_id == user_id)]
    person_ids = [i for (i,) in db.query(Person.id).filter(Person.user_id == user_id)]
    album_ids = [i for (i,) in db.query(Album.id).filter(Album.user_id == user_id)]
    if photo_ids:
        db.query(PhotoPerson).filter(PhotoPerson.photo_id.in_(photo_ids)).delete(synchronize_session=False)
        db.query(AlbumPhoto).filter(AlbumPhoto.photo_id.in_(photo_ids)).delete(synchronize_session=False)
    if person_ids:
        db.query(PhotoPerson).filter(PhotoPerson.person_id.in_(person_ids)).delete(synchronize_session=False)
    if album_ids:
        db.query(AlbumPhoto).filter(AlbumPhoto.album_id.in_(album_ids)).delete(synchronize_session=False)

    for _label, model in OWNED_MODELS:
        db.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)

    # The audit trail goes too. It used to be kept deliberately, but its `meta` now
    # carries the names of the records acted on — "Deleted loan 'HDFC Home Loan'",
    # photo captions, document titles — so leaving it behind would leave exactly the
    # personal detail the deletion is meant to erase. The administrator's own record
    # OF the deletion is written afterwards under their id, so the fact that it
    # happened, and who did it, still survives.
    gone = (db.query(AuditLog).filter(AuditLog.user_id == user_id)
            .delete(synchronize_session=False))
    if gone:
        removed["counts"]["audit entries"] = gone
        removed["totalRows"] += gone
    db.commit()

    # Files last: if anything above fails we'd rather keep orphaned files than
    # delete files whose rows survived.
    for module in (storage.GALLERY, storage.DOCUMENTS, storage.AVATARS):
        storage.purge_user(module, user_id)

    return removed
