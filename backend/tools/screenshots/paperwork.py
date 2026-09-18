"""Generate the images the demo instance gets photographed with.

The previous set was the problem the user is pointing at. The gallery was
filled with soft landscape gradients -- a sky, a sun, two hills -- which at
thumbnail size is a coloured rectangle, not a photograph, and which also
contradicts the copy beside it. The documents were pages of grey BARS standing
in for text, so the documents screen photographed as a loading skeleton.

So: real text, real structure, real fonts. Every image here is one of the
things a household actually keeps -- a receipt, a utility bill, a warranty
card, a policy schedule -- either photographed on a surface or scanned flat.
Nothing is a real company, a real person, or a real account number.
"""
import io
import math
import random
from PIL import Image, ImageDraw, ImageFilter, ImageFont

F = r"C:\Windows\Fonts"
def font(name, size):
    return ImageFont.truetype(f"{F}\\{name}", size)

REG, BOLD, MONO, MONOB = "arial.ttf", "arialbd.ttf", "consola.ttf", "consolab.ttf"

INK = (38, 44, 58)
GREY = (108, 118, 138)
FAINT = (168, 176, 192)
RULE = (214, 220, 232)


def _w(d, txt, f):
    return d.textbbox((0, 0), txt, font=f)[2]


# ------------------------------------------------------------------ surfaces
def surface(w, h, kind):
    """The thing the paper is lying on. Subtle, because it is only context."""
    if kind == "wood":
        base, streak = (176, 141, 104), (150, 116, 82)
    elif kind == "desk":
        base, streak = (216, 214, 208), (198, 196, 190)
    else:  # cloth
        base, streak = (128, 142, 160), (110, 124, 142)
    im = Image.new("RGB", (w, h), base)
    d = ImageDraw.Draw(im)
    rnd = random.Random(kind)
    for i in range(160):
        y = rnd.randint(0, h)
        d.line([(0, y), (w, y + rnd.randint(-14, 14))],
               fill=tuple(max(0, min(255, streak[c] + rnd.randint(-14, 14))) for c in range(3)),
               width=rnd.randint(1, 5))
    im = im.filter(ImageFilter.GaussianBlur(7))
    # a soft light from the top-left, so the paper has something to sit in
    grad = Image.new("L", (w, h), 0)
    gd = ImageDraw.Draw(grad)
    for r in range(28, 0, -1):
        gd.ellipse([-w * .3 - r * 26, -h * .5 - r * 26, w * 1.1 + r * 10, h * 1.5 + r * 10],
                   fill=int(120 - r * 4))
    im = Image.composite(Image.new("RGB", (w, h), (255, 255, 255)), im,
                         grad.filter(ImageFilter.GaussianBlur(60)).point(lambda v: v // 4))
    return im


def place(bg, paper, angle, centre, shadow=34):
    """Drop a sheet onto a surface at an angle, with a soft shadow."""
    p = paper.convert("RGBA").rotate(angle, expand=True, resample=Image.BICUBIC)
    sh = Image.new("RGBA", p.size, (0, 0, 0, 0))
    sh.paste((18, 22, 32, 130), (0, 0), p.split()[3])
    sh = sh.filter(ImageFilter.GaussianBlur(shadow))
    x = int(centre[0] - p.width / 2)
    y = int(centre[1] - p.height / 2)
    bg.paste(sh, (x + 6, y + 14), sh)
    bg.paste(p, (x, y), p)
    return bg


# ------------------------------------------------------------------ documents
def receipt(store, city, items, pay, when):
    """A till receipt: narrow, monospaced, with a torn look at the bottom."""
    w, h = 520, 760
    im = Image.new("RGBA", (w, h), (253, 252, 249, 255))
    d = ImageDraw.Draw(im)
    f_name, f_s, f_m, f_b = font(BOLD, 34), font(REG, 17), font(MONO, 19), font(MONOB, 22)

    d.text(((w - _w(d, store, f_name)) / 2, 44), store, font=f_name, fill=INK)
    d.text(((w - _w(d, city, f_s)) / 2, 88), city, font=f_s, fill=GREY)
    d.text(((w - _w(d, "- " * 26, f_s)) / 2, 120), "- " * 26, font=f_s, fill=RULE)

    y = 162
    total = 0
    for name, qty, price in items:
        total += price
        d.text((40, y), name[:22], font=f_m, fill=INK)
        d.text((40, y + 24), f"  {qty}", font=f_m, fill=FAINT)
        amt = f"{price:,.2f}"
        d.text((w - 40 - _w(d, amt, f_m), y), amt, font=f_m, fill=INK)
        y += 56

    d.line([(40, y + 6), (w - 40, y + 6)], fill=RULE, width=2)
    y += 26
    d.text((40, y), "TOTAL", font=f_b, fill=INK)
    tot = f"{total:,.2f}"
    d.text((w - 40 - _w(d, tot, f_b), y), tot, font=f_b, fill=INK)
    y += 44
    d.text((40, y), pay, font=f_m, fill=GREY)
    y += 30
    d.text((40, y), when, font=f_m, fill=GREY)
    y += 46
    d.text(((w - _w(d, "THANK YOU", f_s)) / 2, y), "THANK YOU", font=f_s, fill=FAINT)

    # a torn lower edge, which is what makes it read as a till slip
    cut = min(h - 12, y + 84)
    d.rectangle([0, cut, w, h], fill=(0, 0, 0, 0))
    zig = ImageDraw.Draw(im)
    for x in range(0, w, 16):
        zig.polygon([(x, cut), (x + 8, cut - 9), (x + 16, cut)], fill=(0, 0, 0, 0))
    return im


def bill(company, kind, account, period, amount, due, rows):
    """A utility bill: coloured masthead, a big amount, a small table."""
    w, h = 760, 1040
    im = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    accent = (31, 96, 180)
    d.rectangle([0, 0, w, 120], fill=accent)
    d.text((46, 34), company, font=font(BOLD, 34), fill=(255, 255, 255))
    d.text((46, 76), kind, font=font(REG, 19), fill=(206, 224, 248))

    d.text((46, 158), "TAX INVOICE", font=font(BOLD, 15), fill=GREY)
    for i, (k, v) in enumerate([("Account", account), ("Billing period", period)]):
        d.text((46, 192 + i * 46), k, font=font(REG, 16), fill=FAINT)
        d.text((46, 213 + i * 46), v, font=font(BOLD, 18), fill=INK)

    d.rounded_rectangle([440, 176, w - 46, 300], 14, fill=(240, 246, 254))
    d.text((466, 198), "Amount due", font=font(REG, 16), fill=GREY)
    d.text((466, 220), amount, font=font(BOLD, 40), fill=accent)
    d.text((466, 268), f"Due {due}", font=font(REG, 16), fill=GREY)

    y = 348
    d.line([(46, y), (w - 46, y)], fill=RULE, width=2)
    y += 20
    d.text((46, y), "DESCRIPTION", font=font(BOLD, 14), fill=FAINT)
    d.text((w - 160, y), "AMOUNT", font=font(BOLD, 14), fill=FAINT)
    y += 34
    for label, val in rows:
        d.text((46, y), label, font=font(REG, 18), fill=INK)
        d.text((w - 46 - _w(d, val, font(REG, 18)), y), val, font=font(REG, 18), fill=INK)
        y += 40
    d.line([(46, y + 4), (w - 46, y + 4)], fill=RULE, width=2)
    y += 26
    d.text((46, y), "Total", font=font(BOLD, 20), fill=INK)
    d.text((w - 46 - _w(d, amount, font(BOLD, 20)), y), amount, font=font(BOLD, 20), fill=INK)

    d.text((46, h - 96), "This is a computer generated invoice.", font=font(REG, 15), fill=FAINT)
    d.rectangle([46, h - 58, 300, h - 50], fill=(228, 233, 242))
    return im


def warranty(product, model, serial, bought, valid):
    """A warranty card: landscape, boxed fields."""
    w, h = 820, 520
    im = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    accent = (22, 120, 96)
    d.rectangle([0, 0, w, 14], fill=accent)
    d.text((48, 54), "WARRANTY CARD", font=font(BOLD, 30), fill=INK)
    d.text((48, 96), product, font=font(REG, 21), fill=GREY)

    fields = [("Model", model), ("Serial no.", serial),
              ("Date of purchase", bought), ("Valid until", valid)]
    y = 158
    for k, v in fields:
        d.text((48, y), k.upper(), font=font(BOLD, 13), fill=FAINT)
        d.text((48, y + 22), v, font=font(REG, 20), fill=INK)
        d.line([(48, y + 56), (w - 300, y + 56)], fill=RULE, width=2)
        y += 80

    d.rounded_rectangle([w - 250, 158, w - 48, 360], 12, outline=RULE, width=2)
    d.text((w - 226, 186), "DEALER STAMP", font=font(BOLD, 13), fill=FAINT)
    d.ellipse([w - 214, 216, w - 100, 330], outline=(190, 200, 216), width=3)
    return im


def policy(insurer, kind, number, holder, cover, premium, renews):
    """A policy schedule page."""
    w, h = 760, 1040
    im = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    accent = (86, 62, 160)
    d.text((48, 52), insurer, font=font(BOLD, 32), fill=accent)
    d.text((48, 96), kind, font=font(REG, 20), fill=GREY)
    d.line([(48, 136), (w - 48, 136)], fill=RULE, width=3)

    d.text((48, 168), "POLICY SCHEDULE", font=font(BOLD, 15), fill=FAINT)
    rows = [("Policy number", number), ("Policy holder", holder),
            ("Sum assured", cover), ("Premium", premium), ("Renews on", renews)]
    y = 214
    for k, v in rows:
        d.text((48, y), k, font=font(REG, 17), fill=FAINT)
        d.text((330, y), v, font=font(BOLD, 19), fill=INK)
        y += 56
    d.line([(48, y + 10), (w - 48, y + 10)], fill=RULE, width=2)

    y += 48
    d.text((48, y), "The cover described above is subject to the terms, conditions",
           font=font(REG, 16), fill=GREY)
    d.text((48, y + 26), "and exclusions of the policy document.", font=font(REG, 16), fill=GREY)
    y += 96
    for n in (600, 540, 580, 430, 520, 470):
        d.rectangle([48, y, 48 + n, y + 9], fill=(232, 236, 244))
        y += 26
    d.text((48, h - 130), "Authorised signatory", font=font(REG, 15), fill=FAINT)
    d.line([(48, h - 96), (280, h - 96)], fill=RULE, width=2)
    return im


def statement(title, subtitle, rows, total_label, total):
    """A plain account statement, for the flat-scan look."""
    w, h = 760, 1040
    im = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    d.text((48, 54), title, font=font(BOLD, 30), fill=INK)
    d.text((48, 98), subtitle, font=font(REG, 18), fill=GREY)
    d.line([(48, 140), (w - 48, 140)], fill=RULE, width=2)
    y = 176
    for a, b in rows:
        d.text((48, y), a, font=font(REG, 18), fill=INK)
        d.text((w - 48 - _w(d, b, font(REG, 18)), y), b, font=font(REG, 18), fill=INK)
        d.line([(48, y + 34), (w - 48, y + 34)], fill=(240, 243, 248), width=1)
        y += 52
    y += 12
    d.text((48, y), total_label, font=font(BOLD, 20), fill=INK)
    d.text((w - 48 - _w(d, total, font(BOLD, 20)), y), total, font=font(BOLD, 20), fill=INK)
    return im


# ------------------------------------------------------------------ finishing
def photographed(paper, kind="desk", angle=None, size=(1100, 820)):
    """A phone photo of the sheet lying on something."""
    bg = surface(size[0], size[1], kind)
    scale = min(size[0] * .74 / paper.width, size[1] * .88 / paper.height)
    p = paper.resize((int(paper.width * scale), int(paper.height * scale)), Image.LANCZOS)
    a = angle if angle is not None else random.uniform(-7, 7)
    bg = place(bg, p, a, (size[0] // 2, size[1] // 2))
    return bg.filter(ImageFilter.GaussianBlur(.4))


def scanned(paper, size=(1000, 1360)):
    """A flat scan: straight, cropped to the page, very slightly grey."""
    im = paper.convert("RGB").resize(size, Image.LANCZOS)
    return Image.blend(im, Image.new("RGB", size, (250, 250, 248)), .06)


def jpeg(im, q=88):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q)
    return buf.getvalue()
