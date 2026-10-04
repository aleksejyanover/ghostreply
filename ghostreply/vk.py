"""Адаптер VK: бот сообщества отвечает в VK-диалогах (Bots Long Poll API).

Токен: токен сообщества с правом messages (настройки сообщества → Сообщения).
Ссылку на сообщество можно указать в интерфейсе — сохранится для справки.
"""

import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

from .config import Config
from .engine import ReplyEngine
from .history import HistoryStore
from .status import StatusHub

API = "https://api.vk.com/method/{method}"
V = "5.131"


class VKError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(f"VK error {code}: {msg}")
        self.code = code
        self.msg = msg


def api_call(method: str, token: str, params: Optional[Dict] = None,
             timeout: int = 20) -> dict:
    q = {"v": V, "access_token": token}
    q.update(params or {})
    url = API.format(method=method) + "?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    if "error" in data:
        e = data["error"]
        raise VKError(e.get("error_code", 0), e.get("error_msg", "?"))
    return data.get("response", {})


def validate_token(token: str, group_id: str = "") -> str:
    """Проверка токена: возвращает имя сообщества или кидает VKError/URLError."""
    params = {"fields": "screen_name"} if not group_id else {"group_id": group_id}
    resp = api_call("groups.getById", token, params)
    groups = resp if isinstance(resp, list) else resp.get("groups", [])
    if not groups:
        raise VKError(-1, "сообщество не найдено")
    g = groups[0]
    return g.get("name") or g.get("screen_name") or "сообщество"


def parse_vk_link(text: str) -> Dict:
    """Извлечь group_id из ссылки вида https://vk.com/club123 / vk.com/имя."""
    import re
    out = {}
    m = re.search(r"vk\.(?:com|me)/(?:club|event|public)(\d+)", text, re.I)
    if m:
        out["group_id"] = m.group(1)
    m = re.search(r"https?://vk\.com/([A-Za-z0-9_.]+)", text, re.I)
    if m and m.group(1) not in ("im", "feed", "friends", "photos", "video"):
        out["link"] = "https://vk.com/" + m.group(1)
    elif "vk.com" in text.lower():
        out["link"] = text.strip()
    return out


class VKAdapter:
    def __init__(self, cfg: Config, store: HistoryStore, engine: ReplyEngine,
                 hub: StatusHub):
        self.cfg = cfg
        self.store = store
        self.engine = engine
        self.hub = hub
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- lifecycle ---------------------------------------------------------
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        if self.running:
            return True
        if not self.cfg.vk_token:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="vk-adapter")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self.hub.set("vk", running=False, configured=bool(self.cfg.vk_token))

    # --- main loop ---------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                name = validate_token(self.cfg.vk_token, self.cfg.vk_group_id)
                self.hub.set("vk", running=True, configured=True)
                self.hub.event("vk", "-", "out", f"подключено: {name}")
                self._poll_loop()
            except (VKError, OSError) as e:
                self.hub.set("vk", running=False, error=str(e), configured=True)
                self.hub.event("vk", "-", "error", str(e))
            except OSError as e:
                self.hub.set("vk", running=False, error=f"сеть: {e}",
                             configured=True)
                self.hub.event("vk", "-", "error", f"сеть: {e}")
            if not self._stop.is_set():
                self._stop.wait(5)  # пауза перед переподключением

    def _poll_loop(self) -> None:
        srv = api_call("groups.getLongPollServer", self.cfg.vk_token,
                       {"group_id": self.cfg.vk_group_id} if self.cfg.vk_group_id else None)
        key, ts, server = srv["key"], srv["ts"], srv["server"]
        while not self._stop.is_set():
            q = urllib.parse.urlencode({"act": "a_check", "key": key, "ts": ts,
                                        "wait": 25})
            try:
                with urllib.request.urlopen(server + "?" + q, timeout=40) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(1)
                continue

            if "failed" in data:
                code = data["failed"]
                if code == 2:  # ключ устарел — новый сервер
                    srv = api_call("groups.getLongPollServer", self.cfg.vk_token,
                                   {"group_id": self.cfg.vk_group_id} if self.cfg.vk_group_id else None)
                    key, ts, server = srv["key"], srv["ts"], srv["server"]
                else:
                    ts = data.get("ts", ts)
                continue

            ts = data.get("ts", ts)
            for upd in data.get("updates", []):
                if upd.get("type") == "message_new":
                    self._handle(upd.get("object") or {})

    # --- обработка ---------------------------------------------------------
    def _handle(self, obj: dict) -> None:
        msg = obj.get("message", obj)  # формат v5.131
        text = (msg.get("text") or "").strip()
        peer = msg.get("peer_id")
        if not text or not peer:
            return
        if msg.get("from_id") in (0, self.cfg.vk_group_id and -int(self.cfg.vk_group_id)):
            return  # служебные/свои

        chat_id = f"vk:{peer}"
        self.hub.event("vk", chat_id, "in", text)
        self.store.append(chat_id, "peer", text, msg.get("date", time.time()))

        reply = self.engine.reply(chat_id, text)
        if not reply:
            return
        if self.cfg.mode != "auto":
            self.hub.event("vk", chat_id, "draft", reply)
            return

        self._typing(peer)
        self.store.append(chat_id, "me", reply)
        api_call("messages.send", self.cfg.vk_token, {
            "peer_id": peer,
            "message": reply,
            "random_id": random.randint(1, 2 ** 31 - 1),
        })
        self.hub.event("vk", chat_id, "out", reply)

    def _typing(self, peer: int) -> None:
        """Реализм: показываем «печатает…» и немного ждём, прежде чем ответить."""
        try:
            api_call("messages.setActivity", self.cfg.vk_token,
                     {"peer_id": peer, "type": "typing"}, timeout=10)
        except (VKError, OSError):
            pass
        time.sleep(random.uniform(1.0, 2.5))
