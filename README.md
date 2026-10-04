# GhostReply 📝

Приложение, **которое пишет сообщения в мессенджере вместо тебя**.

Ты скидываешь ему историю чата — оно разбирает **твой стиль** (длина сообщений,
эмодзи, капс, восклицания, фирменные слова, как здоровякаешься) и отвечает
собеседнику так, как отвечал бы ты сам.

## Что умеет

- **Стиль по истории** — профиль строится из твоих сообщений, а не из шаблонов.
- **Ответ по контексту** — смотрит последние N сообщений чата и отвечает на последнее.
- **HTTP API** — подключается к любому мессенджеру (свой, Telegram, WhatsApp-шлюз и т.д.).
- **Telegram адаптер** — long polling, отвечает сам в выбранных чатах.
- **Два режима** — `auto` (отправляет сразу) и `draft` (черновик, решаешь сам).
- **Фолбэк без сети** — если LLM недоступна, отвечает локально по стилю.
- **Ноль зависимостей** — чистый Python 3.9+, ничего ставить не надо.

## Старт за 1 минуту

```bash
python3 test_app.py          # самопроверка
python3 main.py              # HTTP API на 127.0.0.1:8787
```

С LLM (OpenAI-совместимый API):

```bash
export GHOST_LLM_API_KEY="sk-..."
export GHOST_LLM_MODEL="gpt-4o-mini"       # или groq/llama, ollama и т.п.
python3 main.py
```

Telegram вместо тебя:

```bash
export GHOST_TELEGRAM_TOKEN="123:ABC..."
export GHOST_TELEGRAM_ALLOWED_CHATS="-100123,456789"   # пусто = все чаты
export GHOST_MODE="auto"                                # draft — только черновики
python3 main.py --telegram
```

Конфиг можно держать в `config.json` (см. `config.example.json`) и передавать
`--config config.json` — переменные окружения имеют приоритет.

## HTTP API

| Метод | Путь | Тело | Что делает |
|---|---|---|---|
| POST | `/history` | `{"chat_id", "messages":[{"sender":"me"\|"peer","text"}]}` | импорт истории чата |
| POST | `/message` | `{"chat_id", "text", "sender":"peer"}` | входящее → `{"reply","mode"}` |
| POST | `/reply` | `{"chat_id?", "text"}` | просто сгенерировать ответ |
| GET | `/style/<chat_id>` | — | профиль твоего стиля |
| GET | `/health` | — | `{ok:true}` |

### Пример: подключить свой мессенджер

```bash
# 1. Залей историю переписки (ты — "me", собеседник — "peer")
curl -X POST localhost:8787/history -H 'Content-Type: application/json' -d '{
  "chat_id": "ivan",
  "messages": [
    {"sender": "peer", "text": "привет, ты где?"},
    {"sender": "me",   "text": "ку, на работе 😅"},
    {"sender": "peer", "text": "когда освободишься?"}
  ]
}'

# 2. Получи ответ, когда придёт новое сообщение
curl -X POST localhost:8787/message -H 'Content-Type: application/json' \
  -d '{"chat_id": "ivan", "text": "когда освободишься?"}'
# -> {"reply": "после шести норм, мигом 😅", "mode": "auto"}
```

В режиме `auto` ответ пишется в историю как твой; в `draft` — возвращается
черновик, и решать отправлять ли тебе.

## Как это работает

```
история чата ──► StyleProfile (твой стиль) ─┐
                                            ├─► промпт ─► LLM ─► доводка под стиль ─► ответ
последнее сообщение ────────────────────────┘                    │
                                                    нет LLM ─────┘ локальный фолбэк
```

Файлы:

- `ghostreply/style.py` — разбор твоего стиля из истории
- `ghostreply/engine.py` — промпт, LLM-вызов, стилевая доводка, фолбэк
- `ghostreply/history.py` — JSONL-хранилище чатов (`data/history/`)
- `ghostreply/api.py` — HTTP API для любого мессенджера
- `ghostreply/telegram.py` — Telegram-адаптер

## Данные

Вся история лежит локально в `data/history/*.jsonl` (в `.gitignore`).
Ничего наружу не уходит, кроме промптов к выбранному тобой LLM-провайдеру.

## Лицензия

MIT
