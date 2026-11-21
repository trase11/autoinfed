"""Δικές σου ανακοινώσεις από το Telegram (π.χ. απόφαση ΔΣ ως καρουσέλ).

Ροή (όλα με μόνιμα κουμπιά κάτω στο Telegram, χωρίς να περιμένεις απάντηση):
  📝 Νέα: <σύλλογος>  →  τίτλος (κείμενο)  →  φωτογραφίες (με τη σειρά του καρουσέλ)
  →  λεζάντα (κείμενο)  →  ✅ Τέλος
  🖼 Δικό μου εξώφυλλο: η 1η φωτογραφία γίνεται εξώφυλλο αντί για το template.
  🗑 Ακύρωση: πετάει το πρόχειρο.
Στο ✅ Τέλος φτιάχνονται οι εικόνες και έρχεται προεπισκόπηση με ✅/❌ για έγκριση.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import theme_dap

ROOT = Path(__file__).resolve().parent
IMAGES = ROOT / "images"
ATHENS = ZoneInfo("Europe/Athens")
MAX_SLIDES = 10          # όριο καρουσέλ του Instagram API

NEW = "📝 Νέα: "
OWN_COVER = "🖼 Δικό μου εξώφυλλο"
DONE = "✅ Τέλος"
CANCEL = "🗑 Ακύρωση"

HELP = ("📝 Νέα ανακοίνωση:\n"
        "1. Πάτα «📝 Νέα: …» για τον σύλλογο\n"
        "2. Γράψε τον τίτλο\n"
        "3. Στείλε φωτογραφίες (αν θες) με τη σειρά του καρουσέλ\n"
        "4. Γράψε τη λεζάντα του post\n"
        "5. Πάτα «✅ Τέλος»\n\n"
        "Το εξώφυλλο είναι ο τίτλος στο template της καμπάνιας. "
        "Αν θες να είναι η 1η φωτογραφία σου, πάτα «🖼 Δικό μου εξώφυλλο».\n"
        "Μετά το «✅ Τέλος» έρχεται προεπισκόπηση για ✅/❌.\n\n"
        "⏱ Το bot διαβάζει τα μηνύματα κάθε ~15 λεπτά, οπότε στείλε τα όλα "
        "και περίμενε την προεπισκόπηση.")


def short_name(src: dict) -> str:
    return src.get("short") or re.sub(r"\s*ΠΘ$", "", src["name"])


def keyboard_rows(srcs: list[dict]) -> list[list[str]]:
    btns = [NEW + short_name(s) for s in srcs]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([OWN_COVER])
    rows.append([DONE, CANCEL])
    return rows


def ensure_keyboard(tg, state: dict, srcs: list[dict], force: bool = False) -> None:
    sig = ",".join(s["id"] for s in srcs)
    if force or state.get("_tg_kb") != sig:
        tg.send_keyboard("⌨️ Κουμπιά για νέες ανακοινώσεις:\n\n" + HELP, keyboard_rows(srcs))
        state["_tg_kb"] = sig


def _largest_photo(msg: dict) -> str | None:
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"]
    doc = msg.get("document") or {}
    if (doc.get("mime_type") or "").startswith("image/"):
        return doc["file_id"]
    return None


def status_line(c: dict, srcs_by_id: dict) -> str:
    name = srcs_by_id.get(c["sid"], {}).get("name", c["sid"])
    parts = [f"📝 Πρόχειρο για: {name}",
             f"• Τίτλος: {c['title'] or '— (γράψε τον τίτλο)'}",
             f"• Φωτογραφίες: {len(c['photos'])}" + (" (η 1η = εξώφυλλο)" if c.get("own_cover") else ""),
             f"• Λεζάντα: {'✓' if c.get('caption') else '— (προαιρετικά)'}",
             "", "Όταν είσαι έτοιμος πάτα «✅ Τέλος»."]
    return "\n".join(parts)


def _say(tg, text: str) -> None:
    if not getattr(tg, "live", False):     # με Worker, την απάντηση την έχει δώσει ήδη εκείνος
        tg.notify(text)


def handle_message(tg, state: dict, msg: dict, srcs_by_id: dict, button_to_sid: dict) -> bool:
    """Επεξεργάζεται ένα δικό σου μήνυμα για σύνθεση. Επιστρέφει True αν το «κατανάλωσε».
    Αν το μήνυμα ολοκληρώνει ανακοίνωση, την αφήνει στο state['_compose_done']."""
    text = re.sub(r"@\w+bot$", "", (msg.get("text") or "").strip(), flags=re.I)   # /start@onoma_bot
    uid = str((msg.get("from") or {}).get("id", "me"))     # κάθε άτομο έχει δικό του πρόχειρο
    drafts = state.setdefault("_composes", {})
    if "_compose" in state:                                 # παλιά μορφή (ένα κοινό πρόχειρο)
        drafts.setdefault(uid, state.pop("_compose"))
    c = drafts.get(uid)

    if text in ("/start", "/menu", "/help", "βοήθεια"):
        state["_tg_kb"] = None              # ξαναστέλνει τα κουμπιά στο τέλος του γύρου
        return True
    if text in button_to_sid:
        if c and (c.get("title") or c.get("photos")):
            _say(tg, "🗑 Το προηγούμενο πρόχειρο που δεν είχε ολοκληρωθεί πετάχτηκε.")
        who = (msg.get("from") or {}).get("first_name", "")
        drafts[uid] = {"sid": button_to_sid[text], "title": None, "caption": None,
                       "photos": [], "own_cover": False, "changed": True, "by": who}
        return True
    if text == CANCEL:
        if c:
            drafts.pop(uid, None)
            _say(tg, "🗑 Το πρόχειρο ακυρώθηκε.")
        return True
    if not c:
        return False                        # δεν υπάρχει πρόχειρο: δεν είναι για εμάς

    c["changed"] = True
    if text == OWN_COVER:
        c["own_cover"] = True
        return True
    if text == DONE:
        if not c.get("title"):
            _say(tg, "⚠️ Λείπει ο τίτλος. Γράψε τον τίτλο και πάτα ξανά «✅ Τέλος».")
            return True
        if c.get("own_cover") and not c["photos"]:
            c["own_cover"] = False
        state.setdefault("_compose_done", []).append(c)
        drafts.pop(uid, None)
        return True

    fid = _largest_photo(msg)
    if fid:
        if len(c["photos"]) >= MAX_SLIDES:
            _say(tg, f"⚠️ Το καρουσέλ χωράει έως {MAX_SLIDES} εικόνες· οι επιπλέον αγνοούνται.")
        else:
            c["photos"].append(fid)
        cap = (msg.get("caption") or "").strip()
        if cap:                              # κείμενο μαζί με φωτογραφία
            if not c["title"]:
                c["title"] = cap
            else:
                c["caption"] = (c["caption"] + "\n\n" + cap) if c["caption"] else cap
        return True
    if text:
        if not c["title"]:
            c["title"] = text
        else:
            c["caption"] = (c["caption"] + "\n\n" + text) if c["caption"] else text
        return True
    return True


def build(tg, c: dict, src: dict, stories: bool) -> dict:
    """Κατεβάζει τις φωτογραφίες, φτιάχνει τις σελίδες και επιστρέφει την εγγραφή pending."""
    cid = hashlib.sha1(f"{c['sid']}{c['title']}{time.time()}".encode()).hexdigest()[:12]
    folder = IMAGES / c["sid"] / f"custom_{cid}"
    raw = [tg.download(fid, folder / f"raw_{i}.jpg") for i, fid in enumerate(c["photos"])]
    now = datetime.now(ATHENS)
    slides = []
    total = len(raw) + (0 if c.get("own_cover") else 1)
    swipe = ("Σύρε για περισσότερα", "→") if total > 1 else ("", "")
    if c.get("own_cover"):
        slides.append(theme_dap.photo_slide(raw[0], folder / "s00.jpg", fill=True))
        rest = raw[1:]
    else:
        slides.append(theme_dap.make_image(src, c["title"], now, "", "", folder / "s00.jpg", footer=swipe))
        rest = raw
    for i, r in enumerate(rest, 1):
        slides.append(theme_dap.photo_slide(r, folder / f"s{i:02d}.jpg"))
    story = None
    if stories:
        if c.get("own_cover"):
            story = theme_dap.photo_slide(raw[0], folder / "story.jpg", story=True, fill=True)
        else:
            story = theme_dap.make_image(src, c["title"], now, "", "", folder / "story.jpg",
                                         story=True, footer=("", ""))
    for r in raw:                            # τα πρωτότυπα δεν χρειάζονται στο repo
        r.unlink(missing_ok=True)
    caption = c.get("caption") or c["title"]
    if src.get("hashtags") and src["hashtags"] not in caption:
        caption = f"{caption}\n\n{src['hashtags']}"
    return {"kind": "custom", "sid": c["sid"], "key": f"custom:{cid}", "title": c["title"],
            "date": now.isoformat(), "excerpt": "", "link": "", "category": "",
            "caption": caption[:2200], "own_cover": bool(c.get("own_cover")),
            "slides": [str(p.relative_to(ROOT)) for p in slides],
            "img": str(slides[0].relative_to(ROOT)),
            "story": str(story.relative_to(ROOT)) if story else None}


def regen_cover(src: dict, p: dict) -> list[Path]:
    """Μετά από αλλαγή τίτλου: νέο εξώφυλλο (αν είναι template) και story."""
    if p.get("own_cover"):
        return []
    folder = ROOT / Path(p["slides"][0]).parent
    v = p.get("v", 0) + 1
    p["v"] = v
    date = datetime.fromisoformat(p["date"])
    swipe = ("Σύρε για περισσότερα", "→") if len(p["slides"]) > 1 else ("", "")
    cover = theme_dap.make_image(src, p["title"], date, "", "", folder / f"s00_v{v}.jpg", footer=swipe)
    p["slides"][0] = str(cover.relative_to(ROOT))
    p["img"] = p["slides"][0]
    out = [cover]
    if p.get("story"):
        st = theme_dap.make_image(src, p["title"], date, "", "", folder / f"story_v{v}.jpg",
                                  story=True, footer=("", ""))
        p["story"] = str(st.relative_to(ROOT))
        out.append(st)
    return out
