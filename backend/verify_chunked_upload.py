"""Resumable upload, driven against a real server.

The case that matters is the third one: a transfer that dies partway must
continue from where it stopped, not start again. That is the whole reason a
300 MB video on a home upstream ever finishes.
"""
import os, sys, json, io as _io, urllib.request, urllib.error, uuid
S = "C:/Users/Lenovo/AppData/Local/Temp/claude/d--AI-TUBE/b6c2adb3-9145-4802-8c66-a8b7fb4c29ac/scratchpad/synctest"
os.environ.update(DB_ENGINE="sqlite", DB_FILE=S + "/t.db", MEDIA_ROOT=S + "/media",
                  JWT_SECRET="test-only-secret-for-verification-not-real-0001",
                  MEDIA_SECRET="test-only-media-secret-for-verification-0002",
                  VAULT_KEY_HEX="1" * 64, PUBLIC_BASE_URL="")
sys.path.insert(0, "D:/AI PRO/finmate-react/backend")
from app.database import SessionLocal
from app.models import User, GalleryPhoto
from app.security import create_token, hash_password
from app import ist
from PIL import Image

db = SessionLocal()
u = db.query(User).filter(User.email == "chunk@test.local").first()
if not u:
    u = User(email="chunk@test.local", name="Chunk", role="admin",
             password_hash=hash_password("c" * 14), created_at=ist.now())
    db.add(u); db.commit()
tok = create_token(u)
uid = u.id
db.close()

PORT = "8090"
ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1;  print("  PASS  " + name)
    else:    fail += 1; print("  FAIL  " + name + "  " + str(extra))

buf = _io.BytesIO()
Image.new("RGB", (1600, 1200), (90, 140, 200)).save(buf, "JPEG", quality=90)
PHOTO = buf.getvalue()
print("  test image:", len(PHOTO), "bytes")

def req(method, path, data=None):
    r = urllib.request.Request("http://127.0.0.1:%s%s" % (PORT, path),
                               data=data, method=method,
                               headers={"Authorization": "Bearer " + tok,
                                        "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read()
        try: return e.code, json.loads(body)
        except Exception: return e.code, {"detail": body[:200].decode("utf8", "ignore")}

print("1) a fresh upload id has nothing")
uid1 = str(uuid.uuid4())
st, out = req("GET", "/api/gallery/upload/status?upload_id=" + uid1)
check("status answers 0 for an unknown id", st == 200 and out["received"] == 0, out)

print("2) a whole file in three chunks")
third = len(PHOTO) // 3
parts = [PHOTO[:third], PHOTO[third:third*2], PHOTO[third*2:]]
sent = 0
for i, part in enumerate(parts):
    last = (i == len(parts) - 1)
    st, out = req("POST",
        "/api/gallery/upload/chunk?upload_id=%s&offset=%d&total=%d&filename=r.jpg"
        % (uid1, sent, len(PHOTO) if last else 0), part)
    sent += len(part)
    if not last:
        check("chunk %d accepted, not complete" % (i + 1),
              st == 200 and out.get("complete") is False, out)
check("the last chunk completes it", st == 200 and out.get("complete") is True, out)
made = (out.get("item") or {}).get("id")
check("and it produced a photo", isinstance(made, int), out)

print("3) THE ONE THAT MATTERS: resume after a break")
uid2 = str(uuid.uuid4())
half = len(PHOTO) // 2
st, out = req("POST",
    "/api/gallery/upload/chunk?upload_id=%s&offset=0&total=0&filename=r2.jpg" % uid2,
    PHOTO[:half])
check("first half lands", st == 200 and out["received"] == half, out)

# ...the connection dies here. A new run asks where to continue from.
st, out = req("GET", "/api/gallery/upload/status?upload_id=" + uid2)
check("the computer remembers how much it has", out.get("received") == half, out)

st, out = req("POST",
    "/api/gallery/upload/chunk?upload_id=%s&offset=%d&total=%d&filename=r2.jpg"
    % (uid2, half, len(PHOTO)), PHOTO[half:])
check("it resumes and finishes", st == 200 and out.get("complete") is True, out)
check("NOTHING WAS RE-SENT: only the second half went twice-over the wire",
      out.get("received") == len(PHOTO), out)

print("4) a wrong offset is refused rather than silently corrupting")
uid3 = str(uuid.uuid4())
req("POST", "/api/gallery/upload/chunk?upload_id=%s&offset=0&total=0&filename=x.jpg" % uid3,
    PHOTO[:100])
st, out = req("POST",
    "/api/gallery/upload/chunk?upload_id=%s&offset=5000&total=0&filename=x.jpg" % uid3,
    PHOTO[100:200])
check("a bad offset is a 409", st == 409, (st, out))
check("and it says where to resume from", "100" in str(out.get("detail", "")), out)

print("5) abandoning clears the part file")
st, out = req("POST", "/api/gallery/upload/abandon?upload_id=" + uid3)
st, out = req("GET", "/api/gallery/upload/status?upload_id=" + uid3)
check("nothing is left behind", out.get("received") == 0, out)

print("6) the finished photo is really in the gallery")
db = SessionLocal()
n = db.query(GalleryPhoto).filter(GalleryPhoto.user_id == uid,
                                  GalleryPhoto.is_trashed == False).count()
db.close()
check("photos stored", n >= 1, n)

print()
print("%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
