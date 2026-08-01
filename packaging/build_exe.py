"""Package FinMate into an executable a customer can double-click.

    python packaging/build_exe.py                 build for this computer's OS
    python packaging/build_exe.py --no-models     smaller build; models download later

ONE FOLDER, NOT ONE FILE — and that is deliberate. The payload is roughly 680 MB
(cv2 117 MB, transformers 98 MB, onnxruntime 45 MB, the vision models 188 MB). A
one-file build unpacks all of it into a temporary directory on every single
launch, which turns a two-second start into most of a minute, every time. One
folder with FinMate.exe inside it is how desktop software of this size actually
ships.

CROSS-COMPILING IS NOT POSSIBLE. PyInstaller freezes the interpreter it is run
by, so a Windows build must run on Windows and a Mac build must run on a Mac.
Run this script on each machine you want to ship for.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

# notices.py sits beside this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND_DIST = ROOT / "frontend" / "dist"
MODELS = BACKEND / "models"
OUT = ROOT / "dist-app"
WORK = ROOT / "build-app"

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
APP_NAME = "FinMate"

# The version this build reports. Read from VERSION at the project root so the
# number lives in one place -- the customer's copy compares it against what the
# publisher offers, and a build that does not know its own version treats every
# release as newer and re-offers an update it already has.
def app_version() -> str:
    f = ROOT / "VERSION"
    try:
        return (f.read_text(encoding="utf-8").strip() or "2.0")
    except OSError:
        return "2.0"


# Imported dynamically at runtime, so PyInstaller's static scan never sees them.
HIDDEN = [
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "sqlalchemy.dialects.sqlite", "sqlalchemy.dialects.mysql", "pymysql",
    "onnxruntime", "onnxruntime.capi._pybind_state",
    "passlib.handlers.bcrypt", "bcrypt",
    "email.mime.text", "email.mime.multipart",
    "create_account", "create_admin", "wizard",
    "tkinter", "tkinter.ttk", "tkinter.filedialog",
]

# In a NATIVE build the app package is excluded from analysis (its machine-code
# form is shipped instead), which also hides everything it imports — PyInstaller
# discovers third-party packages by following our imports, and with ours gone it
# found none of them. The first attempt produced a 118 MB build missing fastapi,
# uvicorn, pydantic, cv2 and PIL: it would have failed on the customer's machine,
# not on ours. So the runtime dependencies are declared here instead.
NATIVE_DEPS = [
    "fastapi", "starlette", "uvicorn", "pydantic", "pydantic_settings",
    "dotenv", "multipart", "python_multipart",
    "sqlalchemy", "pymysql", "jwt", "passlib", "bcrypt", "cryptography",
    "PIL", "PIL.Image", "pillow_heif", "numpy", "cv2",
    "requests", "pywebpush", "py_vapid", "transformers", "rapidocr",
    "anyio", "h11", "click", "certifi", "charset_normalizer", "idna", "urllib3",
]

# Packages whose submodules we import by path rather than through their __init__.
# Every one of these must be collected whole in a native build -- see the note in
# the spec. Deliberately excludes numpy/cv2/PIL/transformers, which carry their
# own PyInstaller hooks.
_FRAMEWORKS = [
    "fastapi", "starlette", "uvicorn", "pydantic", "pydantic_settings",
    "sqlalchemy", "passlib", "jwt", "cryptography", "anyio", "pymysql",
    "pywebpush", "py_vapid", "rapidocr", "multipart", "dotenv", "requests",
    "h11", "click", "certifi", "charset_normalizer", "idna", "urllib3",
    "bcrypt", "pillow_heif", "onnxruntime", "email", "encodings",
]

# Packages that load data files from beside their own code. Anything added here
# is collected whole -- see the note in the spec about rapidocr's missing yaml.
_DATA_PKGS = ["rapidocr", "certifi", "onnxruntime"]

# Weight with no purpose in a packaged desktop app.
# tkinter stays IN: the packaged app shows its first-run window with it.
EXCLUDE = ["matplotlib", "pytest", "IPython", "notebook",
           "torch", "tensorflow", "jax", "scipy"]


def check_prerequisites(with_models: bool) -> None:
    if not (FRONTEND_DIST / "index.html").exists():
        raise SystemExit("frontend/dist is missing — run 'npm run build' in frontend/ first.")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("PyInstaller is not installed — run:  pip install pyinstaller")
    if with_models and not MODELS.is_dir():
        print("  ! backend/models is missing; building without the vision models.")


def app_modules() -> list[str]:
    """Every module under backend/app, named for import.

    They are listed explicitly because the entry point imports `app.main` from
    inside a function, and the routers are then imported from there. Static
    analysis does not reliably reach through that, and a router that PyInstaller
    misses is a 404 at runtime rather than a build error — the worst way to find
    out. Enumerating the package costs nothing and cannot silently miss one.
    """
    out = []
    pkg = BACKEND / "app"
    for path in sorted(pkg.rglob("*.py")):
        rel = path.relative_to(BACKEND).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            out.append(".".join(parts))
    return out


def compile_native() -> Path:
    """Compile backend/app into a single native module with Nuitka.

    Turns the whole package into machine code, so a customer's copy contains no
    Python source and no bytecode for our own logic — the licence gate cannot be
    opened in an editor and deleted, which is the point.

    Docstrings survive as string constants: they are runtime data, and Nuitka's
    --python-flag=no_docstrings produces a binary that dies at import on Python
    3.13 ("SystemError: bad argument to internal function"). They reveal intent,
    not editable logic, so the working build is the right trade.
    """
    try:
        import nuitka  # noqa: F401
    except ImportError:
        raise SystemExit(
            "Nuitka is not installed — run:  backend\\venv\\Scripts\\pip install nuitka")

    out = ROOT / "build-app" / "native"
    out.mkdir(parents=True, exist_ok=True)
    print("  Compiling backend/app to native code (this takes a few minutes)...")
    subprocess.run(
        [sys.executable, "-m", "nuitka", "--module", "app",
         "--include-package=app", "--assume-yes-for-downloads", "--remove-output",
         f"--output-dir={out}"],
        cwd=str(BACKEND), check=True)

    built = sorted(out.glob("app.*.pyd")) or sorted(out.glob("app.*.so"))
    if not built:
        raise SystemExit(f"Nuitka produced no module in {out}")
    print(f"  Compiled: {built[0].name} ({built[0].stat().st_size / 1048576:.1f} MB)")
    return built[0]


def mirror_msvc_runtime(internal: Path) -> None:
    """Put the Microsoft C++ runtime beside onnxruntime's own DLLs as well.

    PyInstaller collects one copy into _internal/. onnxruntime.dll is loaded from
    _internal/onnxruntime/capi/ and resolves ITS dependencies relative to itself,
    and since Python 3.8 an extension module's dependencies are no longer looked
    up on PATH. Where that lookup misses, the whole of onnxruntime fails with

        DLL load failed while importing onnxruntime_pybind11_state:
        A dynamic link library (DLL) initialization routine failed.

    which names nothing useful and takes OCR, face matching and photo search with
    it. Reported from a customer's Windows 10 machine; the build machine never
    showed it, which is what a search-path difference looks like from outside.

    Copied after the build rather than declared in the spec, because this takes
    the files PyInstaller actually chose instead of guessing where they live.
    """
    if not IS_WINDOWS:
        return
    target = internal / "onnxruntime" / "capi"
    if not target.is_dir():
        return
    copied = 0
    for name in ("msvcp140.dll", "msvcp140_1.dll", "MSVCP140_1.dll",
                 "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"):
        src = internal / name
        if src.is_file() and not (target / name).exists():
            shutil.copy2(src, target / name)
            copied += 1
    if copied:
        print(f"  C++ runtime mirrored beside onnxruntime: {copied} file(s)")


def brand_name() -> str:
    """What this installation calls itself, for the version resource.

    Deliberately NOT APP_NAME: that is the template filename the bundler looks
    for and renames per customer. This is the product's current name, so a
    renamed app produces an executable whose Properties agree with it.

    Falls back on BaseException, not Exception. Importing `app` pulls in
    app.config, which raises **SystemExit** when the secrets are missing — and
    SystemExit is not an Exception, so `except Exception` lets it straight
    through. A build machine has a .env and never sees this; a CI runner has none
    by design, and the whole build died on its last step after every compiled
    artefact had already been produced.
    """
    try:
        sys.path.insert(0, str(BACKEND))
        from app.bundler import current_app_name
        return current_app_name()
    except BaseException:
        return APP_NAME


def version_file(app_name: str) -> Path:
    """Windows version resource, so the .exe has real Properties.

    An executable with a blank Company and Copyright looks unfinished the first
    time anyone right-clicks it, and on Windows that panel is where people decide
    whether a program is trustworthy. Generated from the app's current name, so a
    renamed build carries the new one rather than the name it was written under.
    """
    year = time.localtime().tm_year
    name = app_name.replace("'", "").replace('"', "")
    lines = [
        "VSVersionInfo(",
        "  ffi=FixedFileInfo(filevers=(2,0,0,0), prodvers=(2,0,0,0),",
        "    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,",
        "    date=(0,0)),",
        "  kids=[",
        "    StringFileInfo([StringTable('040904B0', [",
        "      StringStruct('CompanyName', '%s')," % name,
        "      StringStruct('FileDescription', '%s - your records, on your own computer')," % name,
        "      StringStruct('FileVersion', '2.0.0.0'),",
        "      StringStruct('InternalName', '%s')," % name,
        "      StringStruct('LegalCopyright', '(c) %d %s. All rights reserved.')," % (year, name),
        "      StringStruct('OriginalFilename', '%s.exe')," % name,
        "      StringStruct('ProductName', '%s')," % name,
        "      StringStruct('ProductVersion', '2.0.0.0')])]),",
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])",
        "  ]",
        ")",
        "",
    ]
    out = WORK / "version.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(chr(10).join(lines), encoding="utf-8")
    return out



def spec_text(with_models: bool, native: Path | None = None) -> str:
    """The .spec is generated rather than kept by hand so the paths cannot drift."""
    # NOTE: backend/app is deliberately NOT in `datas`. Listing it there shipped
    # every .py file in the clear — a customer could open the licence gate in
    # Notepad and delete it. As imports they are compiled into the PYZ archive
    # instead, so no source text is present in the build at all.
    # The compiled module goes in as a binary so it lands in _internal, which is
    # on sys.path for a PyInstaller app — so `import app` finds the machine code.
    #
    # It must ALSO be excluded from the analysis. PyInstaller follows
    # `from app.main import app` in runner.py, finds the source on pathex and
    # helpfully bundles all 50 modules as bytecode — which would ship the very
    # thing the native build exists to remove, alongside the compiled copy.
    binaries = [(str(native), ".")] if native else []
    # Only the built frontend is a data file. create_account.py, create_admin.py
    # and wizard.py were listed here too and shipped as readable source in every
    # customer copy -- the same mistake as `backend/app` in `datas`, just smaller.
    # runner.py only ever *imports* them, and all three are in HIDDEN, so the
    # bytecode in the archive already satisfies that. create_admin.py in the clear
    # was the one worth removing: it promotes any existing email to admin and
    # resets its password, and a licensed copy is meant to have no admin at all.
    # (setup.py does run them as files with subprocess -- but that is the source
    # bundle, which ships its own copies and does not use this build.)
    datas = [
        (str(FRONTEND_DIST), "backend/frontend/dist"),
    ]
    if with_models and MODELS.is_dir():
        datas.append((str(MODELS), "backend/models"))

    icon = ROOT / "frontend" / "public" / "icon-512.png"
    icon_line = ""      # a .png is not a valid icon on either platform; skipped
    # Version resource is Windows-only; on a Mac PyInstaller ignores it, so it is
    # simply not emitted there rather than passed and quietly dropped.
    ver = ('\n    version=r\"' + str(version_file(brand_name())) + '\",') if IS_WINDOWS else ""
    return f'''# -*- mode: python ; coding: utf-8 -*-
# GENERATED by packaging/build_exe.py — edit that, not this.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Naming a package in hiddenimports imports only the package itself; PyInstaller
# then follows whatever its __init__ imports. fastapi's __init__ does not import
# fastapi.middleware.cors, so the packaged app died at its sixth line with
# ModuleNotFoundError -- after printing a healthy banner, which is why it read as
# working. With `app` excluded there is nothing left to discover these by
# following, so every submodule of the frameworks is collected explicitly.
#
# Only the frameworks. numpy, cv2, PIL and transformers ship PyInstaller hooks of
# their own that already do this correctly, and collecting transformers' submodule
# tree by hand takes minutes and pulls in optional ML backends we exclude.
_FRAMEWORKS = {_FRAMEWORKS!r}
_hidden = {((HIDDEN + NATIVE_DEPS) if native else HIDDEN + app_modules())!r}
for _pkg in _FRAMEWORKS:
    try:
        _hidden += collect_submodules(_pkg)
    except Exception as _exc:
        print(f"[spec] could not collect submodules of {{_pkg}}: {{_exc}}")
_hidden = sorted(set(_hidden))

# Packages that read files from beside their own code at runtime. PyInstaller
# collects a package's .py/.pyd but NOT its data unless a hook says so, and
# rapidocr has no hook -- so every customer copy started with
#   [ocr] unavailable: No such file or directory: ..._internal/rapidocr/default_models.yaml
# and text recognition on documents and photos was dead. It printed a tidy
# warning and carried on, so nothing failed loudly enough to be noticed.
_datas = {datas!r}
for _pkg in {_DATA_PKGS!r}:
    try:
        _datas += collect_data_files(_pkg)
    except Exception as _exc:
        print(f"[spec] could not collect data files of {{_pkg}}: {{_exc}}")

a = Analysis(
    [r"{ROOT / 'packaging' / 'runner.py'}"],
    pathex=[r"{BACKEND}", r"{ROOT / 'bundle'}"],
    binaries={binaries!r},
    datas=_datas,
    hiddenimports=_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes={((EXCLUDE + app_modules()) if native else EXCLUDE)!r},
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="{APP_NAME}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,{icon_line}{ver}
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[], name="{APP_NAME}",
)
'''


def build(with_models: bool, native: bool = False) -> Path:
    check_prerequisites(with_models)
    native_mod = compile_native() if native else None
    WORK.mkdir(parents=True, exist_ok=True)
    spec = WORK / f"{APP_NAME}.spec"
    spec.write_text(spec_text(with_models, native_mod), encoding="utf-8")

    print(f"  Building {APP_NAME} for {platform.system()} ({platform.machine()})")
    print(f"  Models included: {'yes' if with_models and MODELS.is_dir() else 'no'}")
    started = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(spec),
         "--distpath", str(OUT), "--workpath", str(WORK / "tmp"), "--noconfirm"],
        cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit("PyInstaller failed — see the messages above.")

    app_dir = OUT / APP_NAME
    mirror_msvc_runtime(app_dir / "_internal")
    # Stamped as a plain file rather than baked into the binary: the
    # bundler renames the executable per customer, and a version that
    # travels beside it survives that untouched.
    (app_dir / "version.txt").write_text(app_version() + "\n", encoding="ascii")
    print(f"  Version stamped: {app_version()}")

    # A second home under dist-app/<platform>/, so one machine can hold a Windows
    # build and a Mac build at once and issue customer copies for either. The
    # original location stays exactly where it was, so every existing script and
    # every existing bundle path keeps working.
    plat = "mac" if IS_MAC else "windows"
    shared = OUT / plat / APP_NAME
    try:
        if shared.exists():
            shutil.rmtree(shared)
        shared.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app_dir, shared)
        print(f"  Also placed at: dist-app/{plat}/{APP_NAME}")
    except OSError as exc:
        print(f"  ! could not place the per-platform copy: {exc}")

    # Collect the licences of everything that actually shipped. Done here, after
    # PyInstaller has finished, because the dist-info folders in the OUTPUT are
    # the authoritative list: requirements.txt says what we asked for, this says
    # what the customer receives — transitive packages included.
    try:
        import notices
        n = notices.write(app_dir / "_internal",
                          app_dir / "THIRD-PARTY-NOTICES.txt",
                          brand_name(), time.localtime().tm_year,
                          extra=NATIVE_DEPS)
        print(f"  Third-party notices: {n} packages")
    except BaseException as exc:
        # BaseException on purpose, as a second guard: everything above this point
        # is already built, and no notices file is worth throwing that away.
        print(f"  ! could not write third-party notices: {exc}")

    size = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file())
    mins = (time.time() - started) / 60
    print(f"\n  Built in {mins:.1f} min — {size / 1e6:.0f} MB")
    print(f"  Folder    : {app_dir}")
    print(f"  Executable: {app_dir / (APP_NAME + '.exe' if IS_WINDOWS else APP_NAME)}")
    return app_dir


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Package FinMate as an executable.")
    ap.add_argument("--no-models", action="store_true",
                    help="leave out the 188 MB vision models (downloaded on first use)")
    ap.add_argument("--clean", action="store_true", help="delete previous build output first")
    ap.add_argument("--native", action="store_true",
                    help="compile backend/app to machine code with Nuitka first — "
                         "the build then contains no source or bytecode of our own")
    args = ap.parse_args()
    if args.clean:
        shutil.rmtree(OUT, ignore_errors=True)
        shutil.rmtree(WORK, ignore_errors=True)
    build(with_models=not args.no_models, native=args.native)
