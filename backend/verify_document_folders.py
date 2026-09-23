"""Does the documents folder tree hold together?

The one that matters is the cycle: moving a folder inside its own child
detaches the whole subtree from the top level. It still exists, it is simply
unreachable, and the breadcrumb walk loops forever. Throwaway SQLite; nothing
in production is touched.
"""
import os
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import ist, models
from app.routers.documents import _breadcrumb, _descendants

tmp = tempfile.mkdtemp(prefix='sn-folders-')
db_path = os.path.join(tmp, 't.db').replace(os.sep, '/')
eng = create_engine('sqlite:///' + db_path)
models.DocumentFolder.__table__.create(bind=eng)
models.Document.__table__.create(bind=eng)
db = sessionmaker(bind=eng)()
now = ist.now()

UID = 1


def mk(name, parent=None):
    f = models.DocumentFolder(user_id=UID, parent_id=parent, name=name,
                              is_trashed=0, created_at=now, updated_at=now)
    db.add(f); db.commit(); db.refresh(f)
    return f


#  Home
#   +- Bank
#   |   +- 2025
#   |       +- Statements
#   +- Medical
home = mk('Home')
bank = mk('Bank', home.id)
y25 = mk('2025', bank.id)
stmts = mk('Statements', y25.id)
med = mk('Medical', home.id)
print('  built: Home > Bank > 2025 > Statements, and Home > Medical')

print()
print('  --- breadcrumb walks up to the top level ---')
crumb = _breadcrumb(db, UID, stmts.id)
names = [c['name'] for c in crumb]
print('  path to Statements:', ' / '.join(names))
assert names == ['Home', 'Bank', '2025', 'Statements'], names
print('  OK')

print()
print('  --- descendants, which is what refuses a bad move ---')
d = _descendants(db, UID, bank.id)
print('  at or below Bank:', sorted(d))
assert d == {bank.id, y25.id, stmts.id}, d
assert med.id not in d, 'Medical is not under Bank'
assert home.id not in d, 'a parent is not its own descendant'
print('  OK: Bank, 2025, Statements — and not Medical or Home')

print()
print('  --- the move that would destroy the tree ---')
for name, mover, target in (
        ('into its own child', bank.id, y25.id),
        ('into its own grandchild', bank.id, stmts.id),
        ('into itself', bank.id, bank.id)):
    blocked = target in _descendants(db, UID, mover)
    print(f'  move Bank {name:24} -> refused: {blocked}')
    assert blocked, f'a move {name} was NOT refused'

ok = med.id not in _descendants(db, UID, bank.id)
print(f'  move Bank into Medical (legitimate)  -> allowed: {ok}')
assert ok, 'a legitimate move was refused'

print()
print('  --- two folders of the same name in one place ---')
mk('Taxes', home.id)
try:
    mk('Taxes', home.id)
    print('  !! FAILED: a duplicate name was allowed in the same folder')
    raise SystemExit(1)
except IntegrityError:
    db.rollback()
    print('  same name, same parent: REFUSED (correct)')

# ...but the same name elsewhere is fine, and so is another household's.
mk('Taxes', med.id)
print('  same name, different parent: allowed (correct)')
f = models.DocumentFolder(user_id=2, parent_id=home.id, name='Taxes',
                          is_trashed=0, created_at=now, updated_at=now)
db.add(f); db.commit()
print('  same name, different user: allowed (correct)')

print()
print('  --- a cycle that somehow existed must not hang the breadcrumb ---')
# Force one directly in the database, bypassing the endpoint that refuses it.
db.query(models.DocumentFolder).filter(
    models.DocumentFolder.id == home.id).update({'parent_id': stmts.id})
db.commit()
crumb = _breadcrumb(db, UID, stmts.id)
print('  breadcrumb returned', len(crumb), 'entries instead of looping')
assert len(crumb) <= 64, 'breadcrumb did not terminate'
print('  OK: bounded, returns a short path rather than hanging the request')

db.close()
print()
print('  ALL PASS')
