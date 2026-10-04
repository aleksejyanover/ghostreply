#!/usr/bin/env python3
"""GhostReply: веб-интерфейс + HTTP API + (опционально) Telegram.

Запуск:  python3 main.py            — поднимает сервер и открывает окно в браузере
         python3 main.py --telegram — + автответы в Telegram
"""

import argparse
import sys
import threading
import time
import webbrowser

from ghostreply import api, config
from ghostreply.engine import ReplyEngine
from ghostreply.history import HistoryStore
from ghostreply.status import StatusHub
from ghostreply.telegram import TelegramAdapter
from ghostreply.vk import VKAdapter


def main() -> int:
    ap = argparse.ArgumentParser(description="GhostReply — пишет вместо тебя")
    ap.add_argument("--config", default="", help="путь к config.json")
    ap.add_argument("--host", default="", help="адрес HTTP API")
    ap.add_argument("--port", type=int, default=0, help="порт HTTP API")
    ap.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    ap.add_argument("--once", metavar="CHAT:TEXT", help="сгенерировать один ответ и выйти")
    args = ap.parse_args()

    cfg = config.load(args.config)
    if args.host:
        cfg.http_host = args.host
    if args.port:
        cfg.http_port = args.port

    store = HistoryStore(cfg.data_dir)
    engine = ReplyEngine(cfg, store)
    hub = StatusHub()
    adapters = {
        "telegram": TelegramAdapter(cfg, store, engine, hub),
        "vk": VKAdapter(cfg, store, engine, hub),
    }

    if args.once:
        chat_id, _, text = args.once.partition(":")
        if not chat_id or not text:
            print("--once формат: CHAT:ТЕКСТ", file=sys.stderr)
            return 2
        print(engine.reply(chat_id, text))
        return 0

    # если порт занят — берём следующий свободный
    httpd = None
    for port in range(cfg.http_port, cfg.http_port + 20):
        cfg.http_port = port
        try:
            httpd, _ = api.serve(cfg, store, engine, hub, adapters)
            break
        except OSError:
            continue
    if httpd is None:
        print(f"Не удалось занять порт {cfg.http_port}", file=sys.stderr)
        return 1

    # подключаем ранее настроенные мессенджеры
    for name, ad in adapters.items():
        if ad.start():
            print(f"[{name}] подключаюсь...")

    url = f"http://{cfg.http_host}:{cfg.http_port}"
    llm = cfg.llm_model if cfg.llm_api_key else "локальный фолбэк"
    print(f"GhostReply запущен: {url}")
    print(f"  режим={cfg.mode}  llm={llm}  данные={cfg.data_dir}")
    print("  Ctrl+C — остановить")

    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nОстанавливаю...")
        for ad in adapters.values():
            ad.stop()
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
