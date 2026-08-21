"""A setup window, for people who should never have to meet a terminal.

Standard library only — tkinter and nothing else. This runs BEFORE the
dependencies are installed, so anything from pip is unavailable by definition,
and it has to work on a machine that has never seen this app.

Everything here is optional. `run()` returns None when there is no display, no
tkinter, or the person closes the window, and setup.py falls back to asking the
same questions at the prompt. A missing GUI toolkit must never be the reason
somebody cannot install the app.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, ttk
    # macOS ships an ancient Tk 8.5 with its Command Line Tools Python. Apple has
    # deprecated it and it is known to hang or crash outright on recent macOS —
    # which is worse than having no window at all, because a crash takes the whole
    # installer down instead of falling back. Anything below 8.6 uses the prompts.
    HAVE_TK = float(tk.TkVersion) >= 8.6
except Exception:                       # headless Linux, or a Python built without Tk
    HAVE_TK = False

# The brand shown to the person installing. Written capitalised on purpose: a
# SOURCE bundle has this constant rewritten case-sensitively at build time
# (bundler.BRAND_TOKEN). A COMPILED copy cannot be rewritten -- the file is
# bytecode inside the executable -- so it reads APP_BRAND from the environment,
# which the packaged launcher sets from the database before opening this window.
# Without that, a renamed app asked for its password under the old name.
BRAND_NAME = "App"


def brand() -> str:
    import os
    return (os.environ.get("APP_BRAND") or "").strip() or BRAND_NAME


def _title() -> str:
    return f"{brand()} Setup"


WIDTH, HEIGHT = 760, 720

# Kept close to the app's own palette so the installer and the app look related.
STEPS = (("1", "Your data"), ("2", "Access"), ("3", "Sign in"))

# Named sizes rather than numbers scattered through the layout — the previous
# 8pt hints were genuinely hard to read on a laptop screen.
F_TITLE = ("Segoe UI", 24, "bold")
F_SUB = ("Segoe UI", 11)
F_H = ("Segoe UI", 17, "bold")
F_BODY = ("Segoe UI", 11)
F_LABEL = ("Segoe UI", 11, "bold")
F_HINT = ("Segoe UI", 10)
F_BTN = ("Segoe UI", 12, "bold")

# Usable text width: window minus the 214px rail minus the pane's own padding.
TEXTW = WIDTH - 214 - 96

BRAND = "#5b5bd6"


def accent() -> str:
    """The app's theme colour, so the installer matches the app it installs.

    Same reasoning as brand(): a renamed app with its own colour should not open
    a setup window in the colour it was compiled with.
    """
    import os
    import re
    v = (os.environ.get("APP_THEME") or "").strip()
    return v if re.fullmatch(r"#[0-9a-fA-F]{6}", v or "") else BRAND


def _shade(hex_colour: str, factor: float) -> str:
    """Darken a #rrggbb colour. Used for button hover and pressed states."""
    try:
        r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % (int(r * factor), int(g * factor), int(b * factor))
    except Exception:
        return hex_colour


INK = "#1a1a2e"
SOFT = "#6b7280"
LINE = "#e5e7eb"
BG = "#f7f7fb"
CARD = "#ffffff"
OK_ = "#16a34a"
WARN = "#d97706"


