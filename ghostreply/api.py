"""HTTP API + веб-интерфейс GhostReply.

Страницы:
  GET  /                          веб-интерфейс (static/index.html)
  GET  /health
API:
  GET    /chats                   список чатов
  GET    /chat/<id>               история чата
  DELETE /chat/<id>               удалить историю
  GET    /style/<id>              профиль стиля
  GET    /settings                настройки (без data_dir)
  POST   /settings                применить+сохранить настройки
  POST   /history                 {"chat_id", "messages":[{sender,text,ts?}]}
  POST   /message                 {"chat_id","text","sender":"peer"} -> {"reply","mode"}
  POST   /reply                   {"chat_id?","text"} — сгенерировать без записи
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Tuple
from urllib.parse import urlparse, unquote

from . import config as cfgmod
from .config import Config
from .engine import ReplyEngine
from .history import HistoryStore
from .status import StatusHub
from . import telegram as tgmod
from . import vk as vkmod

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon"}

_SETTINGS_FIELDS = ("mode", "llm_url", "llm_api_key", "llm_model",
                    "telegram_allowed_chats")


def make_handler(cfg: Config, store: HistoryStore, engine: ReplyEngine,
                 hub: StatusHub, adapters: dict):
    """adapters: {"telegram": TelegramAdapter, "vk": VKAdapter}"""
    class Handler(BaseHTTPRequestHandler):
        server_version = "GhostReply/0.1"

        def log_message(self, fmt, *args):
            pass  # тихо, чтобы консоль не засорялась

        # --- helpers -------------------------------------------------------
        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", code)

        def _body(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except (ValueError, json.JSONDecodeError):
                return {}

        def _static(self, rel: str) -> bool:
            path = os.path.normpath(os.path.join(STATIC_DIR, rel))
            if not path.startswith(STATIC_DIR) or not os.path.isfile(path):
                return False
            ext = os.path.splitext(path)[1].lower()
            with open(path, "rb") as f:
                self._send(f.read(), MIME.get(ext, "application/octet-stream"))
            return True

        # --- GET -----------------------------------------------------------
        def do_GET(self):
            path = unquote(urlparse(self.path).path)

            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path == "/health":
                return self._json({"ok": True})

            if path in ("/connections", "/events"):
                snap = hub.snapshot(log_limit=60 if path == "/events" else 0)
                conns = {}
                for name, ad in adapters.items():
                    st = snap["adapters"].get(name, {})
                    conns[name] = {
                        "running": bool(ad.running),
                        "configured": bool(
                            cfg.telegram_token if name == "telegram" else cfg.vk_token),
                        "link": (cfg.telegram_link if name == "telegram"
                                 else cfg.vk_link),
                        "error": st.get("error", ""),
                    }
                if path == "/connections":
                    return self._json({"connections": conns, "mode": cfg.mode})
                return self._json({"connections": conns, "events": snap["log"]})

            if path == "/chats":
                rows = []
                for cid in store.chats():
                    msgs = store.load(cid)
                    last = msgs[-1] if msgs else {}
                    rows.append({
                        "chat_id": cid,
                        "count": len(msgs),
                        "mine": sum(1 for m in msgs if m.get("sender") == "me"),
                        "last_text": (last.get("text") or "")[:80],
                        "last_ts": last.get("ts", 0),
                    })
                rows.sort(key=lambda r: r["last_ts"], reverse=True)
                return self._json({"chats": rows})

            if path == "/settings":
                d = {f: getattr(cfg, f) for f in _SETTINGS_FIELDS}
                d["llm_enabled"] = bool(cfg.llm_api_key)
                d["telegram_enabled"] = bool(cfg.telegram_token)
                return self._json(d)

            m = re.match(r"^/chat/([^/]+)$", path)
            if m:
                cid = m.group(1)
                return self._json({"chat_id": cid, "messages": store.load(cid)})

            m = re.match(r"^/style/([^/]+)$", path)
            if m:
                p = engine.profile(m.group(1))
                return self._json({"chat_id": m.group(1), "style": p.style_lines,
                                   "samples": p.samples(5)})

            if self._static(path.lstrip("/")):
                return
            self._json({"error": "not found"}, 404)

        # --- DELETE --------------------------------------------------------
        def do_DELETE(self):
            path = unquote(urlparse(self.path).path)
            m = re.match(r"^/chat/([^/]+)$", path)
            if m:
                store.delete(m.group(1))
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)

        # --- POST ----------------------------------------------------------
        def _do_connect(self, data: dict):
            name = str(data.get("messenger") or "")
            token = str(data.get("token") or "").strip()
            link = str(data.get("link") or "").strip()
            ad = adapters.get(name)
            if not ad:
                return self._json({"error": "неизвестный мессенджер"}, 400)

            saved = cfg.telegram_token if name == "telegram" else cfg.vk_token
            if token in ("", "••••••••", "******"):
                if not saved:
                    return self._json({"error": "нужен токен бота"}, 400)
                token = saved  # переподключение по уже сохранённому токену
            if not link:
                link = cfg.telegram_link if name == "telegram" else cfg.vk_link

            # проверка токена до сохранения
            try:
                if name == "telegram":
                    me = tgmod.validate_token(token)
                    who = "@" + str(me.get("username") or me.get("id"))
                else:
                    group_id = vkmod.parse_vk_link(link).get("group_id", "")
                    who = vkmod.validate_token(token, group_id)
                    cfg.vk_group_id = group_id
            except tgmod.TGError as e:
                return self._json({"error": f"Telegram: {e}"}, 400)
            except vkmod.VKError as e:
                return self._json({"error": str(e)}, 400)
            except OSError as e:
                return self._json({"error": f"сеть недоступна: {e}"}, 500)

            if name == "telegram":
                cfg.telegram_token, cfg.telegram_link = token, link
            else:
                cfg.vk_token, cfg.vk_link = token, link
            cfgmod.save_settings(cfg)
            started = ad.start()
            hub.event(name, "-", "out", f"подключено к {who}")
            return self._json({"ok": True, "running": started, "who": who})

        def do_POST(self):
            path = unquote(urlparse(self.path).path)
            data = self._body()
            chat_id = str(data.get("chat_id") or "").strip()
            text = str(data.get("text") or "").strip()

            if path == "/connect":
                return self._do_connect(data)

            if path == "/disconnect":
                name = str(data.get("messenger") or "")
                ad = adapters.get(name)
                if not ad:
                    return self._json({"error": "неизвестный мессенджер"}, 400)
                ad.stop()
                # токен храним — чтобы можно было переподключить одной кнопкой;
                # forget=true — вычистить совсем
                if data.get("forget"):
                    if name == "telegram":
                        cfg.telegram_token, cfg.telegram_link = "", ""
                    else:
                        cfg.vk_token, cfg.vk_link, cfg.vk_group_id = "", "", ""
                    cfgmod.save_settings(cfg)
                return self._json({"ok": True, "running": False})

            if path == "/settings":
                unknown = [k for k in data if k not in _SETTINGS_FIELDS]
                if unknown:
                    return self._json({"error": "неизвестные поля: " + ", ".join(unknown)}, 400)
                if "mode" in data and data["mode"] not in ("auto", "draft"):
                    return self._json({"error": "mode: только auto или draft"}, 400)
                for k in _SETTINGS_FIELDS:
                    if k in data:
                        setattr(cfg, k, data[k])
                try:
                    cfgmod.save_settings(cfg)
                except OSError as e:
                    return self._json({"error": f"не сохранилось: {e}"}, 500)
                return self._json({"ok": True})

            if path == "/history":
                if not chat_id or not isinstance(data.get("messages"), list):
                    return self._json({"error": "chat_id и messages обязательны"}, 400)
                try:
                    n = store.import_messages(chat_id, data["messages"])
                except (TypeError, ValueError) as e:
                    return self._json({"error": f"плохой формат: {e}"}, 400)
                return self._json({"ok": True, "imported": n})

            if path == "/reply":
                if not text:
                    return self._json({"error": "text обязателен"}, 400)
                return self._json({"reply": engine.reply(chat_id or "_adhoc", text),
                                   "mode": "draft"})

            if path == "/message":
                if not chat_id or not text:
                    return self._json({"error": "chat_id и text обязательны"}, 400)
                sender = "me" if data.get("sender") == "me" else "peer"
                store.append(chat_id, sender, text)
                if sender == "me":
                    return self._json({"ok": True, "reply": None, "mode": cfg.mode})
                reply = engine.reply(chat_id, text)
                if not reply:
                    return self._json({"error": "не удалось сгенерировать ответ"}, 502)
                if cfg.mode == "auto":
                    store.append(chat_id, "me", reply)
                return self._json({"reply": reply, "mode": cfg.mode})

            self._json({"error": "not found"}, 404)

    return Handler


def serve(cfg: Config, store: HistoryStore, engine: ReplyEngine,
          hub: StatusHub, adapters: dict) -> Tuple[ThreadingHTTPServer, Thread]:
    httpd = ThreadingHTTPServer((cfg.http_host, cfg.http_port),
                                make_handler(cfg, store, engine, hub, adapters))
    t = Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t
