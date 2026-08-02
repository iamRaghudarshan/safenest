"""Build a portable the app bundle from the command line.

A thin wrapper over backend/app/bundler.py — the same code the "Move to another
computer" button in the app uses, so the two can never produce different bundles.

Run it with the backend's own Python:

    backend\\venv\\Scripts\\python.exe make_bundle.py

or just double-click "Create the app Bundle.bat".
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

from app import bundler  # noqa: E402


class C:
    _on = sys.stdout.isatty()
    B = "\033[1m" if _on else ""
    DIM = "\033[2m" if _on else ""
    G = "\033[32m" if _on else ""
    Y = "\033[33m" if _on else ""
    R = "\033[31m" if _on else ""
    X = "\033[0m" if _on else ""


def pause(msg="Press Enter to close..."):
    try:
        input(msg)
    except EOFError:
        pass


def title(t):
    print(f"\n{C.B}{t}{C.X}\n{C.DIM}{'-' * len(t)}{C.X}")


def ask_yes_no(q, default=True):
    hint = "Y/n" if default else "y/N"
    while True:
        a = input(f"  {q} {C.DIM}[{hint}]{C.X}: ").strip().lower()
        if not a:
            return default
        if a in ("y", "yes"):
            return True
        if a in ("n", "no"):
            return False


def ask(q, default=""):
    return input(f"  {q} {C.DIM}[{default}]{C.X}: ").strip() or default


def main() -> int:
    print(f"\n{C.B}  Create a portable the app bundle{C.X}")
    print(f"  {C.DIM}Everything needed to run the app on another Windows PC or Mac.{C.X}")

    title("Which computer is it for?")
    print("    1) Windows")
    print("    2) Mac")
    choice = ask("Choose 1 or 2", "1")
    platform = bundler.MAC if choice.strip() == "2" else bundler.WINDOWS

    title("What to include")
    with_data = ask_yes_no("Include your existing data (photos, documents, records)?", True)
    if with_data:
        print(f"  {C.Y}Note:{C.X} the bundle will then contain your vault encryption key and")
        print(f"        all your personal files. {C.B}Treat it like a password.{C.X}")
        print("        Copy it with a USB drive — not email, not a shared cloud folder.\n")
    make_zip = ask_yes_no("Also compress it into a single .zip file?", False)
    out_root = Path(ask("Where should it be created?",
                        str(bundler.default_output_root()))).expanduser()

    title("Building")
    last = [""]

    def progress(step, pct):
        if step != last[0]:
            print(f"  {pct:>3}%  {step}")
            last[0] = step

    try:
        result = bundler.build(platform, with_data, out_root, progress,
                               make_zip=make_zip, rebuild_frontend=True)
    except Exception as exc:
        print(f"\n  {C.R}Failed:{C.X} {exc}\n")
        pause()
        return 1

    title("Done")
    print(f"  Folder : {C.B}{result['folder']}{C.X}")
    print(f"  Size   : {result['bytes'] / 1048576:.0f} MB")
    if result["with_data"]:
        print(f"  Data   : {'database exported' if result['database'] else 'DATABASE EXPORT FAILED'}"
              f", {result['media_files']:,} media files")
    if result.get("zip"):
        print(f"  Zip    : {C.B}{result['zip']}{C.X}")

    launcher = bundler.platform_layout(bundler.current_app_name())[platform]["launcher"]
    print(f"""
  {C.B}To use it on the other computer{C.X}
    1. Copy the folder onto a USB drive, then onto the other machine.
    2. Double-click  "{launcher}"
    3. Answer the few questions it asks. It installs the rest itself.

  Full instructions are in README.txt inside the bundle.
""")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        code = 130
    if os.name == "nt":
        pause()
    raise SystemExit(code)
