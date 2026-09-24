"""Run every verify_*.py in turn, and report one line each.

WHY THIS EXISTS RATHER THAN A for LOOP. Each script signs in for itself, and
the login endpoint allows ten attempts per five minutes per IP — which is
correct, and which a suite of twenty-five scripts walks straight into. Running
them back to back produced ten passes and fifteen "failures" that were all the
same 429, and that is a worse outcome than not running them: a suite that
cries wolf gets ignored, and the run where something is genuinely broken looks
exactly like this one.

So a 429 is not treated as a failure. It waits out the window and tries once
more, and only then believes the result.

    python verify_all.py           # everything
    python verify_all.py drive     # only scripts whose name contains "drive"

The browser ones (verify_*_ui.py) each start their own headless Chrome and
take longer; --no-ui skips them.
"""
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "venv", "Scripts", "python.exe")
if not os.path.isfile(PY):
    PY = sys.executable

#: The login limiter's window, plus a little. Waiting slightly longer than the
#: server does is the cheap side to be wrong on.
COOLDOWN = 310


#: Scripts that need something this runner does not provide, with the reason.
#: Listed rather than deleted: a script that quietly stops being run is a
#: script nobody notices has rotted, and "needs a mail catcher" is a different
#: state from "failing".
NEEDS_SETUP = {
    "verify_features.py": "wants its own throwaway server on 8090",
    "verify_sync.py": "takes a scratch dir and a port as arguments",
    "verify_chunked_upload.py": "builds its own database; run it directly",
    "verify_alerts.py": "needs an SMTP catcher on 127.0.0.1",
}


def is_rate_limited(out: str) -> bool:
    """Both wordings.

    The server says "Too many attempts"; urllib raises its own generic
    "HTTP Error 429: Too Many Requests" and never shows the body. Matching
    only the first meant a script that hit the limiter through an uncaught
    urlopen was reported as a genuine failure — which is exactly the false
    alarm this runner exists to prevent.
    """
    return "429" in out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    skip_ui = "--no-ui" in sys.argv

    names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, "verify_*.py")))
    names = [n for n in names if n != "verify_all.py"]
    if skip_ui:
        names = [n for n in names if not n.endswith("_ui.py")]
    if args:
        names = [n for n in names if any(a.lower() in n.lower() for a in args)]
    if not names:
        print("nothing matched")
        return 1

    print(f"{len(names)} scripts\n")
    passed, failed, skipped, waited = [], [], [], 0

    for i, n in enumerate(names, 1):
        print(f"[{i}/{len(names)}] {n:<32}", end="", flush=True)
        if n in NEEDS_SETUP and not args:
            print(f"SKIP  ({NEEDS_SETUP[n]})")
            skipped.append(n)
            continue
        for attempt in (1, 2):
            r = subprocess.run([PY, os.path.join(HERE, n)],
                               capture_output=True, text=True, timeout=900)
            out = (r.stdout or "") + (r.stderr or "")
            if r.returncode == 0:
                print("PASS")
                passed.append(n)
                break
            if attempt == 1 and is_rate_limited(out):
                # Not a failure: the login limiter doing its job.
                print(f"rate limited, waiting {COOLDOWN}s… ", end="", flush=True)
                time.sleep(COOLDOWN)
                waited += 1
                continue
            print("FAIL")
            failed.append((n, out))
            break

    print()
    print(f"  {len(passed)} passed, {len(failed)} failed"
          + (f", {len(skipped)} skipped" if skipped else "")
          + (f", waited out the login limiter {waited}x" if waited else ""))
    if skipped:
        print("  skipped (name them explicitly to run one anyway):")
        for n in skipped:
            print(f"    {n:<32} {NEEDS_SETUP[n]}")
    for n, out in failed:
        print(f"\n--- {n} " + "-" * (60 - len(n)))
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-12:]
        for ln in tail:
            print("   ", ln)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
