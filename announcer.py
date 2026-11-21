#!/usr/bin/env python3
"""
Ελέγχει τα RSS των τμημάτων/συλλόγων και ανεβάζει τις νέες ανακοινώσεις
στον λογαριασμό Instagram του καθενός.

Τρέχει από το GitHub Actions κάθε ώρα. Τοπικά:
    python announcer.py --dry-run          # φτιάχνει μόνο εικόνες, δεν ανεβάζει
    python announcer.py --dry-run --feed-file tests/sample_feed.xml
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml
from PIL import Image, ImageDraw, ImageFont

import compose
import theme_dap
from telegram_approval import Telegram, TelegramError

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "seen.json"
IMAGES_DIR = ROOT / "images"
FONT_REG = str(ROOT / "fonts" / "DejaVuSans.ttf")
FONT_BOLD = str(ROOT / "fonts" / "DejaVuSans-Bold.ttf")

GRAPH = "https://graph.instagram.com/v23.0"
ATHENS = ZoneInfo("Europe/Athens")
UA = "Mozilla/5.0 (compatible; uni-announcer/1.0; +https://github.com)"


# --------------------------------------------------------------------------- #
# Βοηθητικά
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(f"[{datetime.now(ATHENS):%H:%M:%S}] {msg}", flush=True)


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    text = re.sub(r"\[&#8230;\]|\[…\]|\[\.\.\.\]", "…", text)
    return re.sub(r"\s+", " ", text).strip()


def _fold(s: str) -> str:
    """Πεζά χωρίς τόνους/στίξη, για σύγκριση κειμένων."""
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower())
    return re.sub(r"[\W_]+", "", "".join(c for c in s if not unicodedata.combining(c)))


def clean_excerpt(excerpt: str, title: str) -> str:
    # Το αυτόματο υποσέλιδο του WordPress: «Το άρθρο … εμφανίστηκε πρώτα στο …»
    excerpt = re.sub(r"\s*(Το άρθρο|The post)\s.*?(εμφανίστηκε πρώτα στο|appeared first on)\s.*$",
                     "", excerpt, flags=re.S).strip()
    # Αν η περίληψη ξεκινά με τον τίτλο (με άλλα κεφαλαία/τόνους), τον αφαιρούμε
    ft = _fold(title)
    if ft:
        acc = ""
        for i, ch in enumerate(excerpt):
            acc = _fold(excerpt[:i + 1])
            if acc == ft:
                excerpt = excerpt[i + 1:].lstrip(" .:–-")
                break
            if not ft.startswith(acc):
                break
    return excerpt.strip()


def item_key(entry) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def item_date(entry) -> datetime:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        return datetime(*t[:6], tzinfo=timezone.utc).astimezone(ATHENS)
    return datetime.now(ATHENS)


# --------------------------------------------------------------------------- #
# Ανάγνωση RSS
# --------------------------------------------------------------------------- #
DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def fetch_html_entries(src: dict, html_file: str | None = None) -> list:
    """Για site χωρίς RSS: διαβάζει τη σελίδα λίστας και βρίσκει κάθε
    ανακοίνωση από τη γραμμή ημερομηνίας (ΗΗ/ΜΜ/ΕΕΕΕ) και τον τίτλο πάνω της."""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    url = src["url"]
    raw = Path(html_file).read_bytes() if html_file else \
        requests.get(url, headers={"User-Agent": UA}, timeout=30).content
    soup = BeautifulSoup(raw, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        t.decompose()

    entries, seen_titles = [], set()
    for node in soup.find_all(string=DATE_RE):
        if len(node.strip()) > 40:          # ημερομηνία μέσα σε κείμενο, όχι γραμμή ημερομηνίας
            continue
        # Ανεβαίνουμε στο μικρότερο κουτί που περιέχει τίτλο + ημερομηνία
        box = node.parent
        while box.parent is not None and len(box.get_text(" ", strip=True)) < len(node.strip()) + 8:
            box = box.parent
        lines = [l.strip() for l in box.get_text("\n").split("\n") if l.strip()]
        try:
            i = next(k for k, l in enumerate(lines) if DATE_RE.search(l) and len(l) <= 40)
        except StopIteration:
            continue
        if i == 0:   # ο τίτλος είναι στο αδελφό στοιχείο πάνω από την ημερομηνία
            prev = box.find_previous(string=lambda s: s and s.strip() and not DATE_RE.fullmatch(s.strip()))
            title = prev.strip() if prev else ""
        else:
            title = lines[i - 1]
        body = " ".join(lines[i + 1:i + 12])
        if not title or title in seen_titles or len(title) < 8:
            continue
        seen_titles.add(title)
        link = url
        for a in box.find_all("a", href=True):
            if re.search(src.get("link_pattern", r"/announcements/\d+|/uploads/"), a["href"]):
                link = urljoin(url, a["href"])
                m = re.search(r"/(?:announcements|uploads/pages)/(\d+)", a["href"])
                if m and src.get("item_url"):
                    link = src["item_url"].format(id=m.group(1))
                break
        key = "h:" + hashlib.sha1(title.encode()).hexdigest()[:16]
        entries.append({"id": key, "title": title, "link": link, "summary": body, "tags": []})

    if not entries:
        raise RuntimeError("Η σελίδα διαβάστηκε αλλά δεν βρέθηκε καμία ανακοίνωση. "
                           "Μάλλον άλλαξε η εμφάνιση του site.")
    entries.reverse()   # η σελίδα έχει τις νεότερες πάνω· ανεβάζουμε τις παλιότερες πρώτα
    return entries


ECLASS_RSS = "https://eclass.uth.gr/modules/announcements/rss.php?c={code}"


_ECLASS_LINKS: dict | None = None


def eclass_links() -> dict:
    """Προσωπικοί σύνδεσμοι RSS από το secret ECLASS_RSS (ένας ανά γραμμή, όπως τους
    δίνει το eClass στις Ανακοινώσεις του μαθήματος). Το μάθημα βρίσκεται από το c=…
    μέσα στον σύνδεσμο. Δεν τυπώνονται ποτέ, γιατί περιέχουν προσωπικό κωδικό."""
    global _ECLASS_LINKS
    if _ECLASS_LINKS is None:
        _ECLASS_LINKS = {}
        for url in re.findall(r"https?://\S+", os.environ.get("ECLASS_RSS", "")):
            m = re.search(r"[?&]c(?:ourse)?=([A-Za-z0-9_]+)", url)
            if m:
                _ECLASS_LINKS[m.group(1)] = url.strip().rstrip(",;")
    return _ECLASS_LINKS


def fetch_eclass(src: dict, state: dict, settings: dict) -> list:
    """Ανακοινώσεις μαθημάτων eClass για τον λογαριασμό του τμήματος.
    - ελέγχονται κάθε `eclass_every_minutes` (προεπιλογή 30') για να μη φορτώνεται το eClass
    - την 1η φορά για κάθε μάθημα οι παλιές ανακοινώσεις σημειώνονται ως γνωστές
    - η ετικέτα στην εικόνα είναι το όνομα του μαθήματος"""
    sid = src["id"]
    every = int(settings.get("eclass_every_minutes", 30))
    last = state.setdefault("_eclass_last", {})
    now = time.time()
    if now - last.get(sid, 0) < every * 60 - 30:
        return []
    last[sid] = now
    s = state[sid]
    known = s.setdefault("eclass_feeds", [])
    out = []
    for course in src["eclass"]:
        code, name = course["code"], course.get("name", course["code"])
        url = eclass_links().get(code) or ECLASS_RSS.format(code=code)
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            parsed = feedparser.parse(r.content)
            if r.status_code != 200 or (not parsed.entries and not parsed.feed.get("title")):
                raise RuntimeError(f"HTTP {r.status_code}, δεν είναι RSS (μάλλον θέλει σύνδεση)")
        except Exception as e:
            hint = "" if code in eclass_links() else " · βάλε τον προσωπικό σύνδεσμο RSS στο secret ECLASS_RSS"
            log(f"⚠️  {sid}: eClass {code} ({name}): {e}{hint}")
            continue
        entries = []
        for e in parsed.entries:
            e["tags"] = [{"term": name}]          # ετικέτα = όνομα μαθήματος
            if not (e.get("id") or e.get("link")):
                continue
            e["id"] = "eclass:" + (e.get("id") or e.get("link"))
            # το μάθημα φαίνεται και στη λεζάντα/περίληψη, όχι μόνο στην ετικέτα της εικόνας
            e["summary"] = f"Μάθημα: {name}. " + (e.get("summary") or "")
            entries.append(e)
        if code not in known:                     # πρώτη φορά: τα παλιά = γνωστά
            for e in entries:
                s["seen"].setdefault(e["id"], "initial")
            known.append(code)
            log(f"🆕 {sid}: eClass {code} ({name}): {len(entries)} παλιές ανακοινώσεις σημειώθηκαν ως γνωστές")
            continue
        out += entries
    if out or src["eclass"]:
        log(f"📚 {sid}: eClass, {len(src['eclass'])} μαθήματα ελέγχθηκαν")
    return out


def fetch_entries(src: dict, feed_file: str | None = None) -> list:
    if src.get("type") == "html":
        return fetch_html_entries(src, feed_file)
    if feed_file:
        parsed = feedparser.parse(Path(feed_file).read_bytes())
    else:
        r = requests.get(src["feed"], headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"Δεν διαβάστηκε το RSS: {parsed.bozo_exception}")

    wanted = set(src.get("categories") or [])
    entries = []
    for e in parsed.entries:
        cats = {t.get("term", "") for t in e.get("tags", [])}
        if wanted and not (cats & wanted):
            continue
        entries.append(e)
    entries.sort(key=item_date)  # παλιότερη πρώτη
    return entries


# --------------------------------------------------------------------------- #
# Εικόνα 1080x1350 (4:5, το μέγιστο ύψος που δέχεται το feed)
# --------------------------------------------------------------------------- #
W, H = 1080, 1350
PAD = 80


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _hex(c: str) -> tuple:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def make_image(src: dict, title: str, date: datetime, excerpt: str, category: str, out: Path,
               story: bool = False) -> Path:
    """story=False: post 1080x1350 (4:5). story=True: story 1080x1920 (9:16), με περιθώρια
    πάνω/κάτω γιατί εκεί το Instagram βάζει το όνομα του λογαριασμού και το πεδίο απάντησης."""
    H = 1920 if story else 1350
    top = 200 if story else 0
    color = _hex(src.get("color", "#0b3d91"))
    img = Image.new("RGB", (W, H), (247, 246, 242))
    d = ImageDraw.Draw(img)
    maxw = W - 2 * PAD

    # Πάνω μπάντα
    band_h = top + 230
    d.rectangle([0, 0, W, band_h], fill=color)
    d.text((PAD, top + 62), "ΑΝΑΚΟΙΝΩΣΗ", font=ImageFont.truetype(FONT_BOLD, 30), fill=(255, 255, 255, 200))
    name_font = ImageFont.truetype(FONT_BOLD, 46)
    name = src["name"]
    while d.textlength(name, font=name_font) > maxw and name_font.size > 28:
        name_font = ImageFont.truetype(FONT_BOLD, name_font.size - 2)
    d.text((PAD, top + 112), name, font=name_font, fill=(255, 255, 255))

    y = band_h + 70

    # Ετικέτα κατηγορίας + ημερομηνία
    meta_font = ImageFont.truetype(FONT_BOLD, 30)
    x = PAD
    if category:
        cw = d.textlength(category, font=meta_font)
        d.rounded_rectangle([x, y, x + cw + 40, y + 54], radius=27, fill=_tint(color))
        d.text((x + 20, y + 10), category, font=meta_font, fill=color)
        x += cw + 64
    d.text((x, y + 10), date.strftime("%d/%m/%Y"), font=meta_font, fill=(110, 110, 110))
    y += 110

    # Τίτλος: μικραίνει μέχρι να χωρέσει σε 7 γραμμές
    footer_y = H - (330 if story else 130)
    max_lines = 9 if story else 7
    size = 76 if story else 72
    while True:
        tf = ImageFont.truetype(FONT_BOLD, size)
        lines = _wrap(d, title, tf, maxw)
        line_h = int(size * 1.22)
        if len(lines) <= max_lines or size <= 40:
            break
        size -= 4
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" ,.;") + "…"
    for ln in lines:
        d.text((PAD, y), ln, font=tf, fill=(25, 25, 30))
        y += line_h

    # Περίληψη στον χώρο που περισσεύει
    y += 40
    ef = ImageFont.truetype(FONT_REG, 34)
    eh = 48
    room = (footer_y - 40 - y) // eh
    if excerpt and room >= 2:
        room = min(room, 10 if story else 6)
        elines = _wrap(d, excerpt, ef, maxw - 30)
        if len(elines) > room:
            elines = elines[:room]
            elines[-1] = elines[-1].rstrip(" ,.;…") + "…"
        top = y
        for ln in elines:
            d.text((PAD + 30, y), ln, font=ef, fill=(80, 80, 85))
            y += eh
        d.rectangle([PAD, top + 6, PAD + 6, y - 8], fill=color)

    # Υποσέλιδο
    d.line([PAD, footer_y, W - PAD, footer_y], fill=(215, 213, 207), width=2)
    domain = re.sub(r"^https?://([^/?]+).*$", r"\1", src.get("site") or src.get("url") or src.get("feed", ""))
    ff = ImageFont.truetype(FONT_REG, 30)
    d.text((PAD, footer_y + 34), "Ολόκληρη η ανακοίνωση στο", font=ff, fill=(110, 110, 110))
    dw = d.textlength(domain, font=ImageFont.truetype(FONT_BOLD, 30))
    d.text((W - PAD - dw, footer_y + 34), domain, font=ImageFont.truetype(FONT_BOLD, 30), fill=color)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=90)  # το Instagram δέχεται μόνο JPEG
    return out


def _tint(rgb, amount=0.86):
    return tuple(int(c + (255 - c) * amount) for c in rgb)


# --------------------------------------------------------------------------- #
# Λεζάντα
# --------------------------------------------------------------------------- #
def make_caption(src, title, date, excerpt, link) -> str:
    parts = [f"📢 {title}", ""]
    if excerpt:
        parts += [textwrap.shorten(excerpt, 1200, placeholder="…"), ""]
    parts += [f"📅 {date:%d/%m/%Y}", f"🔗 {link}"]
    if src.get("hashtags"):
        parts += ["", src["hashtags"]]
    return "\n".join(parts)[:2200]


# --------------------------------------------------------------------------- #
# Instagram API (Instagram Login, graph.instagram.com)
# --------------------------------------------------------------------------- #
class InstagramError(RuntimeError):
    pass


# Δύο είδη token:
#  • "Instagram login" (παλιές εφαρμογές): ξεκινάει με IGAA, graph.instagram.com, λήγει σε 60 μέρες
#  • "Unified login" (εφαρμογές από τέλη Οκτ. 2026): system-user token, graph.facebook.com,
#    δεν λήγει ποτέ
GRAPH_FB = "https://graph.facebook.com/v23.0"


def _host(token: str) -> str:
    return GRAPH if token.startswith("IGAA") else GRAPH_FB


def ig_call(method, path, token, **params):
    params["access_token"] = token
    r = requests.request(method, f"{_host(token)}/{path}", params=params if method == "GET" else None,
                         data=params if method == "POST" else None, timeout=60)
    data = r.json() if r.content else {}
    if r.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        raise InstagramError(f"{err.get('message', r.text)} (code {err.get('code')})")
    return data


def ig_account_id(token: str, given: str | None) -> str:
    """Βρίσκει τον λογαριασμό Instagram ενός token, δοκιμάζοντας όλους τους τρόπους
    που δίνει η Meta (Instagram login, Unified login/system user, σελίδα Facebook)."""
    if given:
        return str(given)
    errors = []
    tries = [
        ("ig:me", lambda: requests.get(f"{GRAPH}/me", params={"fields": "user_id,username",
                                        "access_token": token}, timeout=30).json(),
         lambda d: d.get("user_id")),
        ("fb:assigned_instagram_accounts", lambda: requests.get(f"{GRAPH_FB}/me/assigned_instagram_accounts",
                                        params={"fields": "id,username", "access_token": token}, timeout=30).json(),
         lambda d: (d.get("data") or [{}])[0].get("id") if len(d.get("data") or []) == 1 else None),
        ("fb:accounts", lambda: requests.get(f"{GRAPH_FB}/me/accounts",
                                        params={"fields": "instagram_business_account", "access_token": token}, timeout=30).json(),
         lambda d: next((x["instagram_business_account"]["id"] for x in d.get("data") or []
                         if x.get("instagram_business_account")), None)),
        ("fb:me", lambda: requests.get(f"{GRAPH_FB}/me", params={"fields": "id,name",
                                        "access_token": token}, timeout=30).json(),
         lambda d: None),   # μόνο για διάγνωση: σε ποιον ανήκει το token
    ]
    for name, call, pick in tries:
        try:
            d = call()
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
        if "error" in d:
            errors.append(f"{name}: {d['error'].get('message', '')[:120]}")
            continue
        got = pick(d)
        if got:
            return str(got)
        errors.append(f"{name}: {str(d)[:120]}")
    raise InstagramError("Δεν βρέθηκε ο λογαριασμός Instagram του token. Απαντήσεις Meta → " + " | ".join(errors))


def ig_recent_captions(cred, limit: int = 30) -> str:
    """Οι λεζάντες των τελευταίων posts του λογαριασμού, για έλεγχο διπλών.
    Αν αποτύχει, επιστρέφει κενό ώστε να μη μπλοκάρει τη δημοσίευση."""
    token, given_id = (cred, None) if isinstance(cred, str) else (cred["token"], cred.get("ig_id"))
    try:
        uid = ig_account_id(token, given_id)
        data = ig_call("GET", f"{uid}/media", token, fields="caption", limit=limit).get("data", [])
        return "\n".join(m.get("caption") or "" for m in data)
    except Exception as e:
        log(f"   (δεν έγινε έλεγχος διπλών: {e})")
        return ""


def _wait_ready(container: str, token: str) -> None:
    for _ in range(30):
        status = ig_call("GET", container, token, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Η εικόνα απορρίφθηκε (status {status})")
        time.sleep(3)


def ig_publish_carousel(cred, image_urls: list[str], caption: str) -> str:
    """Καρουσέλ: ένα container ανά εικόνα, μετά ένα CAROUSEL με όλα, μετά δημοσίευση."""
    token, given_id = (cred, None) if isinstance(cred, str) else (cred["token"], cred.get("ig_id"))
    uid = ig_account_id(token, given_id)
    children = []
    for url in image_urls[:10]:
        cid = ig_call("POST", f"{uid}/media", token, image_url=url, is_carousel_item="true")["id"]
        _wait_ready(cid, token)
        children.append(cid)
    container = ig_call("POST", f"{uid}/media", token, media_type="CAROUSEL",
                        children=",".join(children), caption=caption)["id"]
    _wait_ready(container, token)
    return ig_call("POST", f"{uid}/media_publish", token, creation_id=container)["id"]


def ig_publish(cred, image_url: str, caption: str = "", story: bool = False) -> str:
    token, given_id = (cred, None) if isinstance(cred, str) else (cred["token"], cred.get("ig_id"))
    uid = ig_account_id(token, given_id)
    extra = {"media_type": "STORIES"} if story else {"caption": caption}
    container = ig_call("POST", f"{uid}/media", token, image_url=image_url, **extra)["id"]
    for _ in range(20):  # περιμένουμε να επεξεργαστεί την εικόνα
        status = ig_call("GET", container, token, fields="status_code").get("status_code")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Η εικόνα απορρίφθηκε (status {status})")
        time.sleep(3)
    return ig_call("POST", f"{uid}/media_publish", token, creation_id=container)["id"]


# --------------------------------------------------------------------------- #
# Git: οι εικόνες πρέπει να είναι online πριν τις ζητήσει το Instagram
# --------------------------------------------------------------------------- #
def push_images(paths: list[Path]) -> None:
    rel = [str(p.relative_to(ROOT)) for p in paths]
    subprocess.run(["git", "add", *rel], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    subprocess.run(["git", "commit", "-q", "-m", f"Εικόνες για {len(rel)} ανακοινώσεις"], cwd=ROOT, check=True)
    subprocess.run(["git", "pull", "-q", "--rebase"], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "-q"], cwd=ROOT, check=True)
    time.sleep(10)  # λίγος χρόνος για να εμφανιστούν στο raw.githubusercontent.com


def raw_url(path: Path) -> str:
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{path.relative_to(ROOT).as_posix()}"


# --------------------------------------------------------------------------- #
# Κύρια ροή
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Έγκριση μέσω Telegram
# --------------------------------------------------------------------------- #
def _pid(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def approval_round(tg: Telegram, cfg: dict, state: dict, new_jobs: list, category_of: dict,
                   tokens: dict | None = None):
    """1) διαβάζει τα ✅/❌, τις αλλαγές (reply) και τα πρόχειρα (📝 Νέα), 2) στέλνει
    προεπισκόπηση για τα νέα, 3) επιστρέφει όσα έχουν εγκριθεί για δημοσίευση."""
    pending = state.setdefault("_pending", {})
    offset = state.get("_tg_offset", 0)
    try:
        decisions, offset, strangers, msgs = tg.read_updates(offset, int(os.environ.get("WAIT_FOR") or 0))
        state["_tg_offset"] = offset
    except TelegramError as e:
        log(f"⚠️  Telegram: {e}")
        decisions, strangers, msgs = {}, [], []

    if not tg.chat_id:
        try:
            me = tg._call("getMe")
            bot = f"@{me.get('username')}"
            hook = tg._call("getWebhookInfo").get("url")
            if hook:
                log(f"⚠️  Το bot έχει webhook ({hook}), οπότε δεν φτάνουν μηνύματα εδώ")
        except TelegramError as e:
            bot = f"(άκυρο token: {e})"
        log(f"ℹ️  Bot: {bot}. Δεν έχει οριστεί telegram_chat_id στο config. Στείλε /start στο bot. "
            "Συνομιλίες που έγραψαν: " + (", ".join(strangers) or "καμία ακόμα"))
        return None
    if strangers:
        log(f"ℹ️  Μηνύματα από άλλους χρήστες στο bot (αγνοούνται): {', '.join(strangers)}")
    if state.get("_tg_hello") != tg.chat_id:          # νέα συνομιλία/ομάδα → ξανά καλωσόρισμα
        tg.notify("✅ Το bot συνδέθηκε!\n\nΚάθε νέα ανακοίνωση θα σου έρχεται εδώ με κουμπιά "
                  "✅ / ❌ και θα ανεβαίνει στο Instagram μόνο αν πατήσεις ✅.")
        state["_tg_hello"] = tg.chat_id
        state["_tg_kb"] = None                         # και ξανά τα κουμπιά

    srcs = {x["id"]: x for x in cfg["sources"]}
    usable = [x for x in cfg["sources"] if x.get("enabled", True) and (tokens is None or x["id"] in tokens)]
    button_to_sid = {compose.NEW + compose.short_name(x): x["id"] for x in usable}
    stories_default = cfg.get("settings", {}).get("stories", True)

    # --- μηνύματα: αλλαγές με reply, πρόχειρα νέων ανακοινώσεων ---------------
    by_msg = {p.get("msg"): pid for pid, p in pending.items() if p.get("msg")}
    hinted = False
    for m in msgs:
        to = (m.get("reply_to_message") or {}).get("message_id")
        text = (m.get("text") or "").strip()
        if to in by_msg and text:
            edit_pending(tg, pending, by_msg[to], text, srcs, by_msg)
            continue
        if compose.handle_message(tg, state, m, srcs, button_to_sid):
            continue
        if not hinted and not tg.live:
            tg.notify("✏️ Για αλλαγή σε ανακοίνωση: «Απάντηση» (reply) στο μήνυμα με την εικόνα της.\n\n"
                      + compose.HELP)
            hinted = True

    # --- ολοκληρωμένα πρόχειρα → εικόνες → προεπισκόπηση -----------------------
    for c in state.pop("_compose_done", []):
        src = srcs.get(c["sid"])
        if not src:
            continue
        try:
            entry = compose.build(tg, c, src, src.get("stories", stories_default))
        except Exception as e:
            log(f"⚠️  {c['sid']}: δεν φτιάχτηκε η δική σου ανακοίνωση: {e}")
            tg.notify(f"⚠️ Δεν φτιάχτηκε η ανακοίνωση «{c['title']}»: {e}")
            continue
        push_images([ROOT / x for x in entry["slides"]] + ([ROOT / entry["story"]] if entry["story"] else []))
        pid = _pid(entry["key"])
        entry["preview"] = preview_text(src, entry)
        if len(entry["slides"]) > 1:
            tg.send_album([ROOT / x for x in entry["slides"]])
        entry["msg"] = tg.send_preview(ROOT / entry["img"], entry["preview"], pid,
                                       compose.styles_for(entry), entry.get("style"))
        pending[pid] = entry
        log(f"📨 {c['sid']}: δική σου ανακοίνωση για έγκριση ({len(entry['slides'])} εικόνες): {c['title'][:60]}")
    for c in state.get("_composes", {}).values():
        if c.pop("changed", False) and not tg.live:
            tg.notify(compose.status_line(c, srcs))

    # --- ξαναστελνόμενες εκκρεμότητες (π.χ. μετά από αποτυχία) ----------------
    for pid, p in pending.items():
        if p.pop("resend", False) and p["sid"] in srcs:
            src = srcs[p["sid"]]
            p["preview"] = preview_text(src, p)
            if len(p.get("slides") or []) > 1:
                tg.send_album([ROOT / x for x in p["slides"]])
            p["msg"] = tg.send_preview(ROOT / p["img"], p["preview"], pid,
                                       compose.styles_for(p), p.get("style", "blue"))
            log(f"📨 {p['sid']}: ξαναστάλθηκε για έγκριση: {p['title'][:60]}")

    # --- 🔵 / ⚪ / 📸 αλλαγή στυλ --------------------------------------------------
    for pid, style in tg.styles.items():
        p = pending.get(pid)
        if not p or p.get("approved") or p["sid"] not in srcs:
            continue
        if style not in compose.styles_for(p) or style == p.get("style", "blue"):
            continue
        src = srcs[p["sid"]]
        try:
            if p.get("kind") == "custom":
                push_images(compose.render(src, p, style, bool(p.get("story"))))
            else:
                regen_images(src, p, style)
        except Exception as e:
            log(f"⚠️  {p['sid']}: δεν άλλαξε το στυλ ({e})")
            tg.notify(f"⚠️ Δεν άλλαξε το στυλ για «{p['title'][:60]}»: {e}")
            continue
        label = tg.STYLE_LABELS.get(style, style)
        tg.set_status(p["msg"], p["preview"], f"🎨 Άλλαξε σε {label} — δες τη νέα προεπισκόπηση")
        p["preview"] = preview_text(src, p)
        if len(p.get("slides") or []) > 1:
            tg.send_album([ROOT / x for x in p["slides"]])
        p["msg"] = tg.send_preview(ROOT / p["img"], p["preview"], pid, compose.styles_for(p), style)
        log(f"🎨 {p['sid']}: στυλ {style}{' από ' + tg.who.get(pid, '') if tg.who.get(pid) else ''}: {p['title'][:60]}")

    # --- ✅ / ❌ ----------------------------------------------------------------
    for pid, choice in decisions.items():
        p = pending.get(pid)
        if not p or p.get("approved"):
            continue
        by = tg.who.get(pid, "")
        p["by"] = by
        if choice == "y":
            p["approved"] = True
            log(f"👍 {p['sid']}: εγκρίθηκε{' από ' + by if by else ''}: {p['title'][:60]}")
        else:
            seen_bucket(state, p["sid"], p["key"])["seen"][p["key"]] = "rejected"
            tg.set_status(p["msg"], p["preview"], "❌ Δεν ανέβηκε (απορρίφθηκε" + (f" από {by})" if by else ")"))
            log(f"👎 {p['sid']}: απορρίφθηκε: {p['title'][:60]}")
            del pending[pid]

    # --- νέες ανακοινώσεις από τα site → προεπισκόπηση -------------------------
    for src, key, title, date, excerpt, link, img, story_img in new_jobs:
        pid = _pid(key)
        entry = {"sid": src["id"], "key": key, "title": title, "date": date.isoformat(),
                 "excerpt": excerpt, "link": link, "category": category_of.get(key, ""),
                 "img": str(img.relative_to(ROOT)),
                 "story": str(story_img.relative_to(ROOT)) if story_img else None}
        preview = preview_text(src, entry)
        try:
            msg = tg.send_preview(img, preview, pid, ["blue", "white"], "blue")
        except Exception as e:
            log(f"⚠️  {src['id']}: δεν στάλθηκε προεπισκόπηση στο Telegram ({e}), θα ξαναδοκιμάσει")
            continue
        state[src["id"]]["seen"][key] = "pending"
        pending[pid] = {**entry, "msg": msg, "preview": preview}
        log(f"📨 {src['id']}: στάλθηκε για έγκριση: {title[:60]}")

    compose.ensure_keyboard(tg, state, usable)

    # --- όσα έχουν εγκριθεί (τώρα ή νωρίτερα) και δεν ανέβηκαν ακόμα -------------
    ready = []
    for pid, p in pending.items():
        if p.get("approved") and p["sid"] in srcs:
            img = [ROOT / x for x in p["slides"]] if p.get("slides") else ROOT / p["img"]
            ready.append((srcs[p["sid"]], p["key"], p["title"], datetime.fromisoformat(p["date"]),
                          p["excerpt"], p["link"], img,
                          ROOT / p["story"] if p.get("story") else None))
    ready.sort(key=lambda j: j[3])          # με χρονολογική σειρά, όποια σειρά κι αν πατηθεί ✅
    waiting = sum(1 for p in pending.values() if not p.get("approved"))
    if waiting:
        log(f"⏳ {waiting} ανακοινώσεις περιμένουν την έγκρισή σου στο Telegram")
    return ready


def seen_bucket(state: dict, sid: str, key: str) -> dict:
    """Οι δικές σου ανακοινώσεις κρατιούνται χωριστά από αυτές των site, ώστε να μην
    επηρεάζουν την «πρώτη εκτέλεση» ενός τμήματος."""
    if key.startswith("custom:"):
        return state.setdefault("_custom", {"seen": {}, "attempts": {}})
    return state.setdefault(sid, {"seen": {}, "attempts": {}})


def edit_pending(tg, pending: dict, pid: str, text: str, srcs: dict, by_msg: dict) -> None:
    p = pending[pid]
    src = srcs.get(p["sid"])
    m = re.match(r"\s*(τίτλος|τιτλος|title|περίληψη|περιληψη|πρόταση|προταση|summary)\s*:\s*(.*)", text, re.S | re.I)
    if m and m.group(1).lower().startswith(("τ", "t")):
        what = "τίτλος"
        p["title"] = m.group(2).strip()   # (αν έχεις γράψει δική σου λεζάντα, μένει ίδια)
    elif m:
        if p.get("kind") == "custom" and p.get("style") != "photo":
            tg.notify("ℹ️ Πρόταση κάτω από τον τίτλο μπαίνει μόνο στο στυλ 📸 Φωτογραφία· "
                      "εδώ άλλαξε τη λεζάντα ή τον τίτλο.")
            return
        what = "περίληψη"
        val = m.group(2).strip()
        p["excerpt"] = "" if val in ("-", "—", "χωρίς", "καμία") else val
    else:
        what = "λεζάντα"
        p["caption"] = text.strip()[:2200]
    if what != "λεζάντα" and src:
        if p.get("kind") == "custom":
            paths = compose.regen_cover(src, p)
            if paths:
                push_images(paths)
        else:
            regen_images(src, p)
    tg.set_status(p["msg"], p["preview"], f"✏️ Άλλαξε ({what}) — δες τη νέα προεπισκόπηση")
    p["preview"] = preview_text(src or {"name": p["sid"]}, p)
    if p.get("kind") == "custom" and what == "τίτλος" and len(p.get("slides", [])) > 1:
        tg.send_album([ROOT / x for x in p["slides"]])
    p["msg"] = tg.send_preview(ROOT / p["img"], p["preview"], pid,
                               compose.styles_for(p), p.get("style", "blue"))
    by_msg[p["msg"]] = pid
    p.pop("approved", None)      # μετά από αλλαγή χρειάζεται νέο ✅
    log(f"✏️  {p['sid']}: άλλαξε {what}: {p['title'][:60]}")


def preview_text(src: dict, p: dict) -> str:
    cap = p.get("caption") or make_caption(src, p["title"], datetime.fromisoformat(p["date"]),
                                           p["excerpt"], p["link"])
    extra = "\n\n📱 + story" if p.get("story") else ""
    if len(p.get("slides") or []) > 1:
        extra = f"\n\n🖼 Καρουσέλ με {len(p['slides'])} εικόνες" + extra
    hint = "\n\n✏️ Reply εδώ για αλλαγή (κείμενο = λεζάντα, «τίτλος: …», «περίληψη: …» · στο 📸 «πρόταση: …»)"
    head = f"🏛 {src.get('name', '')}\n\n"
    room = 1024 - len(head) - len(extra) - len(hint)
    return head + (cap if len(cap) <= room else cap[:room - 1] + "…") + extra + hint


def regen_images(src: dict, p: dict, style: str | None = None) -> None:
    """Ξαναφτιάχνει εικόνα (και story) μετά από αλλαγή τίτλου/περίληψης και τις ανεβάζει."""
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    theme = src.get("theme", cfg.get("settings", {}).get("theme", "classic"))
    if style:
        p["style"] = style
    style = p.get("style", "blue")
    if theme == "dap":
        def render(*a, **k):
            return theme_dap.make_image(*a, style=style, **k)
    else:
        render = make_image
    date = datetime.fromisoformat(p["date"])
    v = p.get("v", 0) + 1                      # νέο όνομα αρχείου, αλλιώς το Instagram
    p["v"] = v                                 # μπορεί να πάρει την παλιά (cache) εικόνα
    base = Path(p["img"]).with_suffix("").name.split("_v")[0]
    img = render(src, p["title"], date, p["excerpt"], p.get("category", ""),
                 IMAGES_DIR / p["sid"] / f"{base}_v{v}.jpg")
    paths = [img]
    p["img"] = str(img.relative_to(ROOT))
    if p.get("story"):
        st = render(src, p["title"], date, p["excerpt"], p.get("category", ""),
                    IMAGES_DIR / p["sid"] / f"{base}_v{v}_story.jpg", story=True)
        p["story"] = str(st.relative_to(ROOT))
        paths.append(st)
    push_images(paths)


def recent_folded(recent: dict, sid: str) -> str:
    """Οι πρόσφατες λεζάντες «διπλωμένες» (χωρίς κενά/τόνους), για σύγκριση με τις δικές σου."""
    key = f"_folded_{sid}"
    if key not in recent:
        recent[key] = _fold(recent.get(sid, ""))
    return recent[key]


def done_pending(tg, state: dict, key: str, status: str) -> None:
    """Κλείνει μια εκκρεμότητα έγκρισης και ενημερώνει το μήνυμα στο Telegram."""
    if not tg:
        return
    pid = _pid(key)
    p = state.get("_pending", {}).pop(pid, None)
    if p:
        if p.get("by") and status.startswith("✅"):
            status += f" (εγκρίθηκε από {p['by']})"
        tg.set_status(p["msg"], p["preview"], status)


def parse_tokens(raw: str, ids: list[str]) -> dict:
    """Διαβάζει το IG_TOKENS. Δέχεται:
       {"mie": "EAA..."}            (κανονική μορφή)
       {“mie”: “EAA...”}            (έξυπνα εισαγωγικά του iPhone)
       mie: EAA...  /  mie=EAA...   (μία γραμμή ανά τμήμα)
    Δεν τυπώνει ποτέ το ίδιο το token."""
    raw = raw.strip()
    if not raw:
        return {}
    for a, b in (("“", '"'), ("”", '"'), ("„", '"'), ("«", '"'), ("»", '"'), ("‘", "'"), ("’", "'")):
        raw = raw.replace(a, b)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    out = {}
    for line in re.split(r"[\n,;{}]+", raw):
        m = re.match(r"""\s*["']?([a-z0-9_-]+)["']?\s*[:=]\s*["']?([A-Za-z0-9_.-]{20,})["']?\s*$""", line)
        if m and m.group(1) in ids:
            out[m.group(1)] = m.group(2)
    if out:
        return out
    kind = "σκέτο token χωρίς όνομα τμήματος" if re.fullmatch(r"(EAA|IGAA)\S+", raw) else "άγνωστη μορφή"
    raise SystemExit(f"❌ Το secret IG_TOKENS δεν διαβάζεται ({kind}). "
                     'Γράψ\' το έτσι: {"mie": "EAA..."}  ή απλά:  mie: EAA...')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="μόνο εικόνες, χωρίς Instagram/git")
    ap.add_argument("--only", help="τρέξε μόνο για αυτό το id")
    ap.add_argument("--feed-file", help="τοπικό αρχείο RSS αντί για το site (για δοκιμές)")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    st = cfg.get("settings", {})
    max_per_run = st.get("max_posts_per_run", 4)
    first_run_posts = st.get("first_run_posts", 0)
    max_attempts = st.get("max_attempts", 3)
    stories_default = st.get("stories", True)
    theme_default = st.get("theme", "classic")
    approval = st.get("approval", "none")          # "telegram" = έγκριση πριν ανέβει
    tg = None
    if approval == "telegram" and not args.dry_run:
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not tg_token:
            # Χωρίς bot δεν ανεβαίνει τίποτα (θέλουμε έγκριση) και δεν σημειώνεται τίποτα
            # ως γνωστό: όταν μπει το token, οι ανακοινώσεις θα σταλούν για έγκριση.
            log("⏸  Είναι ενεργή η έγκριση μέσω Telegram αλλά λείπει το secret "
                "TELEGRAM_BOT_TOKEN. Δεν γίνεται τίποτα μέχρι να μπει.")
            return 0
        tg = Telegram(tg_token, st.get("telegram_chat_id"), st.get("telegram_queue_url"))

    if os.environ.get("STORIES"):
        # Χειροκίνητο ανέβασμα εικόνων ως stories: STORIES="mie:path1,path2,..."
        # (π.χ. εξώφυλλα για highlights). Οι εικόνες πρέπει να υπάρχουν ήδη στο repo.
        sid, _, paths = os.environ["STORIES"].partition(":")
        toks = parse_tokens(os.environ.get("IG_TOKENS") or "", [x["id"] for x in cfg["sources"]])
        cred = toks.get(sid)
        if not cred:
            log(f"❌ δεν υπάρχει token για {sid}")
            return 1
        tok = cred if isinstance(cred, str) else cred["token"]
        for rel in [x.strip() for x in paths.split(",") if x.strip()]:
            try:
                log(f"📱 {sid}: story {rel} → {ig_publish(tok, raw_url(ROOT / rel), story=True)}")
            except Exception as e:
                log(f"❌ {sid}: story {rel}: {e}")
            time.sleep(5)
        return 0
    if os.environ.get("CHECK_TOKENS") == "1":
        for sid, cred in parse_tokens(os.environ.get("IG_TOKENS") or "", [x["id"] for x in cfg["sources"]]).items():
            tok = cred if isinstance(cred, str) else cred["token"]
            kind = f"{tok[:4]}… ({len(tok)} χαρακτήρες)"
            try:
                log(f"🔑 {sid}: token {kind} → λογαριασμός Instagram {ig_account_id(tok, None if isinstance(cred, str) else cred.get('ig_id'))}")
            except Exception as e:
                log(f"🔑 {sid}: token {kind} → ❌ {e}")
        return 0
    tokens = parse_tokens(os.environ.get("IG_TOKENS") or "", [s["id"] for s in cfg["sources"]])
    state = load_state()
    jobs = []  # (src, key, title, date, excerpt, link, image_path, story_path)
    category_of: dict[str, str] = {}
    had_error = False

    for src in cfg["sources"]:
        sid = src["id"]
        if not src.get("enabled", True) or (args.only and args.only != sid):
            continue
        if not args.dry_run and sid not in tokens:
            # Χωρίς token δεν κάνουμε τίποτα, ούτε καν την «πρώτη εκτέλεση»:
            # αυτή θα γίνει όταν προστεθεί το token του.
            log(f"·  {sid}: δεν υπάρχει ακόμα token στο IG_TOKENS, παράλειψη")
            continue
        try:
            entries = fetch_entries(src, args.feed_file)
        except Exception as e:  # ένα site που έπεσε δεν σταματάει τους υπόλοιπους
            log(f"⚠️  {sid}: {e}")
            had_error = True
            continue

        first_time = sid not in state
        if src.get("eclass") and not args.feed_file and not first_time:
            entries += fetch_eclass(src, state, cfg.get("settings", {}))
        s = state.setdefault(sid, {"seen": {}, "attempts": {}})
        new = [e for e in entries if item_key(e) not in s["seen"]]

        if first_time and not args.dry_run:
            keep = new[-first_run_posts:] if first_run_posts else []
            for e in new:
                if e not in keep:
                    s["seen"][item_key(e)] = "initial"
            log(f"🆕 {sid}: πρώτη εκτέλεση, {len(new) - len(keep)} παλιές σημειώθηκαν ως γνωστές. "
                f"Οι πιο πρόσφατες που βρέθηκαν:")
            for e in new[-3:][::-1]:
                log(f"      • {clean_text(e.get('title', ''))[:80]}")
            new = keep

        if not new:
            log(f"✓  {sid}: τίποτα καινούριο")
            continue
        if len(new) > max_per_run:
            log(f"…  {sid}: {len(new)} νέες, ανεβαίνουν οι {max_per_run} πρώτες τώρα, οι υπόλοιπες την επόμενη ώρα")
            new = new[:max_per_run]

        for e in new:
            key = item_key(e)
            title = clean_text(e.get("title", ""))
            excerpt = clean_excerpt(clean_text(e.get("summary", "")), title)
            cats = [t.get("term", "") for t in e.get("tags", [])
                    if t.get("term", "") not in ("Μη κατηγοριοποιημένο", "Uncategorized")]
            wanted = src.get("categories") or cats
            category = next((c for c in cats if c in wanted), cats[0] if cats else "")
            date = item_date(e)
            h = hashlib.sha1(key.encode()).hexdigest()[:12]
            render = theme_dap.make_image if src.get("theme", theme_default) == "dap" else make_image
            img = render(src, title, date, excerpt, category, IMAGES_DIR / sid / f"{h}.jpg")
            story_img = None
            if src.get("stories", stories_default):
                story_img = render(src, title, date, excerpt, category,
                                       IMAGES_DIR / sid / f"{h}_story.jpg", story=True)
            jobs.append((src, key, title, date, excerpt, e.get("link", ""), img, story_img))
            category_of[key] = category
            log(f"🖼  {sid}: {title[:70]}")

    if args.dry_run:
        log(f"Dry run: φτιάχτηκαν {len(jobs)} εικόνες στο {IMAGES_DIR}")
        return 0

    if jobs:
        push_images([p for j in jobs for p in j[-2:] if p])

    if tg:
        jobs = approval_round(tg, cfg, state, jobs, category_of, tokens)
        if jobs is None:          # δεν έχει οριστεί ακόμα σε ποια συνομιλία στέλνουμε
            save_state(state)
            return 0

    recent: dict[str, str] = {}
    for src, key, title, date, excerpt, link, img, story_img in jobs:
        sid = src["id"]
        s = seen_bucket(state, sid, key)
        token = tokens.get(sid)
        if not token:
            log(f"⚠️  {sid}: λείπει το token στο IG_TOKENS, παραλείπεται")
            had_error = True
            continue
        # Δικλίδα: αν το link υπάρχει ήδη σε λεζάντα του λογαριασμού, έχει ανέβει ήδη
        # (π.χ. χάθηκε το state). Το σημειώνουμε ως γνωστό και δεν το ξανανεβάζουμε.
        if sid not in recent:
            recent[sid] = ig_recent_captions(token)
        custom = state.get("_pending", {}).get(_pid(key), {}).get("caption")
        sig = _fold(custom)[:80] if custom else ""
        if (link and link in recent[sid]) or (sig and sig in recent_folded(recent, sid)):
            s["seen"][key] = "already-on-instagram"
            s["attempts"].pop(key, None)
            log(f"↩️  {sid}: υπάρχει ήδη στο Instagram, παράλειψη: {title[:60]}")
            done_pending(tg, state, key, "↩️ Υπήρχε ήδη στο Instagram")
            continue
        try:
            custom = state.get("_pending", {}).get(_pid(key), {}).get("caption")
            caption = custom or make_caption(src, title, date, excerpt, link)
            if isinstance(img, list) and len(img) > 1:
                media_id = ig_publish_carousel(token, [raw_url(x) for x in img], caption)
            else:
                media_id = ig_publish(token, raw_url(img[0] if isinstance(img, list) else img), caption)
            s["seen"][key] = datetime.now(ATHENS).isoformat(timespec="minutes")
            s["attempts"].pop(key, None)
            log(f"✅ {sid}: δημοσιεύτηκε ({media_id})")
            done_pending(tg, state, key, "✅ Ανέβηκε στο Instagram")
            if story_img:
                # Το story είναι «μπόνους»: αν αποτύχει, το post έχει ήδη ανέβει
                # και δεν το ξαναδοκιμάζουμε (αλλιώς θα διπλασιαζόταν το post).
                try:
                    time.sleep(5)
                    log(f"📱 {sid}: story ({ig_publish(token, raw_url(story_img), story=True)})")
                except Exception as se:
                    log(f"⚠️  {sid}: το post ανέβηκε, το story όχι: {se}")
        except Exception as e:
            n = s["attempts"].get(key, 0) + 1
            s["attempts"][key] = n
            log(f"❌ {sid}: απέτυχε ({n}/{max_attempts}): {e}")
            if n >= max_attempts:
                s["seen"][key] = "failed"
                s["attempts"].pop(key, None)
                done_pending(tg, state, key, f"⚠️ Απέτυχε να ανέβει: {e}")
            had_error = True
        time.sleep(5)

    save_state(state)
    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
