"""Collect the licences of everything that actually ships, into one file.

WHY THIS EXISTS
The build bundles around sixty open-source packages. Several — OpenCV, requests,
transformers, bcrypt — are Apache 2.0, which requires their notices to travel
with any binary distribution; the MIT and BSD ones require their copyright line
and permission notice to be included too. PyInstaller does copy each package's
licence into `_internal/<pkg>.dist-info/licenses/`, so the obligation is met in
the strictest reading. But nobody has ever found a licence there. A single file
beside the executable is what a customer, a reseller or an auditor expects to
see, and it is the difference between complying and being able to show it.

WHAT IT READS
The build output itself, not the project's requirements. Requirements say what we
asked for; the dist-info folders say what was actually shipped, including the
transitive packages nobody listed. Anything that ended up in the customer's hands
belongs in the file.
"""
import io
import re
from pathlib import Path

# Read from the built folder rather than the environment, so the file can never
# describe a package that was not in fact included.
_LICENCE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "LICENSE.txt",
                  "LICENSE.md", "LICENSE.rst")


def _meta(dist: Path) -> dict:
    """Name, version and licence for one .dist-info folder."""
    out = {"name": dist.name.split("-")[0], "version": "", "licence": ""}
    meta = dist / "METADATA"
    if not meta.is_file():
        return out
    classifiers = []
    for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line[0].isspace():
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "name":
            out["name"] = val
        elif key == "version":
            out["version"] = val
        elif key in ("license-expression", "license") and val and len(val) < 80:
            # A short value is an SPDX id or name; a long one is the whole licence
            # text pasted into the header, which is not useful here.
            out["licence"] = out["licence"] or val
        elif key == "classifier" and "License ::" in val:
            classifiers.append(val.split("::")[-1].strip())
    if not out["licence"] and classifiers:
        out["licence"] = classifiers[0]
    return out


def _licence_text(dist: Path) -> str:
    """The licence file shipped with the package, if there is one."""
    candidates = []
    lic_dir = dist / "licenses"
    if lic_dir.is_dir():
        candidates.extend(sorted(lic_dir.rglob("*")))
    candidates.extend(sorted(dist.glob("LICEN*")) + sorted(dist.glob("COPYING*")))
    for path in candidates:
        if path.is_file() and path.stat().st_size < 200_000:
            try:
                return path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
    return ""


def _from_environment(name: str) -> dict:
    """Metadata for a package that shipped without its .dist-info folder.

    PyInstaller only copies dist-info when something reads it at runtime, so the
    heavyweights — cv2, onnxruntime, sqlalchemy, transformers, bcrypt — arrive as
    plain folders with no metadata beside them. They are also the ones carrying
    Apache-2.0 obligations, so missing them would defeat the point of this file.
    Their licence is looked up in the build environment instead, which is where
    they were installed from.
    """
    import importlib.metadata as md
    out = {"name": name, "version": "", "licence": "", "text": ""}
    for dist in md.distributions():
        try:
            top = (dist.read_text("top_level.txt") or "").split()
        except Exception:
            top = []
        meta = dist.metadata
        dist_name = meta.get("Name") or ""
        low = name.lower()
        if (low != dist_name.lower() and low not in [t.lower() for t in top]
                and low != dist_name.lower().replace("-", "_")):
            continue
        out["name"] = dist_name or name
        out["version"] = meta.get("Version") or ""
        lic = meta.get("License-Expression") or meta.get("License") or ""
        if lic and len(lic) < 80:
            out["licence"] = lic
        if not out["licence"]:
            for c in meta.get_all("Classifier") or []:
                if "License ::" in c:
                    out["licence"] = c.split("::")[-1].strip()
                    break
        for f in dist.files or []:
            if Path(str(f)).name.upper().startswith(("LICENSE", "LICENCE", "COPYING")):
                try:
                    out["text"] = dist.read_text(str(f)) or ""
                    break
                except Exception:
                    pass
        return out
    return out


def collect(internal_dir: Path, extra=None) -> list[dict]:
    """Every distribution present in the build, sorted by name."""
    found = []
    for dist in sorted(internal_dir.glob("*.dist-info")):
        info = _meta(dist)
        info["text"] = _licence_text(dist)
        found.append(info)

    # Now the packages that shipped as bare folders. Anything with an __init__
    # is a Python package the customer received, whether or not its metadata
    # came along.
    have = {f["name"].lower().replace("-", "_") for f in found}
    candidates = [f.name for f in sorted(internal_dir.iterdir())
                  if f.is_dir() and not f.name.endswith(".dist-info")
                  and not f.name.startswith(("_", "."))]
    # Packages whose source lives entirely in the archive leave no folder at all
    # (requests is one), so the declared runtime dependencies are checked too.
    candidates += list(extra or [])

    for name in candidates:
        folder = internal_dir / name
        # No __init__.py test: PyInstaller moves the source into the archive and
        # leaves only binaries behind, so the folders for sqlalchemy, onnxruntime
        # and bcrypt look empty of Python — and those are Apache/MIT packages
        # whose notices must ship. Judging by "does it resolve to an installed
        # distribution" is the reliable test.
        if name.lower().replace("-", "_") in have:
            continue
        info = _from_environment(folder.name)
        if info["licence"] or info["text"]:
            # A licence file sitting inside the package folder beats nothing.
            if not info["text"] and folder.is_dir():
                for cand in sorted(folder.glob("LICEN*")) + sorted(folder.glob("*/LICEN*")):
                    if cand.is_file() and cand.stat().st_size < 200_000:
                        info["text"] = cand.read_text(encoding="utf-8", errors="replace")
                        break
            found.append(info)
            have.add(info["name"].lower().replace("-", "_"))
    # De-duplicate on name, keeping the first — a build should not contain two
    # versions of a package, but if it does, saying so twice helps nobody.
    seen, out = set(), []
    for f in found:
        key = f["name"].lower()
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def write(internal_dir: Path, target: Path, app_name: str, year: int, extra=None) -> int:
    """Write THIRD-PARTY-NOTICES.txt. Returns how many packages it covers."""
    packages = collect(internal_dir, extra)
    bar = "=" * 74
    lines = [
        f"{app_name} — third-party notices",
        bar,
        "",
        f"{app_name} is built on open-source software. The packages below are",
        "included in this program. Each remains the property of its authors and is",
        "used under the licence shown. Their licence terms apply to those packages",
        f"only, not to {app_name} itself.",
        "",
        f"This file lists every package actually present in this build ({len(packages)}).",
        "",
        bar,
        "SUMMARY",
        bar,
        "",
    ]
    width = max((len(p["name"]) for p in packages), default=20)
    for p in packages:
        lines.append(f"  {p['name']:<{width}}  {p['version']:<12}  {p['licence'] or 'see below'}")

    lines += ["", bar, "FULL LICENCE TEXTS", bar, ""]
    for p in packages:
        if not p["text"]:
            continue
        lines += ["", "-" * 74,
                  f"{p['name']} {p['version']}", "-" * 74, "", p["text"], ""]

    lines += [
        "", bar,
        f"(c) {year} {app_name}. All rights reserved.",
        f"The {app_name} application itself is licensed, not sold. See README.txt.",
        bar, "",
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    io.open(target, "w", encoding="utf-8", newline="\r\n").write("\n".join(lines))
    return len(packages)
