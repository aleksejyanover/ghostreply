"""Telegram-адаптер: бот общается в Telegram вместо тебя (long polling)."""

import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from .config import Config
from .engine import ReplyEngine
from .history import HistoryStore
from .status import StatusHub

API = "https://api.telegram.org/bot{token}/{method}"


class TGError(Exception):
    pass


def _call(token: str, method: str, params: Optional[Dict] = None,
          timeout: int = 60) -> dict:
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    if not out.get("ok"):
        raise TGError(out.get("description") or "Telegram API error")
    return out


def validate_token(token: str) -> Dict:
    """→ результат getMe (id, username ...) или TGError."""
    res = _call(token, "getMe", timeout=15)
    return res["result"]


class TelegramAdapter:
    def __init__(self, cfg: Config, store: HistoryStore, engine: ReplyEngine,
                 hub: StatusHub):
        self.cfg = cfg
        self.store = store
        self.engine = engine
        self.hub = hub
        self.offset = 0
        self.me_id: Optional[int] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- lifecycle ---------------------------------------------------------
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        if self.running:
            return True
        if not self.cfg.telegram_token:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="tg-adapter")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self.hub.set("telegram", running=False,
                     configured=bool(self.cfg.telegram_token))

    # --- loop --------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                me = validate_token(self.cfg.telegram_token)
                self.me_id = me.get("id")
                self.hub.set("telegram", running=True, configured=True)
                self.hub.event("telegram", "-", "out",
                               f"подключено: @{me.get('username', '?')}")
                while not self._stop.is_set():
                    for upd in self._updates():
                        self._handle(upd.get("message") or {})
            except TGError as e:
                self.hub.set("telegram", running=False, error=str(e),
                             configured=True)
                self.hub.event("telegram", "-", "error", str(e))
                return  # неверный токен — переподключение бессмысленно
            except OSError as e:
                self.hub.set("telegram", running=False, error=f"сеть: {e}",
                             configured=True)
                self.hub.event("telegram", "-", "error", f"сеть: {e}")
            if not self._stop.is_set():
                self._stop.wait(5)

    def _updates(self) -> List[dict]:
        res = _call(self.cfg.telegram_token, "getUpdates", {
            "offset": self.offset, "timeout": 30,
            "allowed_updates": json.dumps(["message"]),
        }, timeout=60)
        updates = res.get("result", [])
        for u in updates:
            self.offset = u["update_id"] + 1
        return updates

    def _allowed(self, chat_id: int) -> bool:
        if not self.cfg.telegram_allowed_chats:
            return True
        return str(chat_id) in self.cfg.telegram_allowed_chats

    # --- обработка ---------------------------------------------------------
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

        tg = f"tg:{chat_id}"
        self.hub.event("telegram", tg, "in", text)
        self.store.append(tg, "peer", text, msg.get("date", time.time()))

        reply = self.engine.reply(tg, text)
        if not reply:
            return
        if self.cfg.mode != "auto":
            self.hub.event("telegram", tg, "draft", reply)
            return

        self._typing(chat_id)
        self.store.append(tg, "me", reply)
        _call(self.cfg.telegram_token, "sendMessage",
              {"chat_id": chat_id, "text": reply}, timeout=30)
        self.hub.event("telegram", tg, "out", reply)

    def _typing(self, chat_id: str) -> None:
        """Реализм: sendChatAction «печатает…» и пауза перед отправкой."""
        try:
            _call(self.cfg.telegram_token, "sendChatAction",
                  {"chat_id": chat_id, "action": "typing"}, timeout=15)
        except (TGError, OSError):
            pass
        time.sleep(random.uniform(1.0, 2.5))
