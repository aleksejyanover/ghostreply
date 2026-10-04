"""Быстрая самопроверка без сети: python3 test_app.py"""

import os
import shutil
import tempfile

from ghostreply import config
from ghostreply.engine import ReplyEngine
from ghostreply.history import HistoryStore
from ghostreply.style import StyleProfile

TMP = tempfile.mkdtemp(prefix="ghostreply_test_")


def check(name, cond):
    print(("OK  " if cond else "FAIL") + " " + name)
    assert cond, name


def main():
    cfg = config.load(data_dir=TMP)
    store = HistoryStore(cfg.data_dir)
    engine = ReplyEngine(cfg, store)

    # 1. импорт истории: собеседник — peer, хозяин — me
    n = store.import_messages("c1", [
        {"sender": "peer", "text": "привет, как дела?"},
        {"sender": "me", "text": "куу, нормально 😎 как ты?"},
        {"sender": "peer", "text": "давай завтра в 7 у встречки?"},
        {"sender": "me", "text": "ок, буду в 7... не опоздаю!"},
        {"sender": "peer", "text": "супер, до связи!"},
    ])
    check("импорт истории", n == 5)

    # 2. профиль стиля
    p = engine.profile("c1")
    check("профиль видит мои сообщения", len(p.my_texts) == 2)
    check("эмодзи в профиле", p.emoji_freq > 0)
    check("описание стиля непустое", len(p.style_lines) >= 3)

    # 3. фолбэк-ответ (без LLM) возвращает текст
    r = engine.reply("c1", "а ты уже купил билеты?")
    check("ответ непустой", bool(r))
    print("   ответ:", repr(r))

    # 4. подгонка стиля: вставка эмодзи при частом эмодзи
    styled = engine._match_style("ок", p)
    check("стилизация добавила эмодзи", any(e in styled for e in p.emoji_set))

    # 5. HTTP API
    from ghostreply.api import serve
    import urllib.request, json as js
    httpd, _ = serve(cfg, store, engine)
    base = f"http://{cfg.http_host}:{cfg.http_port}"

    def post(path, obj):
        req = urllib.request.Request(base + path, data=js.dumps(obj).encode(),
                                     headers={"Content-Type": "application/json"})
        return js.loads(urllib.request.urlopen(req, timeout=5).read())

    h = post("/history", {"chat_id": "c2", "messages": [
        {"sender": "me", "text": "привет!"},
        {"sender": "peer", "text": "привет, ты где?"},
    ]})
    check("POST /history", h.get("imported") == 2)

    m = post("/message", {"chat_id": "c2", "text": "ты где?"})
    check("POST /message возвращает reply", bool(m.get("reply")))
    check("mode=auto сохранил ответ", len(store.load("c2")) == 4)
    print("   reply:", repr(m["reply"]))

    ur = urllib.request.urlopen(base + "/style/c1", timeout=5)
    check("GET /style", js.loads(ur.read())["style"])

    httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    print("\nВсе проверки пройдены.")


if __name__ == "__main__":
    main()
