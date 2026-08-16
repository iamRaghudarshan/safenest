"""Read the Firebase values out of the files Firebase gives you.

WHY NOT TYPE THEM IN. The API key differs between the Android app and the iOS
app, and neither is the "Web API Key" shown on the project settings page —
that one belongs to a web app that does not exist here. Transcribing them off a
screen is how the wrong one ends up compiled in, and the failure is a
registration Firebase refuses with an error naming neither platform.

So: point this at the two config files Firebase generates, and it takes the
right value from each.

    google-services.json      Android  (Project settings -> Android app -> download)
    GoogleService-Info.plist  iOS      (Project settings -> iOS app -> download)

Either may be missing — one platform configured is a perfectly good state.
"""
import json
import pathlib
import plistlib
import re
import sys

OUT = pathlib.Path(r"D:\AI PRO\safenest-mobile\firebase.env")


def from_google_services(path: pathlib.Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    info = d["project_info"]
    client = d["client"][0]
    return {
        "FCM_PROJECT_ID": info["project_id"],
        "FCM_SENDER_ID": info["project_number"],
        "FCM_APP_ID_ANDROID": client["client_info"]["mobilesdk_app_id"],
        "FCM_API_KEY_ANDROID": client["api_key"][0]["current_key"],
    }


def from_plist(path: pathlib.Path) -> dict:
    with open(path, "rb") as fh:
        d = plistlib.load(fh)
    return {
        "FCM_PROJECT_ID": d["PROJECT_ID"],
        "FCM_SENDER_ID": d["GCM_SENDER_ID"],
        "FCM_APP_ID_IOS": d["GOOGLE_APP_ID"],
        "FCM_API_KEY_IOS": d["API_KEY"],
    }


def main(paths: list[str]) -> int:
    values: dict[str, str] = {}
    seen = []
    for raw in paths:
        p = pathlib.Path(raw.strip().strip('"'))
        if not p.is_file():
            print(f"  not found, skipping: {p}")
            continue
        try:
            if p.suffix == ".json":
                values.update(from_google_services(p))
                seen.append("Android")
            elif p.suffix == ".plist":
                values.update(from_plist(p))
                seen.append("iOS")
            else:
                print(f"  not a Firebase config file: {p.name}")
        except Exception as exc:
            print(f"  could not read {p.name}: {exc}")

    if not values:
        print("\nNothing read. Download the config files from the Firebase console.")
        return 1

    # A missing platform is fine; a WRONG one is not. Sanity-check the shapes
    # rather than writing whatever was in the file.
    for key, pattern in (("FCM_APP_ID_ANDROID", r"^1:\d+:android:[0-9a-f]+$"),
                         ("FCM_APP_ID_IOS", r"^1:\d+:ios:[0-9a-f]+$"),
                         ("FCM_SENDER_ID", r"^\d+$")):
        if key in values and not re.match(pattern, values[key]):
            print(f"  [!] {key} does not look right: {values[key]}")
            return 1

    OUT.write_text(
        "# Written from the Firebase config files. Not secret — these identify\n"
        "# the project; the service-account key on the server is what authorises\n"
        "# sending. Gitignored because the repo is public.\n"
        + "".join(f"{k}={v}\n" for k, v in sorted(values.items())),
        encoding="utf-8", newline="\n")

    print(f"\n  read: {', '.join(seen)}")
    for k, v in sorted(values.items()):
        shown = v if len(v) < 46 else v[:20] + "..." + v[-10:]
        print(f"    {k:22} {shown}")
    print(f"\n  written to {OUT}")
    if "FCM_APP_ID_IOS" not in values:
        print("  note: no iOS config — push will work on Android only")
    if "FCM_APP_ID_ANDROID" not in values:
        print("  note: no Android config — push will work on iOS only")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
