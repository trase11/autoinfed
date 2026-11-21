"""Έγκριση posts μέσω Telegram.

Κάθε νέα ανακοίνωση στέλνεται στο Telegram ως προεπισκόπηση (εικόνα + λεζάντα)
με κουμπιά ✅ Ναι / ❌ Όχι. Το πάτημα καταγράφεται από το Telegram και το
διαβάζουμε στον επόμενο έλεγχο (getUpdates), οπότε δεν χρειάζεται server.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: int | str | None):
        self.token = token
        self.chat_id = str(chat_id) if chat_id else None

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
    def send_preview(self, image: Path, caption: str, pid: str) -> int:
        keyboard = {"inline_keyboard": [[
            {"text": "✅ Ναι, ανέβασέ το", "callback_data": f"y:{pid}"},
            {"text": "❌ Όχι", "callback_data": f"n:{pid}"},
        ]]}
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
    def read_updates(self, offset: int):
        """Επιστρέφει ({pid: 'y'|'n'}, νέο offset, άγνωστες συνομιλίες, απαντήσεις).
        απαντήσεις = [(message_id στο οποίο απάντησε, κείμενο)] — για επεξεργασία."""
        updates = self._call("getUpdates", offset=offset + 1 if offset else 0, timeout=0,
                             allowed_updates=["callback_query", "message"])
        decisions: dict[str, str] = {}
        strangers: list[str] = []
        replies: list[tuple[int | None, str]] = []
        new_offset = offset
        for u in updates:
            new_offset = max(new_offset, u["update_id"])
            cq = u.get("callback_query")
            msg = u.get("message")
            if cq:
                chat = str(cq.get("message", {}).get("chat", {}).get("id", ""))
                if self.chat_id and chat == self.chat_id and ":" in cq.get("data", ""):
                    choice, pid = cq["data"].split(":", 1)
                    decisions[pid] = choice
                    try:
                        self._call("answerCallbackQuery", callback_query_id=cq["id"],
                                   text="✅ Θα ανέβει σε λίγα λεπτά" if choice == "y" else "❌ Δεν θα ανέβει")
                    except TelegramError:
                        pass
            elif msg:
                chat = msg.get("chat", {})
                if str(chat.get("id")) == self.chat_id and msg.get("text") and not msg["text"].startswith("/"):
                    to = (msg.get("reply_to_message") or {}).get("message_id")
                    replies.append((to, msg["text"]))
                elif str(chat.get("id")) != self.chat_id:
                    who = chat.get("username") or chat.get("first_name") or "?"
                    strangers.append(f"{chat.get('id')} ({who})")
        return decisions, new_offset, strangers, replies
