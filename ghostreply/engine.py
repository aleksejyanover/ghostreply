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

# Локальный фолбэк (когда LLM нет): намерение -> варианты ответа
FALLBACK_BY_INTENT = {
    "ru": {
        "greet": ["привет!", "ку, как сам?", "здарова 👋", "хай, как дела?"],
        "howareyou": ["нормально 😎 а ты?", "всё ок, ты как?", "норм, работаю"],
        "plan": ["пока не решил... скажу позже", "что-то думаю... а что предлагаешь?",
                 "пока планов нет, а ты?", "может сходим куда-то?"],
        "when": ["скажу чуть позже, уточню", "дам знать сегодня", "давай ближе к вечеру"],
        "question": ["скажу чуть позже 😎", "сейчас гляну и отвечу",
                     "а ты как думаешь?", "хм, надо подумать"],
        "thanks": ["да не за что 👍", "пожалуйста!", "не парься 😎"],
        "bye": ["ок, до связи!", "пока 👋", "ухожу, пиши если что"],
        "yes": ["ок", "давай", "ага, норм"],
        "no": ["ну нет...", "не, не выйдет", "давай как-то в другой раз"],
        "default": ["ок, понял", "ага, ясно", "хорошо, договорились"],
    },
    "en": {
        "greet": ["hey!", "hi, how's it going?"],
        "howareyou": ["all good, you?", "doing fine 😎"],
        "plan": ["not sure yet, will tell you later", "nothing planned, you?"],
        "when": ["will let you know later", "sometime this evening"],
        "question": ["got it, will check", "hmm, let me think"],
        "thanks": ["no problem 👍", "sure"],
        "bye": ["ok, bye!", "later 👋"],
        "yes": ["ok", "sure"],
        "no": ["nah", "don't think so"],
        "default": ["ok, got it", "sounds good"],
    },
}

_GREETING_MARKS = ("привет", "здравств", "ку", "хай", "hello", "hi ", "hey", "добрый",
                   "доброе", "салют", "йо")
_BYE_MARKS = ("пока", "до связи", "бай", "bye", "гудбай", "спокойной")
_THANKS_MARKS = ("спасибо", "thanks", "thx", "сенкс")
_PLAN_MARKS = ("план", "собираешься", "будешь", "поедем", "сходим", "отдыха", "выходн",
               "will you", "plans", "gonna", "wanna")
_WHEN_MARKS = ("когда", "во сколько", "какого числа", "when", "what time")


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
        pool = FALLBACK_BY_INTENT.get(profile.lang, FALLBACK_BY_INTENT["ru"])
        intent = self._intent(incoming)
        variants = pool.get(intent, pool["default"])
        # детерминированный, но разный выбор: длина входа + её длина
        idx = (len(incoming) + incoming.count(" ")) % len(variants)
        reply = variants[idx]

        if profile.emoji_freq > 0.3 and profile.emoji_set and not any(
            e in reply for e in profile.emoji_set
        ):
            reply += " " + profile.emoji_set.most_common(1)[0][0]
        if profile.exclamations > 0.3 and not reply.endswith(("!", "?")):
            reply += "!"
        if profile.lowercase_starts > 0.6:
            reply = reply[0].lower() + reply[1:]
        return reply

    @staticmethod
    def _intent(text: str) -> str:
        t = text.lower()
        if any(m in t for m in _THANKS_MARKS):
            return "thanks"
        if any(t.startswith(m) or f" {m}" in f" {t}" for m in _BYE_MARKS):
            return "bye"
        if any(m in t for m in _GREETING_MARKS) and len(t) < 30:
            return "greet"
        if any(w in t for w in ("как дела", "как ты", "как жизнь", "how's it", "how are you")):
            return "howareyou"
        if any(m in t for m in _WHEN_MARKS):
            return "when"
        if any(m in t for m in _PLAN_MARKS):
            return "plan"
        if "?" in t:
            return "question"
        if t.strip().rstrip("!") in ("да", "ага", "ок", "давай", "yes", "ok", "sure", "даа"):
            return "yes"
        if t.strip().rstrip("!") in ("нет", "не", "no", "nah", "неа"):
            return "no"
        return "default"

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
