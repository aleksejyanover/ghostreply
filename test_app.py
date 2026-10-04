"""Самопроверка без сети: python3 test_app.py"""

import json
import shutil
import tempfile
import urllib.request

from ghostreply import config
from ghostreply.api import serve
from ghostreply.engine import ReplyEngine
from ghostreply.history import HistoryStore
from ghostreply.style import StyleProfile

TMP = tempfile.mkdtemp(prefix="ghostreply_test_")
FAILS = []


def check(name, cond):
    print(("OK  " if cond else "FAIL") + " " + name)
    if not cond:
        FAILS.append(name)


def main():
    cfg = config.load(data_dir=TMP)
    store = HistoryStore(cfg.data_dir)
    engine = ReplyEngine(cfg, store)
    from ghostreply.status import StatusHub
    hub = StatusHub()

    class StubAdapter:  # адаптеры в тестах не поднимаем, но статусы отдаём
        running = False
        def start(self): return False
        def stop(self): pass

    adapters = {"telegram": StubAdapter(), "vk": StubAdapter()}

    # 1. импорт истории
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

    # 3. фолбэк-ответ (без LLM)
    r = engine.reply("c1", "а ты уже купил билеты?")
    check("ответ непустой", bool(r))
    print("   ответ:", repr(r))

    # 3б. глобальный профиль: в новом чате стиль берётся из остальных
    check("в новом чате стиль известен (глобальный)",
          len(engine.profile("brand_new_chat").my_texts) >= 2)
    store.append("brand_new_chat", "me", "я всегда пишу длинно и со смайликами 😊")
    for i in range(6):
        store.append("brand_new_chat", "me" if i % 2 else "peer",
                     f"сообщение номер {i} 😊")
    own = [m for m in store.load("brand_new_chat") if m["sender"] == "me"]
    check("в новом чате накопилось 4+ своих", len(own) >= 4)
    check("после 4 своих — профиль чатовый (без чужих чатов)",
          all(t.startswith("я всегда") or t[0].isdigit() or "😊" in t
              for t in engine.profile("brand_new_chat").my_texts)
          and engine.profile("brand_new_chat").my_texts[-1] == own[-1]["text"])

    # 3в. дубль последнего сообщения вырезается из контекста
    from ghostreply.history import strip_last_duplicate
    msgs = [{"sender": "peer", "text": "привет"}, {"sender": "peer", "text": "ты где?"}]
    check("дубль incoming вырезан", len(strip_last_duplicate(msgs, "ты где?")) == 1)
    check("чужой текст не трогаем", len(strip_last_duplicate(msgs, "иной")) == 2)

    # 4. стиль LLM-ответа
    styled = engine._match_style("ок", p)
    check("стилизация добавила эмодзи", any(e in styled for e in p.emoji_set))

    # 5. HTTP API + интерфейс
    httpd, _ = serve(cfg, store, engine, hub, adapters)
    base = f"http://{cfg.http_host}:{cfg.http_port}"

    def get(path, raw=False):
        resp = urllib.request.urlopen(base + path, timeout=5)
        data = resp.read()
        return data if raw else json.loads(data)

    def post(path, obj, method="POST"):
        req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method=method)
        return json.loads(urllib.request.urlopen(req, timeout=5).read())

    html = get("/", raw=True).decode()
    check("GET / отдаёт интерфейс", "GhostReply" in html and "<html" in html)
    check("интерфейс по-русски", "Импорт истории" in html)
    check("GET /health", get("/health")["ok"] is True)

    h = post("/history", {"chat_id": "c2", "messages": [
        {"sender": "me", "text": "привет!"},
        {"sender": "peer", "text": "привет, ты где?"},
    ]})
    check("POST /history", h.get("imported") == 2)

    m = post("/message", {"chat_id": "c2", "text": "ты где?"})
    check("POST /message возвращает reply", bool(m.get("reply")))
    check("mode=auto сохранил ответ", len(store.load("c2")) == 4)

    m2 = post("/message", {"chat_id": "c2", "text": "ок", "sender": "me"})
    check("сообщение от me не порождает ответ", m2.get("reply") is None)

    check("GET /chats",
          set(c["chat_id"] for c in get("/chats")["chats"]) == {"c1", "c2", "brand_new_chat"})
    check("GET /chats сортирует по свежести",
          get("/chats")["chats"][0]["chat_id"] == "c2")
    check("GET /chat/<id>", len(get("/chat/c1")["messages"]) == 5)
    check("GET /style", len(get("/style/c1")["style"]) >= 3)

    s = post("/settings", {"mode": "draft"})
    check("POST /settings", s["ok"] is True)
    check("настройки сохранились", get("/settings")["mode"] == "draft")
    m3 = post("/message", {"chat_id": "c2", "text": "а завтра?"})
    msgs_c2 = store.load("c2")
    check("draft не пишет ответ в историю", m3["mode"] == "draft"
          and len(msgs_c2) == 6 and msgs_c2[-1]["sender"] == "peer")
    post("/settings", {"mode": "auto"})

    bad = None
    try:
        post("/settings", {"blabla": 1})
    except urllib.error.HTTPError as e:
        bad = e.code
    check("POST /settings отклоняет мусор", bad == 400)

    # импорт из текста «Я: ... / Собеседник: ...» (то, что вставляет UI)
    check("парсер UI-формата", _parse_lines() == [
        {"sender": "peer", "text": "ты где?"},
        {"sender": "me", "text": "на работе 😅"},
        {"sender": "peer", "text": "ok"},
    ])

    # очистка ответа LLM
    from ghostreply.engine import _clean_reply
    check("clean: срезает «Я:»", _clean_reply("Я: ок, до встречи") == "ок, до встречи")
    check("clean: срезает кавычки", _clean_reply("«пока 👋»") == "пока 👋")
    check("clean: пустое — пустое", _clean_reply("") == "")

    # экспорт истории
    check("export отдаёт поля", set(store.export("c1")[0]) == {"ts", "sender", "text"})

    # переподключение по сохранённому токену не требует токена в запросе
    bad = None
    try:
        post("/connect", {"messenger": "vk", "token": "••••••••"})
    except urllib.error.HTTPError as e:
        bad = e.code
    check("переподключение без сохранённого токена отклонено", bad == 400)

    req = urllib.request.Request(base + "/chat/c2", method="DELETE")
    check("DELETE /chat", json.loads(urllib.request.urlopen(req, timeout=5).read())["ok"])
    check("чат удалён", store.load("c2") == [])

    # подключения мессенджеров
    conn = get("/connections")["connections"]
    check("GET /connections", set(conn) == {"telegram", "vk"})
    check("по умолчанию не подключено", not conn["vk"]["running"])

    bad = None
    try:
        post("/connect", {"messenger": "vk"})
    except urllib.error.HTTPError as e:
        bad = e.code
    check("connect без токена отклонён", bad == 400)

    bad = None
    try:
        post("/connect", {"messenger": "what", "token": "x"})
    except urllib.error.HTTPError as e:
        bad = e.code
    check("connect с неизвестным мессенджером отклонён", bad == 400)

    hub.event("vk", "vk:1", "in", "привет")
    hub.event("vk", "vk:1", "out", "ку")
    ev = get("/events")
    check("GET /events отдаёт ленту", len(ev["events"]) >= 2)

    # разбор ссылок
    from ghostreply.vk import parse_vk_link
    check("парсит vk.com/club", parse_vk_link("https://vk.com/club123456")["group_id"] == "123456")
    check("парсит vk.com/имя", parse_vk_link("https://vk.com/mymarket")["link"].endswith("/mymarket"))
    check("импорт сообщений отсеивает не-словари",
          store.import_messages("c3", ["мусор", {"sender": "me", "text": "ок"}]) == 1)

    httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    if FAILS:
        print("\nПровалено:", ", ".join(FAILS))
        raise SystemExit(1)
    print("\nВсе проверки пройдены.")


def _parse_lines():
    """Повторяет parseImport() из интерфейса для линейного формата."""
    import re
    out = []
    for line in ["Собеседник: ты где?", "Я: на работе 😅", "ok", ""]:
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(?:Я|me|Me|Мой)\s*[:\-–]\s*(.+)$", s)
        if m:
            out.append({"sender": "me", "text": m.group(1)})
            continue
        m = re.match(r"^(?:Собеседник|Он|Она|Они|peer|Partner)\s*[:\-–]\s*(.+)$", s, re.I)
        if m:
            out.append({"sender": "peer", "text": m.group(1)})
            continue
        out.append({"sender": "peer", "text": s})
    return out


if __name__ == "__main__":
    main()
