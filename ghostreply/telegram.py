"""Telegram-адаптер: long polling, отвечает вместо тебя в разрешённых чатах.

Токен: GHOST_TELEGRAM_TOKEN. Чаты: GHOST_TELEGRAM_ALLOWED_CHATS (id через запятую, пусто = все).
Себя/ботов пропускает, чтобы не было петли.
"""

import json
import time
import urllib.parse
import urllib.request
from typing import List, Optional

from .config import Config
from .engine import ReplyEngine
from .history import HistoryStore

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramAdapter:
    def __init__(self, cfg: Config, store: HistoryStore, engine: ReplyEngine):
        self.cfg = cfg
        self.store = store
        self.engine = engine
        self.offset = 0
        self.me_id: Optional[int] = None

    def _call(self, method: str, params: dict) -> dict:
        url = API.format(token=self.cfg.telegram_token, method=method)
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _allowed(self, chat_id: int) -> bool:
        if not self.cfg.telegram_allowed_chats:
            return True
        return str(chat_id) in self.cfg.telegram_allowed_chats

    def _get_updates(self) -> List[dict]:
        res = self._call("getUpdates", {
            "offset": self.offset,
            "timeout": 30,
            "allowed_updates": json.dumps(["message"]),
        })
        updates = res.get("result", [])
        for u in updates:
            self.offset = u["update_id"] + 1
        return updates

    def run(self) -> None:
        me = self._call("getMe", {})
        self.me_id = me["result"]["id"]
        print(f"[telegram] запущен как @{me['result']['username']}")
        while True:
            try:
                for upd in self._get_updates():
                    self._handle(upd.get("message") or {})
            except OSError as e:
                print(f"[telegram] сеть: {e}; повтор через 5 сек")
                time.sleep(5)

    def _handle(self, msg: dict) -> None:
        if not msg:
            return
        if msg.get("from", {}).get("is_bot"):
            return
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if not chat_id or not self._allowed(int(chat_id)):
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return

        self.store.append(chat_id, "peer", text, msg.get("date", 0))
        reply = self.engine.reply(chat_id, text)
        if self.cfg.mode == "auto":
            self.store.append(chat_id, "me", reply)
            self._call("sendMessage", {"chat_id": chat_id, "text": reply})
        else:
            # draft: показываем черновик в чате бота-управления самому себе
            self._call("sendMessage", {"chat_id": chat_id,
                                       "text": f"[черновик] {reply}"})