def _dpi_aware() -> None:
    """Tell Windows this program draws its own pixels.

    Without it Windows renders the window at 96 DPI and then stretches the result
    to the display's scaling factor, so on any laptop set to 125% or 150% — which
    is most of them now — every label comes out visibly soft and the window lands
    in the wrong place. Harmless everywhere else; macOS handles this itself.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)      # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()        # older Windows
        except Exception:
            pass


def _is_system_folder(p: Path) -> bool:
    """Folders Windows guards, which must never be written to as a test.

    Named rather than detected: there is no portable way to ask "will writing here
    stall?", and the answer is only ever yes for this handful.
    """
    if os.name != "nt":
        return False
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    drive = Path(resolved.anchor)
    guarded = {drive / n for n in ("Users", "Windows",
                                   "Program Files", "Program Files (x86)")}
    return resolved in guarded


def writability(path: str) -> tuple[bool, str]:
    """Can the app actually save into this folder? (ok, reason-if-not)

    Checked here, at the moment the folder is chosen, because the alternative is
    what used to happen: the whole setup ran, seventy packages downloaded, and
    only then did the first write fail with a Python traceback. The usual culprit
    is a drive formatted on Windows — macOS mounts NTFS read-only, so it looks
    perfectly fine right up until something tries to write to it.
    """
    target = Path(path).expanduser()
    probe = target
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent          # nearest folder that exists is what we can test
    if not probe.exists():
        return False, "That location does not exist."
    if not probe.is_dir():
        return False, "That is a file, not a folder."

    # NEVER write into a folder the app does not own, even to test it. Windows
    # Controlled Folder Access intercepts a write to C:\Users and does not return
    # -- measured here: no result after two minutes -- so probing an ancestor froze
    # the setup window solid for anyone who typed a path under one. Existence and
    # the read-only flag are metadata reads and always answer.
    if _is_system_folder(probe):
        if probe == target:
            return False, ("Windows protects that folder. Choose somewhere inside "
                           "your own user folder, or another drive.")
        # They typed a path whose parent does not exist either -- usually a
        # mistyped user name. Saying "Windows protects that folder" about a folder
        # they did not type is true of the ancestor and baffling to read.
        return False, (f"That folder cannot be created, because {probe} does not "
                       "allow it. Check the path, or pick one with Browse.")
    if probe != target:
        # The folder itself does not exist yet, so there is nothing safe to write
        # into. mkdir at the moment it is used is what proves it, and that failure
        # is reported with the path in it.
        if not os.access(probe, os.W_OK):
            return False, "That location cannot be written to."
        return True, ""

    try:
        import tempfile
        with tempfile.NamedTemporaryFile(dir=probe, prefix=".finmate-", suffix=".tmp"):
            pass
    except OSError as exc:
        if getattr(exc, "errno", None) == 30 or "read-only" in str(exc).lower():
            # Test the path the user actually typed, not the parent we probed —
            # on an unmounted or missing path the probe walks up to "/", which
            # would hide the very clue that explains the failure.
            if "/Volumes/" in target.as_posix():   # as_posix: str() gives \ on Windows
                return False, ("This drive is read-only. Drives formatted on Windows "
                               "(NTFS) cannot be written to by macOS — reformat it as "
                               "exFAT, or choose a folder in your home directory.")
            return False, "This location is read-only, so nothing can be saved there."
        return False, f"{brand()} cannot write here: {exc.strerror or exc}"
    return True, ""


def _scale_for_dpi(root) -> None:
    """Size the text to the screen rather than to an assumption about it."""
    try:
        dpi = root.winfo_fpixels("1i")
        if dpi > 0:
            root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass


class Btn:
    """A button that looks the same on Windows and macOS.

    tk.Button cannot be used for anything coloured: on macOS the Aqua theme draws
    its own control and IGNORES bg entirely, so a "purple button with white text"
    arrives as white text on a light grey button — effectively invisible, which is
    exactly what happened. A Frame with a Label inside honours colour on every
    platform, so this draws the button rather than asking the toolkit for one.
    """

    def __init__(self, parent, text, command, kind="primary"):
        self.command = command
        self.kind = kind
        self.enabled = True
        fill, ink, edge = self._palette(kind)
        self.frame = tk.Frame(parent, bg=fill, highlightthickness=1,
                              highlightbackground=edge, highlightcolor=edge,
                              cursor="hand2")
        self.label = tk.Label(self.frame, text=text, bg=fill, fg=ink,
                              font=F_BTN if kind == "primary" else F_BODY,
                              padx=30 if kind == "primary" else 20, pady=11)
        self.label.pack()
        for w in (self.frame, self.label):
            w.bind("<Button-1>", self._press)
            w.bind("<ButtonRelease-1>", self._release)
            w.bind("<Enter>", lambda e: self._paint(hover=True))
            w.bind("<Leave>", lambda e: self._paint())
        self._paint()

    @staticmethod
    def _palette(kind, hover=False, down=False):
        if kind == "primary":
            base = accent()
            # Hover and pressed are the theme colour darkened, so a rebranded app
            # gets matching states instead of the old hard-coded purple.
            fill = _shade(base, 0.80) if down else (_shade(base, 0.90) if hover else base)
            return fill, "white", fill
        if kind == "danger":
            return (BG if not hover else "#f0e6e6"), "#b91c1c", LINE
        return (BG if not hover else "#ececf6"), INK, LINE

    def _paint(self, hover=False, down=False):
        if not self.enabled:
            self.frame.config(bg=LINE, highlightbackground=LINE)
            self.label.config(bg=LINE, fg=SOFT)
            return
        fill, ink, edge = self._palette(self.kind, hover, down)
        self.frame.config(bg=fill, highlightbackground=edge)
        self.label.config(bg=fill, fg=ink)

    def _press(self, _e):
        if self.enabled:
            self._paint(down=True)

    def _release(self, _e):
        if not self.enabled:
            return
        self._paint(hover=True)
        self.command()

    def set_text(self, text):
        self.label.config(text=text)

    def set_enabled(self, on: bool):
        self.enabled = bool(on)
        self.frame.config(cursor="hand2" if on else "arrow")
        self._paint()

    def pack(self, **kw):
        self.frame.pack(**kw)
        return self

    def pack_forget(self):
        self.frame.pack_forget()

    def is_shown(self):
        return bool(self.frame.winfo_ismapped())


class Wizard:
    """Three steps: where the data lives, how it is reached, who signs in."""

    def __init__(self, cfg: dict, licensed: dict | None = None,
                 needs_account: bool = True, default_dir: str = ""):
        self.cfg = dict(cfg)
        self.licensed = licensed or {}
        self.needs_account = needs_account
        self.result: dict | None = None
        self.step = 0

        _dpi_aware()
        self.root = tk.Tk()
        self.root.title(_title())
        self.root.configure(bg=BG)
        _scale_for_dpi(self.root)
        self.root.minsize(WIDTH, HEIGHT)
        self._centre()

        style = ttk.Style()
        # 'aqua' on macOS and 'vista' on Windows are the native themes; 'clam' is
        # the reasonable fallback anywhere else. Picking one that does not exist
        # raises, so only ever set one that the platform reports.
        for name in ("aqua", "vista", "clam", "default"):
            if name in style.theme_names():
                style.theme_use(name)
                break

        self.vars = {
            "data_dir": tk.StringVar(value=self.cfg.get("data_dir") or default_dir),
            "port": tk.StringVar(value=str(self.cfg.get("port", "8080"))),
            "lan": tk.BooleanVar(value=bool(self.cfg.get("lan", True))),
            # A bought copy defaults to local-only. Anything else asks the customer
            # for infrastructure they were never sold.
            "internet": tk.StringVar(value=self.cfg.get("internet", "none")),
            "tunnel_token": tk.StringVar(value=self.cfg.get("tunnel_token", "")),
            "public_url": tk.StringVar(value=self.cfg.get("public_url", "")),
            "name": tk.StringVar(value=self.licensed.get("name", "")),
            "email": tk.StringVar(value=self.licensed.get("email", "")),
            "pw1": tk.StringVar(),
            "pw2": tk.StringVar(),
        }

        self._chrome()
        self._scroller()
        self._render()
        self.root.protocol("WM_DELETE_WINDOW", self._confirm_cancel)

    # ------------------------------------------------------------------ layout
    def _centre(self):
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - WIDTH) // 2
        y = max(0, (self.root.winfo_screenheight() - HEIGHT) // 3)
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    def _chrome(self):
        """The layout every desktop installer uses: branded rail on the left with
        the steps, white working area on the right, buttons along the bottom.

        People have installed software before. Matching the shape they already know
        means the window explains itself before they read a word of it."""
        # Bottom bar first: in tk, whatever packs to a side claims its space, so
        # the content area must be packed last to take what is left.
        foot = tk.Frame(self.root, bg=BG)
        foot.pack(fill="x", side="bottom")
        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x", side="bottom")

        self.next_btn = Btn(foot, "Next", self._next, "primary")
        self.next_btn.pack(side="right", padx=(0, 26), pady=18)
        self.back_btn = Btn(foot, "Back", self._back, "quiet")
        self.back_btn.pack(side="right", padx=(0, 12), pady=18)
        self.cancel_btn = Btn(foot, "Cancel", self._confirm_cancel, "quiet")
        self.cancel_btn.pack(side="left", padx=26, pady=18)
        self.dots = tk.Label(foot, text="", bg=BG, fg=SOFT, font=F_HINT)
        self.dots.pack(side="left")

        main = tk.Frame(self.root, bg=CARD)
        main.pack(fill="both", expand=True)

        rail = tk.Frame(main, bg=BRAND, width=214)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)      # keep the rail's width whatever it contains
        tk.Label(rail, text=brand(), bg=accent(), fg="white", font=F_TITLE,
                 anchor="w").pack(fill="x", padx=22, pady=(26, 2))
        tk.Label(rail, text="Setup", bg=BRAND, fg="#c9c9ef", font=F_SUB,
                 anchor="w").pack(fill="x", padx=22, pady=(0, 26))

        self.pips = []
        for num, label in STEPS:
            row = tk.Frame(rail, bg=BRAND)
            row.pack(fill="x", pady=2)
            dot = tk.Label(row, text=num, bg=BRAND, fg="#b9b9e8", width=3,
                           font=("Segoe UI", 11, "bold"), anchor="e")
            dot.pack(side="left", padx=(14, 0), pady=7)
            txt = tk.Label(row, text=label, bg=BRAND, fg="#b9b9e8", font=F_BODY,
                           anchor="w")
            txt.pack(side="left", fill="x", expand=True, padx=(10, 14), pady=7)
            self.pips.append((row, dot, txt))

        self.sub = tk.Label(rail, text="", bg=BRAND, fg="#a9a9dd", font=F_HINT,
                            anchor="w", justify="left", wraplength=170)
        self.sub.pack(side="bottom", fill="x", padx=22, pady=22)

        self.stage = tk.Frame(main, bg=CARD)
        self.stage.pack(side="left", fill="both", expand=True)

    def _mark_steps(self):
        """Colour the rail: done, current, still to come."""
        for i, (row, dot, txt) in enumerate(self.pips):
            if i == self.step:
                dot.config(fg="white", bg="#4a4ac4")
                txt.config(fg="white", font=("Segoe UI", 11, "bold"))
                row.config(bg="#4a4ac4")
            else:
                done = i < self.step
                dot.config(fg="#dcdcf5" if done else "#9a9ad4", bg=BRAND,
                           text=f"  {'✓' if done else STEPS[i][0]}  ")
                txt.config(fg="#dcdcf5" if done else "#9a9ad4", font=F_BODY)
                row.config(bg=BRAND)

    def _scroller(self):
        """A scrolling working area inside the stage.

        The tallest step does not fit once a display is scaled, and a field you
        can neither see nor scroll to is worse than an ugly layout. The scrollbar
        only appears when there is something below the fold.
        """
        outer = tk.Frame(self.stage, bg=CARD)
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, bg=CARD, highlightthickness=0)
        self._bar = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)

        self.body = tk.Frame(self.canvas, bg=CARD)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>",
                       lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        # Match the inner frame to the canvas width, or every card collapses to
        # the width of its longest single word.
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(
            int(-e.delta / 120), "units"))

    def _on_scroll(self, first: str, last: str):
        """Show the scrollbar only while it has a job to do."""
        self._bar.set(first, last)
        needed = not (float(first) <= 0.0 and float(last) >= 1.0)
        if needed and not self._bar.winfo_ismapped():
            self._bar.pack(side="right", fill="y")
        elif not needed and self._bar.winfo_ismapped():
            self._bar.pack_forget()

    def _card(self, title: str, blurb: str = "") -> tk.Frame:
        for w in self.body.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)     # a new step starts at its top, not mid-scroll
        wrap = tk.Frame(self.body, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=30, pady=26)
        tk.Label(wrap, text=title, bg=CARD, fg=INK, font=F_H,
                 anchor="w").pack(fill="x")
        if blurb:
            tk.Label(wrap, text=blurb, bg=CARD, fg=SOFT, font=F_BODY,
                     anchor="w", justify="left", wraplength=TEXTW).pack(
                fill="x", pady=(6, 4))
        tk.Frame(wrap, bg=LINE, height=1).pack(fill="x", pady=(14, 0))
        card = tk.Frame(wrap, bg=CARD)
        card.pack(fill="both", expand=True)
        return card

    def _field(self, parent, label: str, var, hint: str = "", show: str = "",
               width: int = 44):
        tk.Label(parent, text=label, bg=CARD, fg=INK, font=F_LABEL,
                 anchor="w").pack(fill="x", padx=2, pady=(18, 6))
        entry = self._entry(parent, var, show=show)
        if hint:
            tk.Label(parent, text=hint, bg=CARD, fg=SOFT, font=F_HINT,
                     anchor="w", justify="left", wraplength=TEXTW).pack(
                fill="x", padx=2, pady=(6, 0))
        return entry

    def _entry(self, parent, var, show: str = ""):
        """A text box with a soft border that lights up when focused.

        tk.Entry's own border is a hard hairline drawn by the toolkit: harsh on
        Windows, and a different shape again under Aqua, so the two platforms
        never match. Putting a borderless Entry inside a Frame means the border
        is ours — same colour, same thickness, same padding everywhere — and it
        can respond to focus, which a bare Entry cannot.
        """
        box = tk.Frame(parent, bg="white", highlightthickness=1,
                       highlightbackground=LINE, highlightcolor=LINE)
        box.pack(fill="x", padx=2)
        entry = tk.Entry(box, textvariable=var, font=F_BODY, relief="flat",
                         borderwidth=0, bg="white", fg=INK, show=show,
                         highlightthickness=0, insertbackground=INK)
        entry.pack(fill="x", padx=12, pady=10)
        entry.bind("<FocusIn>",
                   lambda e: box.config(highlightbackground=BRAND, highlightcolor=BRAND))
        entry.bind("<FocusOut>",
                   lambda e: box.config(highlightbackground=LINE, highlightcolor=LINE))
        return entry

    def _error(self, parent, text: str):
        tk.Label(parent, text="⚠  " + text, bg=CARD, fg="#dc2626", font=F_BODY,
                 anchor="w", justify="left", wraplength=TEXTW).pack(
            fill="x", padx=2, pady=(14, 4))

    # ------------------------------------------------------------------- steps
    def _steps(self) -> list:
        steps = [self._step_data, self._step_access]
        if self.needs_account:
            steps.append(self._step_account)
        return steps

    def _render(self):
        steps = self._steps()
        self.step = max(0, min(self.step, len(steps) - 1))
        self.dots.config(text=f"Step {self.step + 1} of {len(steps)}")
        self._mark_steps()
        if self.step == 0:
            self.back_btn.pack_forget()
        elif not self.back_btn.is_shown():
            self.back_btn.pack(side="right", padx=(0, 12), pady=18)
        self.next_btn.set_text("Finish" if self.step == len(steps) - 1 else "Next")
        steps[self.step]()

    def _step_data(self):
        self.sub.config(text="Where your information is kept")
        card = self._card(
            "Choose where to keep your data",
            "Your records, photos and documents all live in this one folder. "
            "Back it up and you have backed up everything.")
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=18, pady=(18, 0))
        tk.Label(row, text="Data folder", bg=CARD, fg=INK,
                 font=F_LABEL, anchor="w").pack(fill="x")
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=2, pady=(6, 0))
        box = tk.Frame(inner, bg="white", highlightthickness=1,
                       highlightbackground=LINE, highlightcolor=LINE)
        box.pack(side="left", fill="x", expand=True)
        # Chosen with Browse (or a click), not typed — same reasoning as the
        # first-run picker.
        folder = tk.Entry(box, textvariable=self.vars["data_dir"], font=F_BODY,
                          relief="flat", borderwidth=0, bg="white", fg=INK,
                          highlightthickness=0, insertbackground=INK,
                          state="readonly", readonlybackground="white",
                          cursor="hand2")
        folder.pack(fill="x", padx=12, pady=10)
        folder.bind("<Button-1>", lambda _e: self._browse())
        Btn(inner, "Browse…", self._browse, "quiet").pack(side="left", padx=(10, 0))

        chosen = self.vars["data_dir"].get() or "."
        can_write, why = writability(chosen)
        existing = Path(chosen) / "finmate.db"
        if not can_write:
            tk.Label(card, text="⚠  " + why, bg=CARD, fg="#dc2626",
                     font=F_BODY, anchor="w", justify="left",
                     wraplength=TEXTW).pack(fill="x", padx=18, pady=(12, 0))
        elif existing.exists():
            mb = existing.stat().st_size / 1048576
            tk.Label(card, text=f"✓  Existing {brand()} data found here ({mb:.1f} MB). "
                                f"It will be used as it is — nothing is overwritten.",
                     bg=CARD, fg=OK_, font=F_BODY, anchor="w",
                     justify="left", wraplength=TEXTW).pack(
                fill="x", padx=18, pady=(12, 0))
        else:
            tk.Label(card, text=f"A new, empty {brand()} will be set up here.",
                     bg=CARD, fg=SOFT, font=F_BODY, anchor="w").pack(
                fill="x", padx=18, pady=(12, 0))
        if getattr(self, "_err_data", ""):
            self._error(card, self._err_data)

    def _step_access(self):
        self.sub.config(text=f"How you reach {brand()}")
        card = self._card(
            f"How should {brand()} be reached?",
            "It always works on this computer. These settings decide who else can open it.")

        self._field(card, "Port", self.vars["port"],
                    "Leave this at 8080 unless something else on this computer uses it.",
                    width=10)

        tk.Checkbutton(card, text=f"  Let phones and tablets on my Wi-Fi open {brand()}",
                       variable=self.vars["lan"], bg=CARD, fg=INK, activebackground=CARD,
                       font=("Segoe UI", 10), anchor="w", selectcolor="white").pack(
            fill="x", padx=16, pady=(16, 0))

        tk.Label(card, text="From outside your home network",
                 bg=CARD, fg=INK, font=("Segoe UI", 10, "bold"), anchor="w").pack(
            fill="x", padx=18, pady=(16, 4))

        # A web address that came with the copy is a fact to state, not a choice to
        # offer. There is nothing here the customer could usefully change, and a
        # radio button would only let them switch off what they were given.
        hosted = self.cfg.get("_hosted_url")
        if hosted:
            tk.Label(card, text=f"✓  Your {brand()} is already set up at\n     {hosted}",
                     bg=CARD, fg=OK_, font=("Segoe UI", 10), anchor="w",
                     justify="left").pack(fill="x", padx=18, pady=(2, 4))
            tk.Label(card, text="It works from anywhere, on any device you sign in on. "
                                "Nothing to configure.",
                     bg=CARD, fg=SOFT, font=F_HINT, anchor="w",
                     justify="left", wraplength=TEXTW).pack(fill="x", padx=18)
            if getattr(self, "_err_access", ""):
                self._error(card, self._err_access)
            return

        carried = bool(self.cfg.get("_carried_tunnel"))
        # Somebody who was handed a licensed copy has no Cloudflare account and no
        # idea what a tunnel token is. Offering it as an equal choice sends them
        # looking for a value that does not exist, and whatever they paste is
        # rejected as invalid — which reads as "the app is broken".
        bought = bool(self.licensed.get("email")) and not carried
        options = [
            ("none", "No — this computer and my Wi-Fi only",
             "The right choice for almost everyone. Your phone still works over Wi-Fi."),
            ("quick", "Yes — a temporary free link",
             f"{brand()} creates a web address for you. Nothing to sign up for. "
             "The address changes each time you restart."),
            ("tunnel",
             ("Yes — keep the address this copy came with" if carried
              else "Yes — my own Cloudflare account (advanced)"),
             (self.cfg.get("public_url") or "Everything needed travelled with the bundle.")
             if carried else
             ("Only if you already own a domain on Cloudflare and have made a tunnel "
              "for it. Skip this if that means nothing to you.")),
        ]
        # Local-only first for a bought copy; for your own move, the tunnel is
        # usually the point, so leave the order that already made sense there.
        if not bought:
            options = [options[0], options[2], options[1]]

        for value, label, hint in options:
            tk.Radiobutton(card, text="  " + label, value=value,
                           variable=self.vars["internet"], bg=CARD, fg=INK,
                           activebackground=CARD, font=("Segoe UI", 10), anchor="w",
                           selectcolor="white", command=self._render).pack(
                fill="x", padx=16, pady=(6, 0))
            tk.Label(card, text=hint, bg=CARD, fg=SOFT, font=F_HINT,
                     anchor="w", justify="left", wraplength=TEXTW - 62).pack(
                fill="x", padx=(46, 8), pady=(0, 4))

        if self.vars["internet"].get() == "tunnel" and not carried:
            self._field(card, "Tunnel token", self.vars["tunnel_token"],
                        "From: cloudflared tunnel token YOUR-TUNNEL-NAME. "
                        "A long string starting 'ey'.")
            self._field(card, "Your web address", self.vars["public_url"],
                        "The address already pointed at THAT tunnel in Cloudflare.")
            # Typing an address here does not create it. It only labels this copy,
            # so links and notifications are built from the right origin. Two people
            # have now pasted the token of a DIFFERENT tunnel alongside an address
            # that was never created, joined the wrong tunnel, and taken down the
            # site that tunnel was already serving.
            tk.Label(card, text=(
                "Both must belong to the SAME tunnel. Typing an address here does not\n"
                "create it — set it up first with:\n"
                "    cloudflared tunnel create my-tunnel\n"
                "    cloudflared tunnel route dns my-tunnel my.example.com\n"
                "Using another computer's tunnel token will take that computer offline."),
                bg=CARD, fg=WARN, font=F_HINT, anchor="w",
                justify="left").pack(fill="x", padx=18, pady=(10, 0))
        if getattr(self, "_err_access", ""):
            self._error(card, self._err_access)

    def _step_account(self):
        self.sub.config(text="Your sign-in")
        if self.licensed.get("email"):
            card = self._card(
                "Set your password",
                f"This copy is licensed to {self.licensed.get('name', '')} "
                f"({self.licensed['email']}). Choose the password you will sign in with.")
        else:
            card = self._card("Create your account",
                              "This is the account you will sign in with. "
                              "It stays on this computer.")
            self._field(card, "Your name", self.vars["name"])
            self._field(card, "Email address", self.vars["email"],
                        "You will use this as your username.")

        self._field(card, "Password", self.vars["pw1"],
                    "At least 12 characters.", show="•")
        self._field(card, "Confirm password", self.vars["pw2"], show="•")
        if getattr(self, "_err_account", ""):
            self._error(card, self._err_account)

    # ------------------------------------------------------------- navigation
    def _browse(self):
        chosen = filedialog.askdirectory(title=f"Where should {brand()} keep your data?",
                                         initialdir=self.vars["data_dir"].get() or None)
        if chosen:
            self.vars["data_dir"].set(chosen)
            self._render()

    def _validate(self) -> str:
        if self.step == 0:
            chosen = self.vars["data_dir"].get().strip()
            if not chosen:
                return "Choose a folder for your data."
            ok_, why = writability(chosen)
            return "" if ok_ else why
        if self.step == 1:
            port = self.vars["port"].get().strip()
            if not port.isdigit() or not (1 <= int(port) <= 65535):
                return "The port must be a number between 1 and 65535."
            if self.cfg.get("_hosted_url"):
                return ""       # nothing on this step was theirs to get wrong
            if self.vars["internet"].get() == "tunnel" and not self.cfg.get("_carried_tunnel"):
                if not self.vars["tunnel_token"].get().strip():
                    return "Paste your tunnel token, or choose one of the other options."
                if not self.vars["public_url"].get().strip():
                    return "Enter the web address this tunnel serves."
            return ""
        if not self.licensed.get("email"):
            if len(self.vars["name"].get().strip()) < 2:
                return "Enter your name."
            email = self.vars["email"].get().strip()
            if "@" not in email or "." not in email.split("@")[-1]:
                return "Enter a valid email address."
        pw = self.vars["pw1"].get()
        if len(pw) < 12:
            return "Use a password of at least 12 characters."
        if len(set(pw)) < 5:
            return "That password is too repetitive — mix in more different characters."
        if pw != self.vars["pw2"].get():
            return "The two passwords do not match."
        return ""

    def _next(self):
        problem = self._validate()
        key = ("_err_data", "_err_access", "_err_account")[min(self.step, 2)]
        setattr(self, key, problem)
        if problem:
            self._render()
            return
        setattr(self, key, "")
        if self.step < len(self._steps()) - 1:
            self.step += 1
            self._render()
        else:
            self._finish()

    def _back(self):
        self.step = max(0, self.step - 1)
        self._render()

    def _finish(self):
        v = self.vars
        out = dict(self.cfg)
        out.update({
            "data_dir": str(Path(v["data_dir"].get()).expanduser().resolve()),
            "port": v["port"].get().strip(),
            "lan": bool(v["lan"].get()),
            "internet": v["internet"].get(),
            "db_engine": self.cfg.get("db_engine", "sqlite"),
        })
        if out["internet"] == "tunnel" and not self.cfg.get("_carried_tunnel"):
            url = v["public_url"].get().strip()
            if url and not url.startswith(("http://", "https://")):
                url = "https://" + url
            out["tunnel_token"] = v["tunnel_token"].get().strip()
            out["public_url"] = url.rstrip("/")
        elif out["internet"] != "tunnel":
            out.pop("tunnel_token", None)
            out["public_url"] = ""
        if self.needs_account:
            out["_account"] = {
                "name": (self.licensed.get("name") or v["name"].get()).strip(),
                "email": (self.licensed.get("email") or v["email"].get()).strip().lower(),
                "password": v["pw1"].get(),
                "role": "user" if self.licensed.get("email") else "admin",
            }
        out.pop("_carried_tunnel", None)
        self.result = out
        self.root.destroy()

    def _confirm_cancel(self):
        from tkinter import messagebox
        if messagebox.askyesno(
                "Quit setup?",
                f"{brand()} is not set up yet.\n\n"
                "Quit and lose the answers you have given so far?",
                parent=self.root, default="no", icon="question"):
            self._cancel()

    def _cancel(self):
        self.result = None
        self.root.destroy()

    def run(self) -> dict | None:
        self.root.mainloop()
        return self.result


def run(cfg: dict, licensed: dict | None = None, needs_account: bool = True,
        default_dir: str = "") -> dict | None:
    """Show the setup window. None means "fall back to the terminal"."""
    if not HAVE_TK:
        return None
    try:
        return Wizard(cfg, licensed, needs_account, default_dir).run()
    except Exception:
        # A broken display, a remote session without X, a Tk that will not start —
        # all of them mean the prompts, not a failed installation.
        return None


# --------------------------------------------------------------------- progress
class Progress:
    """A window that shows what setup is doing while it does it.

    Installing takes minutes on a first run. Without this the window would sit
    frozen and look crashed, which is when people force-quit halfway through.
    """

    def __init__(self, title: str = ""):
        self.q: queue.Queue = queue.Queue()
        self.done = threading.Event()
        self.failed: str | None = None
        self._ok = HAVE_TK
        if not self._ok:
            return
        try:
            _dpi_aware()
            self.root = tk.Tk()
            self.root.title(title)
            self.root.configure(bg=BG)
            _scale_for_dpi(self.root)
            self.root.geometry("480x180")
            self.root.resizable(False, False)
            tk.Label(self.root, text=f"Setting up {brand()}", bg=BG, fg=INK,
                     font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=24, pady=(24, 2))
            self.msg = tk.Label(self.root, text="Starting…", bg=BG, fg=SOFT,
                                font=F_BODY, anchor="w", wraplength=430,
                                justify="left")
            self.msg.pack(fill="x", padx=24)
            self.bar = ttk.Progressbar(self.root, mode="indeterminate", length=430)
            self.bar.pack(padx=24, pady=18)
            self.bar.start(12)
        except Exception:
            self._ok = False

    def step(self, text: str):
        if self._ok:
            self.q.put(text)

    def pump(self):
        """Drain queued messages and keep the window responsive."""
        if not self._ok:
            return
        try:
            while True:
                self.msg.config(text=self.q.get_nowait())
        except queue.Empty:
            pass
        if self.done.is_set():
            self.bar.stop()
            self.root.destroy()
            return
        self.root.after(120, self.pump)

    def run_until(self, work):
        """Run `work()` on a thread while this window stays alive."""
        if not self._ok:
            return work()
        result = {}

        def runner():
            try:
                result["value"] = work()
            except Exception as exc:
                result["error"] = exc
            finally:
                self.done.set()

        threading.Thread(target=runner, daemon=True).start()
        self.root.after(120, self.pump)
        self.root.mainloop()
        if "error" in result:
            raise result["error"]
        return result.get("value")


# ------------------------------------------------------------- account-only dialog
def free_space(path: str) -> str:
    """Room left on the drive holding `path`, or "" if it cannot be read."""
    import shutil as _sh
    probe = Path(path).expanduser()
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        gb = _sh.disk_usage(str(probe)).free / (1024 ** 3)
    except Exception:
        return ""
    return f"{gb:,.0f} GB free" if gb >= 1 else f"{gb * 1024:,.0f} MB free"


def _risky_location(path: str) -> str:
    """Why this folder would break the database, or "" if it is fine.

    SQLite memory-maps its write-ahead log. If the folder's storage goes away while
    the app is running — a cloud folder evicting files to save space, or an external
    /network drive disconnecting — that mapping faults and the app dies with a bus
    error (SIGBUS: "the backing vnode was force unmounted"). A real customer hit
    exactly this. The internal disk never does that, so it is the only safe home.
    """
    p = path.replace("\\", "/").lower()
    for token in ("/library/mobile documents", "/library/cloudstorage", "/icloud",
                  "/onedrive", "/dropbox", "/google drive", "/googledrive", "/pcloud"):
        if token in p:
            return ("A cloud folder (iCloud, OneDrive, Dropbox…) removes files to "
                    "save space, which corrupts the records. Keep them on this "
                    "computer's own disk instead.")
    if sys.platform == "darwin" and p.startswith("/volumes/"):
        return ("An external or network drive can disconnect while the app is "
                "running and corrupt the records. Keep them on this computer's own "
                "disk (the suggested folder is best).")
    if path.replace("/", "\\").startswith("\\\\"):
        return ("A network location can drop while the app is running and corrupt "
                "the records. Keep them on this computer's own disk.")
    return ""


def ask_location(default: str) -> str | None:
    """Where should this copy keep its records? None means "use the default".

    Asked because the app carries the whole of somebody's life admin — years of
    photos and scanned documents — and the folder it was unzipped into is often a
    Downloads folder on a full C: drive. The default is a stable spot on the
    internal disk; a different LOCAL folder is fine, but cloud-synced and
    external/network folders are refused — they disconnect and corrupt the database.
    """
    if not HAVE_TK:
        return None
    acc = accent()
    try:
        _dpi_aware()
        root = tk.Tk()
        root.title(_title())
        root.configure(bg=BG)
        _scale_for_dpi(root)
        root.resizable(False, False)

        head = tk.Frame(root, bg=acc)
        head.pack(fill="x")
        tk.Label(head, text=brand(), bg=acc, fg="white",
                 font=("Segoe UI", 19, "bold")).pack(anchor="w", padx=24, pady=(14, 0))
        tk.Label(head, text="Where should your records be kept?", bg=acc, fg="#e8ecf8",
                 font=("Segoe UI", 10)).pack(anchor="w", padx=24, pady=(0, 14))

        wrap = tk.Frame(root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=24, pady=18)
        tk.Label(wrap, text="The suggested folder on this computer is best. You can "
                            "pick another folder on this computer's own disk, but not "
                            "a cloud (iCloud/OneDrive/Dropbox) or external/USB drive — "
                            "those disconnect and corrupt your records.",
                 bg=BG, fg=SOFT, font=F_HINT, anchor="w", justify="left",
                 wraplength=520).pack(fill="x", pady=(0, 12))

        card = tk.Frame(wrap, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="both", expand=True)

        chosen = tk.StringVar(value=default)
        out: dict = {}

        tk.Label(card, text="Folder", bg=CARD, fg=INK, font=F_LABEL,
                 anchor="w").pack(fill="x", padx=18, pady=(16, 6))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=18)
        box = tk.Frame(row, bg="white", highlightthickness=1,
                       highlightbackground=LINE, highlightcolor=LINE)
        box.pack(side="left", fill="x", expand=True)
        # Read-only on purpose: the folder is CHOSEN with Browse (or by clicking
        # the field), never hand-typed. A typed path was the commonest way to
        # point the app at a place that did not exist, was misspelt, or could not
        # be written — and then the wizard had to reject it after the fact.
        entry = tk.Entry(box, textvariable=chosen, font=F_BODY, relief="flat",
                         borderwidth=0, bg="white", fg=INK, highlightthickness=0,
                         insertbackground=INK, state="readonly",
                         readonlybackground="white", cursor="hand2")
        entry.pack(fill="x", padx=12, pady=10)

        note = tk.Label(card, text="", bg=CARD, fg=SOFT, font=F_HINT,
                        anchor="w", justify="left", wraplength=500)
        note.pack(fill="x", padx=18, pady=(6, 0))

        def review(*_a):
            path = chosen.get().strip()
            if not path:
                note.config(text="Choose a folder.", fg=WARN)
                return
            risky = _risky_location(path)
            if risky:
                note.config(text=risky, fg=WARN)
                return
            ok, why = writability(path)
            space = free_space(path)
            if ok:
                note.config(text=f"{space} on this drive." if space else "Ready.", fg=OK_)
            else:
                note.config(text=why, fg=WARN)

        chosen.trace_add("write", review)

        def browse():
            picked = filedialog.askdirectory(
                title=f"Where should {brand()} keep your records?",
                mustexist=True, parent=root)
            if picked:
                chosen.set(str(Path(picked) / "data"))

        Btn(row, "Browse", browse, "ghost").frame.pack(side="left", padx=(10, 0))
        # Clicking anywhere in the field opens the picker too, so the whole row
        # reads as "choose a folder", not "type here".
        entry.bind("<Button-1>", lambda _e: browse())

        def finish():
            path = chosen.get().strip()
            risky = _risky_location(path)
            if risky:
                note.config(text=risky, fg=WARN)
                return
            ok, why = writability(path)
            if not ok:
                note.config(text=why, fg=WARN)
                return
            out["path"] = path
            root.destroy()

        foot = tk.Frame(root, bg=BG)
        foot.pack(fill="x", pady=(0, 14))
        Btn(foot, "Use this folder", finish, "primary").frame.pack(side="right", padx=24)

        review()
        root.bind("<Return>", lambda e: finish())
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.update_idletasks()
        root.geometry(f"+{(root.winfo_screenwidth() - root.winfo_width()) // 2}"
                      f"+{max(0, (root.winfo_screenheight() - root.winfo_height()) // 3)}")
        root.mainloop()
        return out.get("path")
    except Exception:
        return None


def ask_account(licensed: dict | None = None) -> dict | None:
    """Just the sign-in, for the packaged app's first run.

    The packaged build has already decided where its data lives and which port to
    use, so the three-step wizard would be two steps of nothing. None means no
    window was possible and the caller should ask at the console instead.
    """
    if not HAVE_TK:
        return None
    licensed = licensed or {}
    try:
        _dpi_aware()
        acc = accent()
        root = tk.Tk()
        root.title(_title())
        root.configure(bg=BG)
        _scale_for_dpi(root)
        root.resizable(False, False)

        head = tk.Frame(root, bg=acc)
        head.pack(fill="x")
        tk.Label(head, text=brand(), bg=acc, fg="white",
                 font=("Segoe UI", 19, "bold")).pack(anchor="w", padx=24, pady=(14, 0))
        tk.Label(head, text="Welcome — let's set up your sign-in", bg=acc, fg="#e8ecf8",
                 font=("Segoe UI", 10)).pack(anchor="w", padx=24, pady=(0, 14))

        wrap = tk.Frame(root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=24, pady=18)
        if licensed.get("email"):
            tk.Label(wrap, text=f"This copy is licensed to {licensed.get('name', '')}",
                     bg=BG, fg=INK, font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x")
            tk.Label(wrap, text=licensed["email"], bg=BG, fg=SOFT,
                     font=("Segoe UI", 10), anchor="w").pack(fill="x", pady=(2, 12))
        card = tk.Frame(wrap, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="both", expand=True)

        v = {k: tk.StringVar(value=licensed.get(k, "")) for k in ("name", "email")}
        v["pw1"] = tk.StringVar()
        v["pw2"] = tk.StringVar()
        out: dict = {}

        entries: dict = {}

        def field(key, label, var, hint="", secret=False):
            tk.Label(card, text=label, bg=CARD, fg=INK, font=F_LABEL,
                     anchor="w").pack(fill="x", padx=18, pady=(16, 6))
            box = tk.Frame(card, bg="white", highlightthickness=1,
                           highlightbackground=LINE, highlightcolor=LINE)
            box.pack(fill="x", padx=18)
            e = tk.Entry(box, textvariable=var, font=F_BODY, relief="flat",
                         borderwidth=0, bg="white", fg=INK, show="•" if secret else "",
                         highlightthickness=0, insertbackground=INK)
            e.pack(side="left", fill="x", expand=True, padx=(12, 0), pady=10)
            entries[key] = e
            if secret:
                # Typing a 12-character password blind, twice, with no way to check
                # it is how people end up locked out of a copy nobody can reset.
                def toggle(_ev=None, _e=e):
                    shown = _e.cget("show") == ""
                    _e.config(show="•" if shown else "")
                    eye.config(text="show" if shown else "hide")
                eye = tk.Label(box, text="show", bg="white", fg=acc, font=F_HINT,
                               cursor="hand2", padx=12)
                eye.pack(side="right")
                eye.bind("<Button-1>", toggle)
            e.bind("<FocusIn>",
                   lambda ev: box.config(highlightbackground=acc, highlightcolor=acc))
            e.bind("<FocusOut>",
                   lambda ev: box.config(highlightbackground=LINE, highlightcolor=LINE))
            if hint:
                tk.Label(card, text=hint, bg=CARD, fg=SOFT, font=F_HINT,
                         anchor="w").pack(fill="x", padx=18, pady=(3, 0))

        if not licensed.get("email"):
            field("name", "Your name", v["name"])
            field("email", "Email address", v["email"],
                  "You will use this as your username.")
        field("pw1", "Password", v["pw1"], secret=True)

        # A live checklist rather than one error at a time. The old screen accepted
        # the form, then rejected it on submit for a rule it had never shown --
        # so people met "too repetitive" only after typing it twice.
        rules = tk.Frame(card, bg=CARD)
        rules.pack(fill="x", padx=18, pady=(8, 0))
        checks = [
            ("At least 12 characters", lambda p, c: len(p) >= 12),
            ("A mix of characters, not one repeated", lambda p, c: len(set(p)) >= 5),
            ("Both entries match", lambda p, c: bool(p) and p == c),
        ]
        marks = []
        for text, _ in checks:
            row = tk.Label(rules, text=f"○  {text}", bg=CARD, fg=SOFT,
                           font=F_HINT, anchor="w")
            row.pack(fill="x", pady=1)
            marks.append(row)

        field("pw2", "Confirm password", v["pw2"], secret=True)
        err = tk.Label(card, text="", bg=CARD, fg="#dc2626", font=F_BODY,
                       anchor="w", wraplength=420, justify="left")
        err.pack(fill="x", padx=18, pady=(10, 0))

        def review(*_a):
            pw, cf = v["pw1"].get(), v["pw2"].get()
            for row, (text, test) in zip(marks, checks):
                good = test(pw, cf)
                row.config(text=f"{'●' if good else '○'}  {text}",
                           fg=OK_ if good else SOFT)
        v["pw1"].trace_add("write", review)
        v["pw2"].trace_add("write", review)

        def finish():
            if not licensed.get("email"):
                if len(v["name"].get().strip()) < 2:
                    err.config(text="Enter your name."); return
                mail = v["email"].get().strip()
                if "@" not in mail or "." not in mail.split("@")[-1]:
                    err.config(text="Enter a valid email address."); return
            pw = v["pw1"].get()
            if len(pw) < 12:
                err.config(text="Use a password of at least 12 characters."); return
            if len(set(pw)) < 5:
                err.config(text="That password is too repetitive."); return
            if pw != v["pw2"].get():
                err.config(text="The two passwords do not match."); return
            out.update({
                "name": (licensed.get("name") or v["name"].get()).strip(),
                "email": (licensed.get("email") or v["email"].get()).strip().lower(),
                "password": pw,
                "role": "user" if licensed.get("email") else "admin",
            })
            root.destroy()

        foot = tk.Frame(root, bg=BG)
        foot.pack(fill="x", pady=(0, 14))
        Btn(foot, "Create my account", finish, "primary").pack(side="right", padx=24)

        root.bind("<Return>", lambda e: finish())
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.update_idletasks()
        root.geometry(f"+{(root.winfo_screenwidth() - root.winfo_width()) // 2}"
                      f"+{max(0, (root.winfo_screenheight() - root.winfo_height()) // 3)}")
        root.mainloop()
        return out or None
    except Exception:
        return None
