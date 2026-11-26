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


def ensure_keyboard(tg, state: dict, srcs: list[dict], chat: str = "", force: bool = False) -> None:
    """Κάθε ομάδα έχει τα δικά της κουμπιά «📝 Νέα» (μόνο για τα τμήματά της)."""
    kb = state.get("_tg_kb")
    if not isinstance(kb, dict):
        kb = state["_tg_kb"] = {}
    sig = ",".join(s["id"] for s in srcs)
    chat = chat or str(tg.chat_id)
    if force or kb.get(chat) != sig:
        tg.send_keyboard("⌨️ Κουμπιά για νέες ανακοινώσεις:\n\n" + HELP, keyboard_rows(srcs))
        kb[chat] = sig


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
    chat = str((msg.get("chat") or {}).get("id", tg.chat_id))
    # κάθε άτομο έχει δικό του πρόχειρο σε κάθε ομάδα
    uid = f"{chat}:{(msg.get('from') or {}).get('id', 'me')}"
    drafts = state.setdefault("_composes", {})
    old = uid.split(":", 1)[1]
    if old in drafts and chat == str(getattr(tg, "main_chat", chat)):   # παλιά μορφή (χωρίς ομάδα)
        drafts.setdefault(uid, drafts.pop(old))
    c = drafts.get(uid)

    if text in ("/start", "/menu", "/help", "βοήθεια"):
        kb = state.get("_tg_kb")
        if isinstance(kb, dict):
            kb.pop(chat, None)              # ξαναστέλνει τα κουμπιά στο τέλος του γύρου
        else:
            state["_tg_kb"] = {}
        return True
    if text in button_to_sid:
        if c and (c.get("title") or c.get("photos")):
            _say(tg, "🗑 Το προηγούμενο πρόχειρο που δεν είχε ολοκληρωθεί πετάχτηκε.")
        who = (msg.get("from") or {}).get("first_name", "")
        drafts[uid] = {"sid": button_to_sid[text], "title": None, "caption": None,
                       "photos": [], "own_cover": False, "changed": True, "by": who, "chat": chat}
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


def _keep_raw(path: Path) -> Path:
    """Κρατάμε τις φωτογραφίες (σε λογικό μέγεθος) για να ξαναφτιάχνονται οι σελίδες
    όταν αλλάξει το στυλ ή ο τίτλος."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((1800, 1800))
    im.save(path, "JPEG", quality=90)
    return path


def render(src: dict, p: dict, style: str, stories: bool = True) -> list[Path]:
    """Φτιάχνει (ξανά) όλες τις σελίδες μιας δικής σου ανακοίνωσης στο στυλ που διάλεξες.
      blue/white : εξώφυλλο-template με τον τίτλο + όλες οι φωτογραφίες
      photo      : η 1η φωτογραφία σβήνει σε λευκό με τον τίτλο από κάτω + οι υπόλοιπες
      own        : η 1η φωτογραφία ολόκληρη ως εξώφυλλο + οι υπόλοιπες"""
    raws = [ROOT / r for r in p.get("raws", [])]
    folder = ROOT / Path(p["slides"][0]).parent
    v = p.get("v", 0) + 1
    p["v"] = v
    date = datetime.fromisoformat(p["date"])
    look = "white" if style in ("white", "photo") else "blue"   # φόντο σελίδων-φωτογραφιών
    slides, rest = [], raws
    if style == "photo" and raws:
        slides.append(theme_dap.photo_cover(src, raws[0], p["title"], folder / f"s00_v{v}.jpg",
                                            arrow=len(raws) > 1, text=False))
        rest = raws[1:]
    elif style == "own" and raws:
        slides.append(theme_dap.photo_slide(raws[0], folder / f"s00_v{v}.jpg", fill=True))
        rest = raws[1:]
    else:
        swipe = ("Σύρε για περισσότερα", "→") if raws else ("", "")
        slides.append(theme_dap.make_image(src, p["title"], date, "", "", folder / f"s00_v{v}.jpg",
                                           footer=swipe, style=look))
    for i, r in enumerate(rest, 1):
        slides.append(theme_dap.photo_slide(r, folder / f"s{i:02d}_v{v}.jpg", style=look))
    out = list(slides)
    p["slides"] = [str(x.relative_to(ROOT)) for x in slides]
    p["img"] = p["slides"][0]
    p["style"] = style
    if stories and (p.get("story") or "story" not in p):
        if style == "photo" and raws:
            st = theme_dap.photo_cover(src, raws[0], p["title"], folder / f"story_v{v}.jpg", story=True,
                                       text=False)
        elif style == "own" and raws:
            st = theme_dap.photo_slide(raws[0], folder / f"story_v{v}.jpg", story=True, fill=True)
        else:
            st = theme_dap.make_image(src, p["title"], date, "", "", folder / f"story_v{v}.jpg",
                                      story=True, footer=("", ""), style=look)
        p["story"] = str(st.relative_to(ROOT))
        out.append(st)
    return out


def build(tg, c: dict, src: dict, stories: bool) -> dict:
    """Κατεβάζει τις φωτογραφίες, φτιάχνει τις σελίδες και επιστρέφει την εγγραφή pending."""
    cid = hashlib.sha1(f"{c['sid']}{c['title']}{time.time()}".encode()).hexdigest()[:12]
    folder = IMAGES / c["sid"] / f"custom_{cid}"
    raw = [_keep_raw(tg.download(fid, folder / f"raw_{i}.jpg")) for i, fid in enumerate(c["photos"])]
    now = datetime.now(ATHENS)
    caption = c.get("caption") or c["title"]
    if src.get("hashtags") and src["hashtags"] not in caption:
        caption = f"{caption}\n\n{src['hashtags']}"
    p = {"kind": "custom", "sid": c["sid"], "key": f"custom:{cid}", "title": c["title"],
         "date": now.isoformat(), "excerpt": "", "link": "", "category": "",
         "caption": caption[:2200], "own_cover": bool(c.get("own_cover")),
         "raws": [str(r.relative_to(ROOT)) for r in raw],
         "slides": [str((folder / "s00.jpg").relative_to(ROOT))], "v": -1}
    if not stories:
        p["story"] = None
    if re.match(r"\s*(δοκιμ|🧪|test)", c["title"], re.I):    # «Δοκιμή …»: δεν ανεβαίνει ποτέ
        p["test"] = True
        p["title"] = re.sub(r"^\s*(δοκιμ\S*|🧪|test)\s*[:\-–]?\s*", "", c["title"], flags=re.I) or c["title"]
    # με φωτογραφίες: εξ ορισμού το στυλ «φωτογραφία συλλόγου»· αλλάζει από τα κουμπιά
    style = "own" if c.get("own_cover") else ("photo" if raw else "blue")
    render(src, p, style, stories)
    return p


def regen_cover(src: dict, p: dict) -> list[Path]:
    """Μετά από αλλαγή τίτλου: ξαναφτιάχνει τις σελίδες στο ίδιο στυλ."""
    if p.get("fixed"):
        return []
    if "raws" not in p:                      # παλιές εγγραφές χωρίς κρατημένες φωτογραφίες
        if p.get("own_cover"):
            return []
        p["raws"] = []
    return render(src, p, p.get("style") or ("own" if p.get("own_cover") else "blue"), bool(p.get("story")))


def styles_for(p: dict) -> list[str]:
    """Ποια κουμπιά στυλ έχει νόημα να εμφανιστούν."""
    if p.get("fixed"):
        return []
    if p.get("kind") == "custom":
        if "raws" not in p:
            return []
        return ["blue", "white"] + (["photo"] if p["raws"] else [])
    return ["blue", "white"]
