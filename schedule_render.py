"""Ωρολόγιο πρόγραμμα ως σελίδες καρουσέλ, στο ύφος του template.

Είσοδος: schedule.json (από το PDF του τμήματος) με, ανά εξάμηνο,
  hours: [[9,10], …], cells: {"<ημέρα 0-4>:<ώρα>": [{name, teacher, room}, …]}
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import theme_dap as t

W, H = 1080, 1350
DAYS = ["ΔΕΥΤΕΡΑ", "ΤΡΙΤΗ", "ΤΕΤΑΡΤΗ", "ΠΕΜΠΤΗ", "ΠΑΡΑΣΚΕΥΗ"]
# Ανοιχτό φόντο: δίνει αντίθεση με το μπλε εξώφυλλο του καρουσέλ
PALETTE = [(0, 122, 204), (0, 150, 120), (110, 84, 220), (214, 140, 0),
           (214, 58, 120), (90, 150, 20), (226, 96, 30), (40, 90, 200), (90, 100, 120)]
PAPER = (246, 248, 252)
INK = t.NAVY
MUTED = (96, 112, 140)
LINE = (214, 222, 236)


def _tint(c, k=0.86):
    return tuple(int(v + (255 - v) * k) for v in c)


def _navy_logo(path: Path) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    solid = Image.new("RGBA", im.size, INK + (255,))
    solid.putalpha(im.getchannel("A"))
    return solid


def short_room(r: str) -> str:
    r = re.sub(r"\s+", " ", r).strip()
    rules = [
        (r"Αμφιθ[εέ]ατρο ΤμΜΧΠΠΑ.*", "Αμφ. Χωροταξίας"),
        (r"Αμφιθ[εέ]ατρο ΤΜΜ", "Αμφ. ΤΜΜ"),
        (r"Αμφιθ[εέ]ατρο Αρχιτεκτονικής", "Αμφ. Αρχιτεκτονικής"),
        (r"Αίθουσα (Α\d) ΤμΜΧΠΠΑ.*", r"\1 Χωροταξίας"),
        (r"(?:Αίθ|Αιθ|Αίθουσα)\.?\s*[Ππ]ροκάτ\s*\(?([ΑAΒB]\d)\)?", r"Προκάτ \1"),
        (r"Αίθουσα Η/Υ", "Αίθ. Η/Υ"),
        (r"Αίθουσα Συνεδριάσεων", "Αίθ. Συνεδριάσεων"),
    ]
    for pat, rep in rules:
        if re.fullmatch(pat, r):
            r = re.sub(pat, rep, r)
            break
    return r.replace("A", "Α").replace("B", "Β")


def nice_name(n: str) -> str:
    n = re.sub(r"\s+", " ", n).strip()
    n = n.replace("ΕΙΣ.ΣΤΙΣ", "ΕΙΣ. ΣΤΙΣ").replace("ΜΗΧ/ΚΕΣ", "ΜΗΧΑΝΟΥΡΓΙΚΕΣ").replace("ΜΗΧ/ΚΗ", "ΜΗΧΑΝΙΚΗ")
    n = n.replace("ΣΤΟΧ. ΠΡΟΤΥΠΑ ΣΤΗΝ ΕΠ. ΕΡΕΥΝΑ", "ΣΤΟΧΑΣΤΙΚΑ ΠΡΟΤΥΠΑ ΣΤΗΝ ΕΠΙΧ. ΕΡΕΥΝΑ")
    n = n.replace("ΒΙΟΜ. ΠΑΡΑΓΩΓΗΣ", "ΒΙΟΜΗΧΑΝΙΚΗΣ ΠΑΡΑΓΩΓΗΣ")
    n = n.replace("ΗΛΕΚΤΡΟΤΕΧΝΙΑ-ΗΛΕΚΤΡΙΚΕΣ ΜΗΧΑΝΕΣ", "ΗΛΕΚΤΡΟΤΕΧΝΙΑ - ΗΛΕΚΤΡΙΚΕΣ ΜΗΧΑΝΕΣ")
    n = re.sub(r"\(Φροντιστήριο\)", "· ΦΡΟΝΤ.", n)
    n = re.sub(r"(?<=\S)-(?=\S)", "- ", n)          # να σπάει η γραμμή στις παύλες
    return t._upper_gr(n)


def _key(e: dict) -> str:
    """Το ίδιο μάθημα με/χωρίς «ΒΙΟΜ.» κ.λπ. → ίδιο χρώμα."""
    return re.sub(r"[^Α-ΩA-Z]", "", nice_name(e["name"]).replace("· ΦΡΟΝΤ.", ""))[:18]


def blocks(sem: dict) -> list[dict]:
    """Ενώνει συνεχόμενες ώρες του ίδιου μαθήματος (ίδια μέρα, ίδια αίθουσα) σε ένα μπλοκ.
    Αν δύο μαθήματα πέφτουν στην ίδια ώρα, παίρνουν από μισή στήλη (lane 0/1)."""
    out = []
    for di in range(5):
        open_ = {}
        for a, b in sem["hours"]:
            cur = sem["cells"].get(f"{di}:{a}", [])
            keys = []
            for lane, e in enumerate(cur):
                k = (_key(e), short_room(e["room"]))
                keys.append(k)
                if k in open_ and open_[k]["end"] == a:
                    open_[k]["end"] = b
                    open_[k]["lanes"] = max(open_[k]["lanes"], len(cur))
                else:
                    blk = {"day": di, "start": a, "end": b, "e": e, "lane": lane, "lanes": len(cur)}
                    out.append(blk)
                    open_[k] = blk
            for k in list(open_):
                if k not in keys:
                    del open_[k]
    # οι λωρίδες: όσα μπλοκ επικαλύπτονται χρονικά στην ίδια μέρα μοιράζονται τη στήλη
    for b in out:
        overl = [x for x in out if x["day"] == b["day"] and x["start"] < b["end"] and b["start"] < x["end"]]
        b["lanes"] = max(len(overl), 1)
        b["lane"] = sorted(overl, key=lambda x: (x["start"], x["e"]["name"])).index(b)
    return out


def _fit(d, text, font_path, max_w, max_h, start, minimum=15, lh=1.08):
    size = start
    while size >= minimum:
        f = ImageFont.truetype(font_path, size)
        lines = t._wrap(d, text, f, max_w)
        if len(lines) * size * lh <= max_h and all(d.textlength(l, font=f) <= max_w for l in lines):
            return f, lines
        size -= 1
    f = ImageFont.truetype(font_path, minimum)
    lines = t._wrap(d, text, f, max_w)
    maxl = max(1, int(max_h // (minimum * lh)))
    if len(lines) > maxl:
        lines = lines[:maxl]
        lines[-1] = lines[-1].rstrip(" ,.") + "…"
    return f, lines


def render_semester(src: dict, sem: dict, out: Path, updated: str = "") -> Path:
    img = Image.new("RGBA", (W, H), PAPER + (255,))
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([0, 0, W, 10], fill=t.MID)

    # κεφαλίδα (ίδια με το template)
    y0 = 52
    logo = t.ROOT / "assets" / f"logo_{src.get('id', '')}.png"
    lw = 0
    if logo.exists():
        lg = _navy_logo(logo)
        lg = lg.resize((92, int(lg.height * 92 / lg.width)), Image.LANCZOS)
        img.paste(lg, (44, y0), lg)
        lw = 92
    hx = 44 + (lw + 22 if lw else 0)
    d.text((hx, y0 + 4), "ΤΜΗΜΑ " + t._upper_gr(re.sub(r"\s*ΠΘ$", "", src["name"])),
           font=ImageFont.truetype(t.BLACK, 40), fill=INK)
    d.text((hx, y0 + 52), "ΠΑΝΕΠΙΣΤΗΜΙΟ ΘΕΣΣΑΛΙΑΣ", font=ImageFont.truetype(t.MED, 24), fill=MUTED)

    # τίτλος εξαμήνου
    ty = 176
    big = ImageFont.truetype(t.BLACK, 74)
    num = f"{sem['semester']}ο ΕΞΑΜΗΝΟ"
    d.text((44, ty), num, font=big, fill=INK)
    nw = d.textlength(num, font=big)
    d.text((44 + nw + 22, ty + 14), "ΩΡΟΛΟΓΙΟ ΠΡΟΓΡΑΜΜΑ", font=ImageFont.truetype(t.BOLD, 30), fill=t.MID)
    d.text((44 + nw + 22, ty + 48), "ΧΕΙΜΕΡΙΝΟ 2026-27", font=ImageFont.truetype(t.MED, 26), fill=MUTED)

    # πλέγμα
    gx0, gx1 = 40, W - 30
    gy0, gy1 = 300, H - 66
    hour_w = 62
    hours = [h for h in sem["hours"]]
    used = [a for a, b in hours if any(sem["cells"].get(f"{di}:{a}") for di in range(5))]
    first, last = min(used), max(b for a, b in hours if a in used)
    rows = [(a, b) for a, b in hours if first <= a < last]
    day_y = gy0 + 44
    col_w = (gx1 - gx0 - hour_w) / 5
    row_h = (gy1 - day_y) / len(rows)

    fday = ImageFont.truetype(t.BOLD, 26)
    for di, name in enumerate(DAYS):
        cx = gx0 + hour_w + col_w * di + col_w / 2
        w = d.textlength(name, font=fday)
        d.text((cx - w / 2, gy0 + 4), name, font=fday, fill=t.MID)
    fh = ImageFont.truetype(t.MED, 22)
    for i, (a, b) in enumerate(rows):
        y = day_y + i * row_h
        d.line([(gx0, y), (gx1, y)], fill=LINE, width=2)
        lab = f"{a}-{b}"
        d.text((gx0 + 2, y + 8), lab, font=fh, fill=MUTED)
        if (a, b) == (13, 14):
            d.rectangle([gx0 + hour_w, y + 1, gx1, y + row_h - 1], fill=(232, 237, 245))
    d.line([(gx0, day_y + len(rows) * row_h), (gx1, day_y + len(rows) * row_h)], fill=LINE, width=2)
    for di in range(6):
        x = gx0 + hour_w + col_w * di
        d.line([(x, day_y), (x, day_y + len(rows) * row_h)], fill=LINE, width=1)

    colors = {}
    for b in sorted(blocks(sem), key=lambda x: (x["day"], x["start"])):
        k = _key(b["e"])
        if k not in colors:
            colors[k] = PALETTE[len(colors) % len(PALETTE)]
        col = colors[k]
        ri = [a for a, _ in rows].index(b["start"])
        span = b["end"] - b["start"]
        lane_w = col_w / b["lanes"]
        x0 = gx0 + hour_w + col_w * b["day"] + lane_w * b["lane"] + 4
        x1 = x0 + lane_w - 8
        y0b = day_y + ri * row_h + 4
        y1b = y0b + span * row_h - 8
        d.rounded_rectangle([x0, y0b, x1, y1b], radius=12, fill=_tint(col))
        d.rounded_rectangle([x0, y0b, x0 + 7, y1b], radius=4, fill=col + (255,))
        pad = 14
        inner_w = x1 - x0 - pad - 8
        room = short_room(b["e"]["room"])
        fr = ImageFont.truetype(t.MED, 19 if b["lanes"] == 1 else 16)
        room_lines = t._wrap(d, room, fr, inner_w)
        room_h = len(room_lines) * 21
        avail = (y1b - y0b) - 16 - room_h - 6
        fn, lines = _fit(d, nice_name(b["e"]["name"]), t.BOLD, inner_w, avail,
                         24 if b["lanes"] == 1 else 18, minimum=14)
        yy = y0b + 9
        for ln in lines:
            d.text((x0 + pad, yy), ln, font=fn, fill=INK)
            yy += fn.size * 1.08
        ry = y1b - room_h - 6
        for ln in room_lines:
            d.text((x0 + pad, ry), ln, font=fr, fill=col)
            ry += 21

    note = "Πηγή: mie.uth.gr" + (f" · τελ. ενημέρωση {updated}" if updated else "")
    d.text((44, H - 50), note, font=ImageFont.truetype(t.REG, 22), fill=MUTED)
    sw = "Σύρε για τα άλλα εξάμηνα →"
    fsw = ImageFont.truetype(t.BOLD, 22)
    d.text((W - 30 - d.textlength(sw, font=fsw), H - 50), sw, font=fsw, fill=t.MID)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "JPEG", quality=93)
    return out


def render_cover(src: dict, out: Path, semesters=(1, 3, 5, 7, 9), story: bool = False,
                 subtitle: str = "ΧΕΙΜΕΡΙΝΟ ΕΞΑΜΗΝΟ 2026-27") -> Path:
    """Εξώφυλλο στο ίδιο ανοιχτό ύφος με τις σελίδες των εξαμήνων."""
    HH = 1920 if story else H
    top = 250 if story else 0
    img = Image.new("RGBA", (W, HH), PAPER + (255,))
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([0, 0, W, 10], fill=t.MID)

    x = 84
    y0 = 70 + top
    logo = t.ROOT / "assets" / f"logo_{src.get('id', '')}.png"
    lw = 0
    if logo.exists():
        lg = _navy_logo(logo)
        lg = lg.resize((104, int(lg.height * 104 / lg.width)), Image.LANCZOS)
        img.paste(lg, (x, y0), lg)
        lw = 104
    hx = x + (lw + 24 if lw else 0)
    d.text((hx, y0 + 8), "ΤΜΗΜΑ " + t._upper_gr(re.sub(r"\s*ΠΘ$", "", src["name"])),
           font=ImageFont.truetype(t.BLACK, 44), fill=INK)
    d.text((hx, y0 + 62), "ΠΑΝΕΠΙΣΤΗΜΙΟ ΘΕΣΣΑΛΙΑΣ", font=ImageFont.truetype(t.MED, 26), fill=MUTED)

    # ετικέτα
    y = y0 + 220
    fchip = ImageFont.truetype(t.BOLD, 30)
    chip = "ΩΡΟΛΟΓΙΟ ΠΡΟΓΡΑΜΜΑ"
    cw = d.textlength(chip, font=fchip)
    d.rounded_rectangle([x, y, x + cw + 40, y + 52], radius=26, fill=t.MID)
    d.text((x + 20, y + 8), chip, font=fchip, fill=t.WHITE)

    # μεγάλος τίτλος
    y += 100
    ft = ImageFont.truetype(t.BLACK, 150)
    for ln in ("ΤΑ ΜΑΘΗΜΑΤΑ", "ΤΟΥ ΕΞΑΜΗΝΟΥ"):
        d.text((x - 4, y), ln, font=ft, fill=INK)
        y += 150
    d.text((x, y + 20), subtitle, font=ImageFont.truetype(t.BOLD, 44), fill=t.MID)
    y += 110

    # «κουτάκια» εξαμήνων με τα χρώματα των σελίδων
    n = len(semesters)
    gap = 18
    bw = (W - 2 * x - gap * (n - 1)) / n
    bh = 190
    fnum = ImageFont.truetype(t.BLACK, 84)
    flab = ImageFont.truetype(t.BOLD, 24)
    for i, s in enumerate(semesters):
        col = PALETTE[i % len(PALETTE)]
        bx = x + i * (bw + gap)
        d.rounded_rectangle([bx, y, bx + bw, y + bh], radius=16, fill=_tint(col))
        d.rounded_rectangle([bx, y, bx + bw, y + 10], radius=5, fill=col)
        num = f"{s}ο"
        nw = d.textlength(num, font=fnum)
        d.text((bx + (bw - nw) / 2, y + 30), num, font=fnum, fill=INK)
        lab = "ΕΞΑΜΗΝΟ"
        d.text((bx + (bw - d.textlength(lab, font=flab)) / 2, y + 132), lab, font=flab, fill=col)
    y += bh + 50

    finfo = ImageFont.truetype(t.MED, 34)
    for ln in t._wrap(d, "Μαθήματα, ώρες και αίθουσες σε μία ματιά, για κάθε εξάμηνο.", finfo, W - 2 * x):
        d.text((x, y), ln, font=finfo, fill=MUTED)
        y += 44

    fb = HH - (300 if story else 0) - 110
    d.line([(x, fb - 24), (W - x, fb - 24)], fill=LINE, width=2)
    if story:
        d.text((x, fb), "Δες το post στο προφίλ μας", font=ImageFont.truetype(t.REG, 30), fill=MUTED)
        d.text((x, fb + 38), "Όλα τα εξάμηνα →", font=ImageFont.truetype(t.BOLD, 40), fill=t.MID)
    else:
        d.text((x, fb), "Σύρε για το εξάμηνό σου", font=ImageFont.truetype(t.REG, 30), fill=MUTED)
        d.text((x, fb + 38), "1ο · 3ο · 5ο · 7ο · 9ο →", font=ImageFont.truetype(t.BOLD, 40), fill=t.MID)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, "JPEG", quality=93)
    return out
