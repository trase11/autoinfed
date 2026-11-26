"""Έγκριση posts μέσω Telegram.

Κάθε νέα ανακοίνωση στέλνεται στο Telegram ως προεπισκόπηση (εικόνα + λεζάντα)
με κουμπιά ✅ Ναι / ❌ Όχι. Το πάτημα καταγράφεται από το Telegram και το
διαβάζουμε στον επόμενο έλεγχο (getUpdates), οπότε δεν χρειάζεται server.
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: int | str | None, queue_url: str | None = None,
                 extra_chats: list | None = None):
        self.token = token
        self.chat_id = str(chat_id) if chat_id else None
        self.main_chat = self.chat_id
        # Όλες οι ομάδες που ακούει το bot: η κεντρική + μία ανά τμήμα (αν έχει δική του)
        self.chats = {c for c in [self.chat_id, *[str(x) for x in (extra_chats or []) if x]] if c}
        self.chat_of: dict[str, str] = {}  # pid → ομάδα από την οποία ήρθε το ✅/❌/στυλ
        # Με Cloudflare Worker: τα μηνύματα έρχονται από την ουρά του Worker (webhook),
        # και τις άμεσες απαντήσεις στον χρήστη τις δίνει ο Worker («live» λειτουργία).
        self.queue_url = (queue_url or "").rstrip("/") or None
        self.live = bool(self.queue_url)
        self.who: dict[str, str] = {}      # pid → όνομα όποιου πάτησε ✅/❌ (για ομάδες)
        self.styles: dict[str, str] = {}   # pid → στυλ που διάλεξε κάποιος (blue/white/photo)

    @contextmanager
    def to(self, chat):
        """Ό,τι στέλνεται μέσα στο with πάει στη συγκεκριμένη ομάδα."""
        prev = self.chat_id
        self.chat_id = str(chat) if chat else self.main_chat
        try:
            yield self
        finally:
            self.chat_id = prev

    def _queue_key(self) -> str:
        return hashlib.sha256(f"autoinfed-queue:{self.token}".encode()).hexdigest()

    def register_chats(self) -> None:
        """Λέει στον Worker ποιες ομάδες είναι δικές μας (για τις άμεσες απαντήσεις)."""
        if not self.queue_url:
            return
        try:
            requests.post(f"{self.queue_url}/chats", json=sorted(self.chats), timeout=20,
                          headers={"Authorization": f"Bearer {self._queue_key()}"})
        except requests.RequestException:
            pass

    def _fetch_updates(self, offset: int, wait_for: int = 0) -> list[dict]:
        if not self.queue_url:
            return self._call("getUpdates", offset=offset + 1 if offset else 0, timeout=0,
                              allowed_updates=["callback_query", "message"])
        key = self._queue_key()
        deadline = time.time() + 90
        while True:
            r = requests.get(f"{self.queue_url}/updates", params={"after": offset},
                             headers={"Authorization": f"Bearer {key}"}, timeout=60)
            if r.status_code != 200:
                raise TelegramError(f"ουρά Worker: HTTP {r.status_code}")
            ups = r.json()
            # Το KV του Cloudflare μπορεί να αργήσει λίγα δευτερόλεπτα να «δει» το
            # τελευταίο μήνυμα· περιμένουμε αυτό που ξεκίνησε τη δουλειά.
            if not wait_for or any(u["update_id"] >= wait_for for u in ups) or time.time() > deadline:
                return ups
            time.sleep(5)

    def _call(self, method: str, files=None, **params):
        url = API.format(token=self.token, method=method)
        if files:
            r = requests.post(url, data=params, files=files, timeout=60)
        else:
            r = requests.post(url, json=params, timeout=60)
        data = r.json() if r.content else {}
        if not data.get("ok"):
            raise TelegramError(data.get("description", r.text[:200]))
        return data["result"]

    # ---- αποστολή προεπισκόπησης -------------------------------------------
    STYLE_LABELS = {"blue": "🔵 Μπλε", "white": "⚪ Άσπρο", "photo": "📸 Φωτογραφία"}

    def send_preview(self, image: Path, caption: str, pid: str,
                     styles: list[str] | None = None, current: str | None = None) -> int:
        rows = [[
            {"text": "✅ Ναι, ανέβασέ το", "callback_data": f"y:{pid}"},
            {"text": "❌ Όχι", "callback_data": f"n:{pid}"},
        ]]
        if styles:
            rows.append([{"text": ("• " if st == current else "") + self.STYLE_LABELS[st],
                          "callback_data": f"s:{st}:{pid}"} for st in styles])
        keyboard = {"inline_keyboard": rows}
        with open(image, "rb") as fh:
            msg = self._call("sendPhoto", files={"photo": fh},
                             chat_id=self.chat_id, caption=caption[:1024],
                             reply_markup=json.dumps(keyboard))
        return msg["message_id"]

    def set_status(self, message_id: int, caption: str, status: str) -> None:
        """Αλλάζει τη λεζάντα του μηνύματος προεπισκόπησης και αφαιρεί τα κουμπιά."""
        try:
            self._call("editMessageCaption", chat_id=self.chat_id, message_id=message_id,
                       caption=f"{status}\n\n{caption}"[:1024],
                       reply_markup={"inline_keyboard": []})
        except TelegramError:
            pass  # π.χ. πολύ παλιό μήνυμα· δεν είναι κρίσιμο

    def notify(self, text: str) -> None:
        try:
            self._call("sendMessage", chat_id=self.chat_id, text=text[:4000])
        except TelegramError:
            pass

    # ---- ανάγνωση απαντήσεων -------------------------------------------------
    def read_updates(self, offset: int, wait_for: int = 0):
        """Επιστρέφει ({pid: 'y'|'n'}, νέο offset, άγνωστες συνομιλίες, απαντήσεις).
        απαντήσεις = όλα τα μηνύματα της δικής σου συνομιλίας (dict του Telegram), με τη σειρά."""
        updates = self._fetch_updates(offset, wait_for)
        decisions: dict[str, str] = {}
        strangers: list[str] = []
        replies: list[dict] = []
        new_offset = offset
        for u in updates:
            new_offset = max(new_offset, u["update_id"])
            cq = u.get("callback_query")
            msg = u.get("message")
            if cq:
                chat = str(cq.get("message", {}).get("chat", {}).get("id", ""))
                if chat in self.chats and ":" in cq.get("data", ""):
                    choice, pid = cq["data"].split(":", 1)
                    if choice == "s":                       # αλλαγή στυλ: s:<στυλ>:<pid>
                        st, pid = pid.split(":", 1)
                        self.styles[pid] = st
                        self.chat_of[pid] = chat
                        if not self.live:
                            try:
                                self._call("answerCallbackQuery", callback_query_id=cq["id"],
                                           text="🎨 Φτιάχνω τη νέα εκδοχή…")
                            except TelegramError:
                                pass
                        continue
                    decisions[pid] = choice
                    self.chat_of[pid] = chat
                    self.who[pid] = (cq.get("from") or {}).get("first_name", "")
                    if self.live:          # την άμεση απάντηση την έδωσε ήδη ο Worker
                        continue
                    try:
                        self._call("answerCallbackQuery", callback_query_id=cq["id"],
                                   text="✅ Θα ανέβει σε λίγα λεπτά" if choice == "y" else "❌ Δεν θα ανέβει")
                    except TelegramError:
                        pass
            elif msg:
                chat = msg.get("chat", {})
                if str(chat.get("id")) in self.chats:
                    replies.append(msg)          # όλα τα δικά σου μηνύματα, με τη σειρά
                else:
                    who = chat.get("username") or chat.get("first_name") or "?"
                    strangers.append(f"{chat.get('id')} ({who})")
        return decisions, new_offset, strangers, replies

    # ---- βοηθητικά για σύνθεση ανακοινώσεων ----------------------------------
    def send_keyboard(self, text: str, rows: list[list[str]]) -> None:
        """Μόνιμα κουμπιά κάτω από το πεδίο γραφής (στέλνουν απλώς το κείμενό τους)."""
        kb = {"keyboard": [[{"text": t} for t in row] for row in rows],
              "resize_keyboard": True, "is_persistent": True}
        self._call("sendMessage", chat_id=self.chat_id, text=text, reply_markup=kb)

    def download(self, file_id: str, dest: Path) -> Path:
        info = self._call("getFile", file_id=file_id)
        url = f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}"
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest

    def send_album(self, images: list[Path]) -> None:
        """Δείχνει όλες τις σελίδες του καρουσέλ (έως 10) ως άλμπουμ."""
        files, media = {}, []
        for i, p in enumerate(images[:10]):
            files[f"f{i}"] = open(p, "rb")
            media.append({"type": "photo", "media": f"attach://f{i}"})
        try:
            self._call("sendMediaGroup", files=files, chat_id=self.chat_id, media=json.dumps(media))
        finally:
            for fh in files.values():
                fh.close()
