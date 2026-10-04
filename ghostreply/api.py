"""HTTP API: подключение любого мессенджера к GhostReply.

Эндпоинты:
  POST /history        {"chat_id": "...", "messages": [{"sender": "me"|"peer", "text": "..."}]}
  POST /message        {"chat_id": "...", "text": "...", "sender": "peer"}  -> {"reply": "...", "mode": "auto"|"draft"}
  POST /reply          {"chat_id": "...", "text": "..."}   ответ без сохранения входа (просто сгенерировать)
  GET  /style/<chat_id>                                       профиль стиля
  GET  /health
"""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Tuple
from urllib.parse import urlparse

from .config import Config
from .engine import ReplyEngine
from .history import HistoryStore


def make_handler(cfg: Config, store: HistoryStore, engine: ReplyEngine):
    class Handler(BaseHTTPRequestHandler):
        server_version = "GhostReply/0.1"

        def log_message(self, fmt, *args):  # тихий лог
            if cfg.http_host not in ("127.0.0.1", "localhost"):
                super().log_message(fmt, *args)

        def _json(self, obj, code: int = 200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except (ValueError, json.JSONDecodeError):
                return {}

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                return self._json({"ok": True})
            m = re.match(r"^/style/([^/]+)$", path)
            if m:
                p = engine.profile(m.group(1))
                return self._json({"chat_id": m.group(1), "style": p.style_lines,
                                   "samples": p.samples(5)})
            self._json({"error": "not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            data = self._body()
            chat_id = str(data.get("chat_id") or "").strip()
            text = str(data.get("text") or "").strip()

            if path == "/history":
                if not chat_id or not isinstance(data.get("messages"), list):
                    return self._json({"error": "chat_id и messages обязательны"}, 400)
                n = store.import_messages(chat_id, data["messages"])
                return self._json({"ok": True, "imported": n})

            if path == "/reply":
                if not text:
                    return self._json({"error": "text обязателен"}, 400)
                chat_id = chat_id or "_adhoc"
                return self._json({"reply": engine.reply(chat_id, text),
                                   "mode": "draft"})

            if path == "/message":
                if not chat_id or not text:
                    return self._json({"error": "chat_id и text обязательны"}, 400)
                sender = data.get("sender", "peer")
                store.append(chat_id, sender, text)
                if sender == "me":
                    return self._json({"ok": True, "reply": None, "mode": cfg.mode})
                reply = engine.reply(chat_id, text)
                if cfg.mode == "auto":
                    store.append(chat_id, "me", reply)
                return self._json({"reply": reply, "mode": cfg.mode})

            self._json({"error": "not found"}, 404)

    return Handler


def serve(cfg: Config, store: HistoryStore, engine: ReplyEngine) -> Tuple[ThreadingHTTPServer, Thread]:
    httpd = ThreadingHTTPServer((cfg.http_host, cfg.http_port), make_handler(cfg, store, engine))
    t = Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t
