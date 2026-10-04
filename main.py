#!/usr/bin/env python3
"""GhostReply: HTTP API + (опционально) Telegram. Запуск: python3 main.py"""

import argparse
import sys

from ghostreply import api, config
from ghostreply.engine import ReplyEngine
from ghostreply.history import HistoryStore
from ghostreply.telegram import TelegramAdapter


def main() -> int:
    ap = argparse.ArgumentParser(description="GhostReply — пишет вместо тебя")
    ap.add_argument("--config", default="", help="путь к config.json")
    ap.add_argument("--host", default="", help="адрес HTTP API")
    ap.add_argument("--port", type=int, default=0, help="порт HTTP API")
    ap.add_argument("--telegram", action="store_true", help="запустить Telegram-адаптер")
    ap.add_argument("--once", metavar="CHAT:TEXT", help="сгенерировать один ответ и выйти")
    args = ap.parse_args()

    cfg = config.load(args.config)
    if args.host:
        cfg.http_host = args.host
    if args.port:
        cfg.http_port = args.port

    store = HistoryStore(cfg.data_dir)
    engine = ReplyEngine(cfg, store)

    if args.once:
        chat_id, _, text = args.once.partition(":")
        if not chat_id or not text:
            print("--once формат: CHAT:ТЕКСТ", file=sys.stderr)
            return 2
        print(engine.reply(chat_id, text))
        return 0

    httpd, _ = api.serve(cfg, store, engine)
    print(f"[http] {cfg.http_host}:{cfg.http_port}  режим={cfg.mode}  "
          f"llm={'да' if cfg.llm_api_key else 'нет (локальный фолбэк)'}")

    if args.telegram or cfg.telegram_token:
        if not cfg.telegram_token:
            print("[telegram] нет токена — пропущено", file=sys.stderr)
        else:
            TelegramAdapter(cfg, store, engine).run()
    else:
        try:
            import threading
            threading.Event().wait()
        except KeyboardInterrupt:
            httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
