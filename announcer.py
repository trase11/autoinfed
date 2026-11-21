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


def make_image(src: dict, title: str, date: datetime, excerpt: str, category: str, out: Path) -> Path:
    color = _hex(src.get("color", "#0b3d91"))
    img = Image.new("RGB", (W, H), (247, 246, 242))
    d = ImageDraw.Draw(img)
    maxw = W - 2 * PAD

    # Πάνω μπάντα
    band_h = 230
    d.rectangle([0, 0, W, band_h], fill=color)
    d.text((PAD, 62), "ΑΝΑΚΟΙΝΩΣΗ", font=ImageFont.truetype(FONT_BOLD, 30), fill=(255, 255, 255, 200))
    name_font = ImageFont.truetype(FONT_BOLD, 46)
    name = src["name"]
    while d.textlength(name, font=name_font) > maxw and name_font.size > 28:
        name_font = ImageFont.truetype(FONT_BOLD, name_font.size - 2)
    d.text((PAD, 112), name, font=name_font, fill=(255, 255, 255))

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
    footer_y = H - 130
    size = 72
    while True:
        tf = ImageFont.truetype(FONT_BOLD, size)
        lines = _wrap(d, title, tf, maxw)
        line_h = int(size * 1.22)
        if len(lines) <= 7 or size <= 40:
            break
        size -= 4
    if len(lines) > 7:
        lines = lines[:7]
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
        room = min(room, 6)
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
    domain = re.sub(r"^https?://([^/?]+).*$", r"\1", src.get("feed", ""))
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
    if given:
        return str(given)
    if token.startswith("IGAA"):
        return ig_call("GET", "me", token, fields="user_id")["user_id"]
    try:
        accts = ig_call("GET", "me/assigned_instagram_accounts", token, fields="id,username").get("data", [])
        if len(accts) == 1:
            return accts[0]["id"]
    except InstagramError:
        pass
    raise InstagramError("Δεν βρέθηκε αυτόματα ο λογαριασμός Instagram αυτού του token. "
                         "Βάλε στο IG_TOKENS τη μορφή {\"token\": \"...\", \"ig_id\": \"...\"}")


def ig_publish(cred, image_url: str, caption: str) -> str:
    token, given_id = (cred, None) if isinstance(cred, str) else (cred["token"], cred.get("ig_id"))
    uid = ig_account_id(token, given_id)
    container = ig_call("POST", f"{uid}/media", token, image_url=image_url, caption=caption)["id"]
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

    tokens = json.loads(os.environ.get("IG_TOKENS") or "{}")
    state = load_state()
    jobs = []  # (src, key, title, date, excerpt, link, image_path)
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
            excerpt = clean_text(e.get("summary", ""))
            if excerpt.startswith(title):
                excerpt = excerpt[len(title):].strip()
            cats = [t.get("term", "") for t in e.get("tags", [])
                    if t.get("term", "") not in ("Μη κατηγοριοποιημένο", "Uncategorized")]
            wanted = src.get("categories") or cats
            category = next((c for c in cats if c in wanted), cats[0] if cats else "")
            date = item_date(e)
            h = hashlib.sha1(key.encode()).hexdigest()[:12]
            img = make_image(src, title, date, excerpt, category, IMAGES_DIR / sid / f"{h}.jpg")
            jobs.append((src, key, title, date, excerpt, e.get("link", ""), img))
            log(f"🖼  {sid}: {title[:70]}")

    if args.dry_run:
        log(f"Dry run: φτιάχτηκαν {len(jobs)} εικόνες στο {IMAGES_DIR}")
        return 0

    if jobs:
        push_images([j[-1] for j in jobs])

    for src, key, title, date, excerpt, link, img in jobs:
        sid = src["id"]
        s = state[sid]
        token = tokens.get(sid)
        if not token:
            log(f"⚠️  {sid}: λείπει το token στο IG_TOKENS, παραλείπεται")
            had_error = True
            continue
        try:
            media_id = ig_publish(token, raw_url(img), make_caption(src, title, date, excerpt, link))
            s["seen"][key] = datetime.now(ATHENS).isoformat(timespec="minutes")
            s["attempts"].pop(key, None)
            log(f"✅ {sid}: δημοσιεύτηκε ({media_id})")
        except Exception as e:
            n = s["attempts"].get(key, 0) + 1
            s["attempts"][key] = n
            log(f"❌ {sid}: απέτυχε ({n}/{max_attempts}): {e}")
            if n >= max_attempts:
                s["seen"][key] = "failed"
                s["attempts"].pop(key, None)
            had_error = True
        time.sleep(5)

    save_state(state)
    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
