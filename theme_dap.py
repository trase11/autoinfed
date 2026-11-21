"""Θέμα εικόνων στο ύφος της καμπάνιας ΔΑΠ-ΝΔΦΚ «Άλμα στο μέλλον».

Μπλε διαγώνιο gradient, λεπτές καμπύλες «στίβου», λευκό περιστέρι,
ψηλά στενά κεφαλαία γράμματα και γαλάζιο (#3DCBFF) για τις λεπτομέρειες.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
F = ROOT / "fonts"
BLACK = str(F / "FiraSansExtraCondensed-Black.ttf")
BOLD = str(F / "FiraSansExtraCondensed-Bold.ttf")
REG = str(F / "FiraSansCondensed-Regular.ttf")
MED = str(F / "FiraSansCondensed-Medium.ttf")
DOVE = ROOT / "assets" / "dove_white.png"
LOGO = ROOT / "assets" / "alma_logo_light.png"

NAVY = (0, 32, 96)
MID = (0, 88, 160)
LIGHT = (8, 152, 210)
CYAN = (61, 203, 255)
PALE = (205, 232, 250)
WHITE = (255, 255, 255)

W = 1080
PAD = 84


def _upper_gr(s: str) -> str:
    """Κεφαλαία χωρίς τόνους, όπως γράφονται σωστά στα ελληνικά."""
    s = unicodedata.normalize("NFD", s.upper())
    s = s.replace("́", "").replace("͂", "")
    return unicodedata.normalize("NFC", s)


def _wrap(d, text, font, maxw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=font) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _background(h: int) -> Image.Image:
    y, x = np.mgrid[0:h, 0:W].astype(float)
    xn, yn = x / W, y / h
    # σκούρο κατά μήκος της διαγωνίου πάνω-δεξιά → κάτω-αριστερά,
    # φωτεινό πάνω-αριστερά και κάτω-δεξιά (όπως στα visuals της καμπάνιας)
    t = np.clip(np.abs(xn + yn - 1.0) * 1.15, 0, 1)
    t = t + 0.18 * np.clip(1 - (xn + yn), 0, 1)       # λίγο πιο φωτεινή η πάνω-αριστερή γωνία
    t = np.clip(t, 0, 1)[..., None]
    a, b, c = (np.array(v, float) for v in (NAVY, MID, LIGHT))
    rgb = np.where(t < 0.5, a + (b - a) * (t / 0.5), b + (c - b) * ((t - 0.5) / 0.5))
    return Image.fromarray(rgb.astype(np.uint8), "RGB")


def _tracks(img: Image.Image, h: int) -> None:
    """Οι λεπτές καμπύλες «στίβου»: τόξα που συνεχίζουν κάθετα προς τα κάτω."""
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    cx, cy = int(W * 0.12), int(h * 0.58)
    for i, r in enumerate(range(430, 1000, 95)):
        col = (255, 255, 255, 30 - i * 2)
        d.arc([cx - r, cy - r, cx + r, cy + r], start=200, end=360, fill=col, width=3)
        d.line([cx + r, cy, cx + r, h], fill=col, width=3)
    img.paste(over, (0, 0), over)


def _paste(img, path: Path, box_w: int, xy, anchor="lt"):
    im = Image.open(path).convert("RGBA")
    ratio = box_w / im.width
    im = im.resize((box_w, int(im.height * ratio)), Image.LANCZOS)
    x, y = xy
    if anchor == "rb":
        x, y = x - im.width, y - im.height
    img.paste(im, (int(x), int(y)), im)
    return im.size


def make_image(src: dict, title: str, date: datetime, excerpt: str, category: str, out: Path,
               story: bool = False, footer: tuple[str, str] | None = None) -> Path:
    """footer=(μικρό κείμενο, μεγάλο γαλάζιο κείμενο) αντί για «Ολόκληρη η ανακοίνωση / site»."""
    H = 1920 if story else 1350
    top = 220 if story else 0           # στο story το Instagram καλύπτει πάνω/κάτω
    bottom = 300 if story else 0
    img = _background(H)
    _tracks(img, H)
    d = ImageDraw.Draw(img)
    maxw = W - 2 * PAD

    # --- Κεφαλίδα: περιστέρι + ΔΑΠ-ΝΔΦΚ + τμήμα
    y0 = top + 70
    dw, dh = _paste(img, DOVE, 118, (PAD, y0))
    hx = PAD + dw + 26
    d.text((hx, y0 - 6), "ΔΑΠ-ΝΔΦΚ", font=ImageFont.truetype(BLACK, 50), fill=WHITE)
    nf = ImageFont.truetype(MED, 32)
    name = src.get("name", "")
    while d.textlength(name, font=nf) > W - PAD - hx and nf.size > 22:
        nf = ImageFont.truetype(MED, nf.size - 1)
    d.text((hx, y0 + 52), name, font=nf, fill=PALE)
    y = y0 + max(dh, 100) + 70

    # --- Ετικέτα «ΑΝΑΚΟΙΝΩΣΗ» / κατηγορία + ημερομηνία
    chip = _upper_gr(category) if category else "ΑΝΑΚΟΙΝΩΣΗ"
    cf = ImageFont.truetype(BOLD, 32)
    cw = d.textlength(chip, font=cf)
    d.rounded_rectangle([PAD, y, PAD + cw + 44, y + 54], radius=27, fill=CYAN)
    d.text((PAD + 22, y + 9), chip, font=cf, fill=NAVY)
    d.text((PAD + cw + 70, y + 9), date.strftime("%d.%m.%Y"), font=ImageFont.truetype(MED, 32), fill=PALE)
    y += 104

    # --- Τίτλος: κεφαλαία, ψηλά στενά γράμματα, μικραίνει μέχρι να χωρέσει
    footer_y = H - bottom - (190 if story else 170)
    title_u = _upper_gr(title)
    max_lines = 8 if story else 6
    if excerpt:
        size = 118 if story else 108
    else:                                # σκέτος τίτλος (π.χ. εξώφυλλο καρουσέλ): πιο μεγάλος
        size = 168 if story else 150
    while True:
        tf = ImageFont.truetype(BLACK, size)
        lines = _wrap(d, title_u, tf, maxw)
        lh = int(size * 1.02)
        if (len(lines) <= max_lines and y + lh * len(lines) < footer_y - 120) or size <= 52:
            break
        size -= 4
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" ,.;:") + "…"
    for ln in lines:
        d.text((PAD, y), ln, font=tf, fill=WHITE)
        y += lh

    # --- Γαλάζια γραμμή + περίληψη στον χώρο που περισσεύει
    y += 26
    d.rounded_rectangle([PAD, y, PAD + 120, y + 8], radius=4, fill=CYAN)
    y += 40
    ef = ImageFont.truetype(REG, 38 if story else 35)
    eh = int(ef.size * 1.38)
    room = (footer_y - 30 - y) // eh
    if excerpt and room >= 1:
        elines = _wrap(d, excerpt, ef, maxw)
        if len(elines) > room:
            elines = elines[:room]
            elines[-1] = elines[-1].rstrip(" ,.;…") + "…"
        for ln in elines:
            d.text((PAD, y), ln, font=ef, fill=PALE)
            y += eh

    # --- Υποσέλιδο: πού είναι ολόκληρη η ανακοίνωση + λογότυπο καμπάνιας
    domain = re.sub(r"^https?://(www\.)?([^/?]+).*$", r"\2",
                    src.get("site") or src.get("url") or src.get("feed", ""))
    logo_w = 104 if story else 92
    lw, lh_ = _paste(img, LOGO, logo_w, (W - PAD, H - bottom - 60), anchor="rb")
    fy = H - bottom - 60 - 70
    small, big = footer if footer is not None else ("Ολόκληρη η ανακοίνωση", domain)
    d.text((PAD, fy), small, font=ImageFont.truetype(REG, 30), fill=PALE)
    d.text((PAD, fy + 38), big, font=ImageFont.truetype(BOLD, 40), fill=CYAN)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=92)
    return out


# --------------------------------------------------------------------------- #
# Σελίδες καρουσέλ από φωτογραφίες
# --------------------------------------------------------------------------- #
def photo_slide(photo: Path, out: Path, story: bool = False, fill: bool = False) -> Path:
    """fill=False: ολόκληρη η φωτογραφία πάνω στο φόντο της καμπάνιας (δεν κόβεται τίποτα,
    π.χ. σκαναρισμένη απόφαση). fill=True: γεμίζει όλο το κάδρο (για δικό σου εξώφυλλο)."""
    H = 1920 if story else 1350
    ph = Image.open(photo)
    try:
        from PIL import ImageOps
        ph = ImageOps.exif_transpose(ph)
    except Exception:
        pass
    ph = ph.convert("RGB")
    if fill:
        r = max(W / ph.width, H / ph.height)
        ph = ph.resize((int(ph.width * r + 1), int(ph.height * r + 1)), Image.LANCZOS)
        x, y = (ph.width - W) // 2, (ph.height - H) // 2
        img = ph.crop((x, y, x + W, y + H))
    else:
        img = _background(H)
        _tracks(img, H)
        m = 56
        top = 220 if story else m           # περιθώρια για το UI του Instagram στο story
        bottom = 300 if story else m
        bw, bh = W - 2 * m, H - top - bottom
        r = min(bw / ph.width, bh / ph.height)
        ph = ph.resize((max(1, int(ph.width * r)), max(1, int(ph.height * r))), Image.LANCZOS)
        x, y = (W - ph.width) // 2, top + (bh - ph.height) // 2
        # απαλή σκιά + στρογγυλεμένες γωνίες
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle([x + 6, y + 10, x + ph.width + 6, y + ph.height + 10],
                                                 radius=26, fill=(0, 10, 40, 110))
        from PIL import ImageFilter
        shadow = shadow.filter(ImageFilter.GaussianBlur(14))
        img.paste(shadow, (0, 0), shadow)
        mask = Image.new("L", ph.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, ph.width - 1, ph.height - 1], radius=22, fill=255)
        img.paste(ph, (x, y), mask)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=92)
    return out
