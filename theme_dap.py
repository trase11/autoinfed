"""Θέμα εικόνων: μπλε διαγώνιο gradient με λεπτές καμπύλες «στίβου», ψηλά στενά
κεφαλαία γράμματα και γαλάζιο (#3DCBFF) για τις λεπτομέρειες. Πάνω το όνομα του
τμήματος και, αν υπάρχει, το λογότυπό του (assets/logo_<id>.png).
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


PAPER = (246, 248, 252)
MUTED = (96, 112, 140)
LINE = (214, 222, 236)
STYLES = ("blue", "white", "photo")


def _canvas(h: int, style: str) -> Image.Image:
    """blue = μπλε καμπάνια με γραμμές · white = λευκό με μπλε (για αντίθεση)."""
    if style == "white":
        img = Image.new("RGB", (W, h), PAPER)
        ImageDraw.Draw(img).rectangle([0, 0, W, 10], fill=MID)
        return img
    img = _background(h)
    _tracks(img, h)
    return img


def _navy(im: Image.Image) -> Image.Image:
    solid = Image.new("RGBA", im.size, NAVY + (255,))
    solid.putalpha(im.getchannel("A"))
    return solid


def _paste(img, path: Path, box_w: int, xy, anchor="lt", navy: bool = False):
    im = Image.open(path).convert("RGBA")
    if navy:
        im = _navy(im)
    ratio = box_w / im.width
    im = im.resize((box_w, int(im.height * ratio)), Image.LANCZOS)
    x, y = xy
    if anchor == "rb":
        x, y = x - im.width, y - im.height
    img.paste(im, (int(x), int(y)), im)
    return im.size


def make_image(src: dict, title: str, date: datetime, excerpt: str, category: str, out: Path,
               story: bool = False, footer: tuple[str, str] | None = None,
               excerpt_size: int | None = None, style: str = "blue",
               signature: str = "") -> Path:
    """footer=(μικρό κείμενο, μεγάλο γαλάζιο κείμενο) αντί για «Ολόκληρη η ανακοίνωση / site».
    style: "blue" (καμπάνια) ή "white" (λευκό φόντο, μπλε γράμματα)."""
    H = 1920 if story else 1350
    top = 220 if story else 0           # στο story το Instagram καλύπτει πάνω/κάτω
    bottom = 300 if story else 0
    wh = style == "white"
    TXT, SUB, ACC = (NAVY, MUTED, MID) if wh else (WHITE, PALE, CYAN)
    img = _canvas(H, "white" if wh else "blue")
    d = ImageDraw.Draw(img)
    maxw = W - 2 * PAD

    # --- Κεφαλίδα: λογότυπο τμήματος (αν υπάρχει) + «ΤΜΗΜΑ …» + «ΠΑΝΕΠΙΣΤΗΜΙΟ ΘΕΣΣΑΛΙΑΣ»
    y0 = top + 70
    logo = ROOT / "assets" / f"logo_{src.get('id', '')}.png"
    dw, dh = _paste(img, logo, 118, (PAD, y0), navy=wh) if logo.exists() else (0, 0)
    hx = PAD + (dw + 28 if dw else 0)
    dept = "ΤΜΗΜΑ " + _upper_gr(re.sub(r"\s*ΠΘ$", "", src.get("name", "")))
    tf_ = ImageFont.truetype(BLACK, 52)
    while d.textlength(dept, font=tf_) > W - PAD - hx and tf_.size > 30:
        tf_ = ImageFont.truetype(BLACK, tf_.size - 2)
    head_h = tf_.size + 44
    ty = y0 + max(0, (dh - head_h) // 2)                     # κάθετα στο κέντρο του λογότυπου
    d.text((hx, ty - 4), dept, font=tf_, fill=TXT)
    d.text((hx, ty + tf_.size + 6), "ΠΑΝΕΠΙΣΤΗΜΙΟ ΘΕΣΣΑΛΙΑΣ", font=ImageFont.truetype(MED, 30), fill=SUB)
    y = y0 + max(dh, head_h) + 70

    # --- Ετικέτα «ΑΝΑΚΟΙΝΩΣΗ» / κατηγορία + ημερομηνία
    chip = _upper_gr(category) if category else "ΑΝΑΚΟΙΝΩΣΗ"
    cf = ImageFont.truetype(BOLD, 32)
    cw = d.textlength(chip, font=cf)
    d.rounded_rectangle([PAD, y, PAD + cw + 44, y + 54], radius=27, fill=MID if wh else CYAN)
    d.text((PAD + 22, y + 9), chip, font=cf, fill=WHITE if wh else NAVY)
    d.text((PAD + cw + 70, y + 9), date.strftime("%d.%m.%Y"), font=ImageFont.truetype(MED, 32), fill=SUB)
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
        d.text((PAD, y), ln, font=tf, fill=TXT)
        y += lh

    # --- Γαλάζια γραμμή + περίληψη στον χώρο που περισσεύει
    y += 26
    d.rounded_rectangle([PAD, y, PAD + 120, y + 8], radius=4, fill=ACC)
    y += 40
    ef = ImageFont.truetype(REG, excerpt_size or (38 if story else 35))
    eh = int(ef.size * 1.38)
    room = (footer_y - 30 - y) // eh
    if signature:                       # μια γραμμή κρατιέται για την υπογραφή
        room -= 1
    if excerpt and room >= 1:
        elines = _wrap(d, excerpt, ef, maxw)
        if len(elines) > room:
            elines = elines[:room]
            elines[-1] = elines[-1].rstrip(" ,.;…") + "…"
        for ln in elines:
            d.text((PAD, y), ln, font=ef, fill=SUB)
            y += eh
    if signature:                       # υπογραφή κάτω από το κείμενο, ίδια γραμματοσειρά
        sf = ImageFont.truetype(BOLD, ef.size)
        d.text((PAD, y + int(eh * 0.25)), signature, font=sf, fill=TXT)

    # --- Υποσέλιδο: πού είναι ολόκληρη η ανακοίνωση + λογότυπο καμπάνιας
    domain = re.sub(r"^https?://(www\.)?([^/?]+).*$", r"\2",
                    src.get("site") or src.get("url") or src.get("feed", ""))
    fy = H - bottom - 60 - 70
    small, big = footer if footer is not None else ("Ολόκληρη η ανακοίνωση", domain)
    if wh:
        d.line([(PAD, fy - 26), (W - PAD, fy - 26)], fill=LINE, width=2)
    d.text((PAD, fy), small, font=ImageFont.truetype(REG, 30), fill=SUB)
    d.text((PAD, fy + 38), big, font=ImageFont.truetype(BOLD, 40), fill=ACC)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=92)
    return out


# --------------------------------------------------------------------------- #
# Σελίδες καρουσέλ από φωτογραφίες
# --------------------------------------------------------------------------- #
def photo_slide(photo: Path, out: Path, story: bool = False, fill: bool = False,
                style: str = "blue") -> Path:
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
        img = _canvas(H, "blue" if style == "blue" else "white")
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
                                                 radius=26, fill=(0, 10, 40, 110 if style == "blue" else 45))
        from PIL import ImageFilter
        shadow = shadow.filter(ImageFilter.GaussianBlur(14))
        img.paste(shadow, (0, 0), shadow)
        mask = Image.new("L", ph.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, ph.width - 1, ph.height - 1], radius=22, fill=255)
        img.paste(ph, (x, y), mask)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=92)
    return out


# --------------------------------------------------------------------------- #
# Εξώφυλλο «φωτογραφία συλλόγου»: η φωτογραφία σβήνει σε λευκό, τίτλος σε μπλε
# --------------------------------------------------------------------------- #
def photo_cover(src: dict, photo: Path, title: str, out: Path, story: bool = False,
                subtitle: str = "", arrow: bool = False, text: bool = True) -> Path:
    """text=False: σκέτη φωτογραφία με το σβήσιμο + λογότυπο (το κείμενο μπαίνει στο Canva)."""
    from PIL import ImageOps
    H = 1920 if story else 1350
    bottom = 300 if story else 0
    ph = ImageOps.exif_transpose(Image.open(photo)).convert("RGB")
    ph_h = int(H * (0.62 if not story else 0.60))
    r = max(W / ph.width, ph_h / ph.height)
    ph = ph.resize((int(ph.width * r + 1), int(ph.height * r + 1)), Image.LANCZOS)
    x = (ph.width - W) // 2
    y = max(0, int((ph.height - ph_h) * 0.35))        # λίγο πιο ψηλά: κρατά τα πρόσωπα
    ph = ph.crop((x, y, x + W, y + ph_h))
    img = Image.new("RGB", (W, H), (255, 255, 255))
    img.paste(ph, (0, 0))
    # σβήσιμο προς τα λευκά στο κάτω 40% της φωτογραφίας
    fade = int(ph_h * 0.42)
    g = np.linspace(0, 1, fade) ** 1.4
    alpha = np.zeros((ph_h, W), np.float32)
    alpha[ph_h - fade:] = g[:, None]
    white = Image.new("RGB", (W, ph_h), (255, 255, 255))
    mask = Image.fromarray((alpha * 255).astype(np.uint8), "L")
    img.paste(white, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # Δύο μορφές:
    #  • με πρόταση (subtitle): τίτλος σε μπλε + μία πρόταση από κάτω
    #  • χωρίς: μόνο ο τίτλος, μέσα σε μπλε κουτί με άσπρα γράμματα
    maxw = W - 2 * 90
    t_top = ph_h - int(fade * 0.30)
    logo_h = 96
    t_bot = H - bottom - logo_h - (80 if not story else 60)
    tu = _upper_gr(title)
    if not text:
        pass
    elif subtitle:
        size = 84
        sf = ImageFont.truetype(MED, 38)
        while True:
            tf = ImageFont.truetype(BLACK, size)
            lines = _wrap(d, tu, tf, maxw)
            slines = _wrap(d, subtitle, sf, maxw)[:3]
            block = len(lines) * int(size * 1.02) + 26 + len(slines) * 50
            if (len(lines) <= 3 and block <= t_bot - t_top) or size <= 48:
                break
            size -= 4
        y = t_top + (t_bot - t_top - block) // 2
        for ln in lines:
            w = d.textlength(ln, font=tf)
            d.text(((W - w) / 2, y), ln, font=tf, fill=MID)
            y += int(size * 1.02)
        y += 26
        for ln in slines:
            w = d.textlength(ln, font=sf)
            d.text(((W - w) / 2, y), ln, font=sf, fill=NAVY)
            y += 50
    else:
        size = 66
        while True:
            tf = ImageFont.truetype(BLACK, size)
            lines = _wrap(d, tu, tf, maxw - 60)
            if len(lines) <= 3 or size <= 40:
                break
            size -= 4
        lh = int(size * 1.08)
        bw = max(d.textlength(l, font=tf) for l in lines) + 70
        bh = len(lines) * lh + 44
        y = t_top + (t_bot - t_top - bh) // 2
        bx = (W - bw) / 2
        d.rectangle([bx, y, bx + bw, y + bh], fill=MID)
        yy = y + 22
        for ln in lines:
            w = d.textlength(ln, font=tf)
            d.text(((W - w) / 2, yy - size * 0.06), ln, font=tf, fill=WHITE)
            yy += lh

    # λογότυπο + τμήμα, στο κέντρο κάτω
    ly = H - bottom - logo_h - (40 if not story else 30)
    logo = ROOT / "assets" / f"logo_{src.get('id', '')}.png"
    dept = "ΤΜΗΜΑ " + _upper_gr(re.sub(r"\s*ΠΘ$", "", src.get("name", "")))
    f1, f2 = ImageFont.truetype(BLACK, 38), ImageFont.truetype(MED, 24)
    tw = max(d.textlength(dept, font=f1), d.textlength("ΠΑΝΕΠΙΣΤΗΜΙΟ ΘΕΣΣΑΛΙΑΣ", font=f2))
    lw = 0
    if logo.exists():
        lg = _navy(Image.open(logo).convert("RGBA"))
        lg = lg.resize((84, int(lg.height * 84 / lg.width)), Image.LANCZOS)
        lw = lg.width + 18
    x0 = (W - (lw + tw)) / 2
    if lw:
        img.paste(lg, (int(x0), int(ly + (logo_h - lg.height) / 2)), lg)
    d.text((x0 + lw, ly + 14), dept, font=f1, fill=NAVY)
    d.text((x0 + lw, ly + 58), "ΠΑΝΕΠΙΣΤΗΜΙΟ ΘΕΣΣΑΛΙΑΣ", font=f2, fill=MUTED)
    if arrow and not story:
        cx, cy, rr = W - 80, ly + logo_h / 2, 34
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=MID)
        d.line([(cx - 14, cy), (cx + 12, cy)], fill=WHITE, width=6)
        d.line([(cx + 2, cy - 12), (cx + 14, cy), (cx + 2, cy + 12)], fill=WHITE, width=6, joint="curve")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=92)
    return out
