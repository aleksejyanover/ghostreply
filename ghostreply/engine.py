"""Генерация ответа: LLM (OpenAI-совместимый API) + локальный фолбэк без сети."""

import json
import urllib.request
import urllib.error
from typing import Dict, List, Optional

from .config import Config
from .history import HistoryStore, transcript
from .style import StyleProfile

SYSTEM_TMPL = """Ты — автозаместитель хозяина чата. Ты пишешь сообщение ОТ ЕГО ИМЕНИ собеседнику.
Правила:
- Полностью повторяй стиль хозяина: длина, тон, эмодзи, капс, пунктуация, обращения.
- Не извиняйся, не представляйся, не пиши "как ИИ". Ты просто он.
- Отвечай ровно на последнее сообщение собеседника, опираясь на контекст истории.
- Только текст сообщения, без кавычек, без пояснений, без "Я:".

Стиль хозяина:
{style}

Примеры его сообщений:
{samples}

История чата:
{transcript}

Последнее сообщение собеседника: {last}
Напиши ответ хозяина:"""

FALLBACK_TEMPLATES = {
    "ru": [
        "ок",
        "понял",
        "да, норм",
        "сейчас гляну",
        "хорошо, договорились",
    ],
    "en": ["ok", "got it", "sure", "sounds good"],
}


class ReplyEngine:
    def __init__(self, cfg: Config, store: HistoryStore):
        self.cfg = cfg
        self.store = store

    def profile(self, chat_id: str) -> StyleProfile:
        return StyleProfile(self.store.load(chat_id))

    def reply(self, chat_id: str, incoming: str, history: Optional[List[Dict]] = None) -> str:
        msgs = history if history is not None else self.store.recent(chat_id, self.cfg.history_window)
        profile = StyleProfile(msgs)
        last = incoming
        transcript_text = transcript(msgs[:-1] if msgs and msgs[-1].get("text") == incoming and msgs[-1].get("sender") == "peer" else msgs)

        prompt = SYSTEM_TMPL.format(
            style="\n".join(f"- {l}" for l in profile.style_lines),
            samples="\n".join(f"- {s}" for s in profile.samples()) or "- (нет данных)",
            transcript=transcript_text or "(пусто)",
            last=last,
        )
        out = self._llm(prompt)
        if out:
            return self._match_style(out, profile)
        return self._local(profile, incoming)

    # --- LLM ---------------------------------------------------------------

    def _llm(self, prompt: str) -> Optional[str]:
        if not self.cfg.llm_api_key:
            return None
        url = self.cfg.llm_url.rstrip("/") + "/chat/completions"
        body = json.dumps({
            "model": self.cfg.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.9,
            "max_tokens": 600,
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg.llm_api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.llm_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"].strip()
            return text or None
        except (urllib.error.URLError, KeyError, IndexError, ValueError, OSError):
            return None

    # --- Фолбэк ------------------------------------------------------------

    def _local(self, profile: StyleProfile, incoming: str) -> str:
        text = incoming.lower()
        templates = FALLBACK_TEMPLATES.get(profile.lang, FALLBACK_TEMPLATES["ru"])
        if any(w in text for w in ("?", "как", "что", "где", "когда", "why", "how", "what")):
            reply = "скажу чуть позже"
        elif any(w in text for w in ("привет", "hello", "hi", "ку")):
            reply = templates[0]
        else:
            reply = templates[min(len(incoming) // 30, len(templates) - 1)]

        if profile.emoji_freq > 0.3 and profile.emoji_set:
            reply += " " + profile.emoji_set.most_common(1)[0][0]
        if profile.exclamations > 0.3 and not reply.endswith("!"):
            reply += "!"
        if profile.lowercase_starts > 0.6:
            reply = reply[0].lower() + reply[1:]
        return reply

    def _match_style(self, text: str, profile: StyleProfile) -> str:
        """Грубая доводка ответа под профиль, если LLM отклонилась."""
        text = text.strip().strip('"').strip("«»")
        if not text:
            return text
        want_excl = profile.exclamations > 0.4 and "!" not in text
        if want_excl and not text.endswith(("!", "?")):
            text += "!"
        if profile.emoji_freq > 0.4 and profile.emoji_set and not any(
            e in text for e in profile.emoji_set
        ):
            text += " " + profile.emoji_set.most_common(1)[0][0]
        if profile.lowercase_starts > 0.7 and text[0].isupper() and not text[:2].isupper():
            text = text[0].lower() + text[1:]
        return text
