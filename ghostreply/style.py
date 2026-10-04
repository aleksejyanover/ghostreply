"""Стилевой профиль: разбор ТВОИХ сообщений из истории, чтобы отвечать так же."""

import re
from collections import Counter
from typing import Dict, List, Optional

EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍❤⭐]"
)


class StyleProfile:
    def __init__(self, messages: Optional[List[Dict]] = None):
        self.my_texts: List[str] = []
        self.avg_len = 40
        self.exclamations = 0.0     # "!" на сообщение
        self.emoji_freq = 0.0       # эмодзи на сообщение
        self.ellipsis_freq = 0.0    # многоточий на сообщение
        self.caps_ratio = 0.0
        self.lowercase_starts = 0.0
        self.questions = 0.0
        self.common_words: Counter = Counter()
        self.emoji_set: Counter = Counter()
        self.greetings: Counter = Counter()
        self.fillers: Counter = Counter()
        self.lang = "ru"
        if messages:
            self._build(messages)

    def _build(self, messages: List[Dict]) -> None:
        texts = [m.get("text", "") for m in messages if m.get("sender") == "me" and m.get("text")]
        self.my_texts = texts
        if not texts:
            return

        self.avg_len = sum(len(t) for t in texts) / len(texts)
        self.exclamations = sum(t.count("!") for t in texts) / len(texts)
        self.ellipsis_freq = sum(t.count("...") + t.count("…") for t in texts) / len(texts)
        self.questions = sum(t.count("?") for t in texts) / len(texts)

        emoji_hits = 0
        letters = upper = 0
        starts_lower = 0
        words: List[str] = []
        for t in texts:
            em = EMOJI_RE.findall(t)
            emoji_hits += len(em)
            self.emoji_set.update(em)
            stripped = EMOJI_RE.sub("", t)
            letters += sum(c.isalpha() for c in stripped)
            upper += sum(c.isupper() for c in stripped)
            if t and t[0].isalpha():
                starts_lower += int(t[0].islower())
            for w in re.findall(r"[а-яёa-z]{3,}", t.lower()):
                words.append(w)

        self.emoji_freq = emoji_hits / len(texts)
        self.caps_ratio = (upper / letters) if letters else 0.0
        self.lowercase_starts = starts_lower / len(texts)

        stop = {
            "это", "так", "вот", "для", "как", "его", "что", "чтобы", "можно", "меня",
            "тебе", "мне", "тоже", "или", "но", "да", "нет", "the", "and", "you", "that",
            "для", "при", "про", "без", "она", "они", "там", "тут", "уже", "еще", "ещё",
        }
        self.common_words = Counter(w for w in words if w not in stop)

        greetings = ["привет", "ку", "здравствуйте", "хай", "hello", "hi", "добрый день",
                     "доброе утро", "добрый вечер", "салют", "йо"]
        g_texts = [t.lower() for t in texts]
        self.greetings = Counter(
            g for g in greetings if any(t.startswith(g) for t in g_texts)
        )

        fillers = ["короче", "типа", "как бы", "в общем", "слышь", "блин", "ну",
                   "вообще", "честно", "кстати", "нуу", "тааак", "вот это", "здорово",
                   "крут", "красота", "топ", "база"]
        self.fillers = Counter(
            f for f in fillers if any(f in t for t in g_texts)
        )

        cyr = sum(1 for w in words if re.match(r"[а-яё]", w))
        self.lang = "ru" if cyr >= len(words) - cyr else "en"

    @property
    def style_lines(self) -> List[str]:
        """Человекочитаемое описание стиля для промпта."""
        if not self.my_texts:
            return ["Стиль неизвестен — отвечай нейтрально и коротко."]
        lines = []
        lines.append(
            f"Средняя длина сообщения ~{int(self.avg_len)} символов"
            f" ({'короткие' if self.avg_len < 60 else 'средние' if self.avg_len < 140 else 'развёрнутые'}),"
            f" {'много' if self.emoji_freq > 0.5 else 'иногда' if self.emoji_freq > 0.1 else 'почти нет'} эмодзи,"
            f" {'!' if self.exclamations > 0.3 else 'без восклицаний'},"
            f" {'начинает со строчной' if self.lowercase_starts > 0.6 else 'с заглавной'} буквы."
        )
        top_words = [w for w, _ in self.common_words.most_common(6)]
        if top_words:
            lines.append("Часто используемые слова: " + ", ".join(top_words) + ".")
        if self.emoji_set:
            top_em = [e for e, _ in self.emoji_set.most_common(6)]
            lines.append("Любимые эмодзи: " + " ".join(top_em) + ".")
        if self.fillers:
            lines.append("Фирменные вставки: " + ", ".join(self.fillers).strip(", ") + ".")
        if self.greetings:
            lines.append("Так здоровается: " + ", ".join(self.greetings) + ".")
        if self.caps_ratio > 0.25:
            lines.append("Иногда пишет БОЛЬШИМИ БУКВАМИ.")
        if self.ellipsis_freq > 0.3:
            lines.append("Часто ставит многоточие...")
        if self.questions > 0.5:
            lines.append("Часто задаёт встречные вопросы.")
        lines.append(
            "Язык: русский." if self.lang == "ru" else "Language: English."
        )
        return lines

    def samples(self, n: int = 8) -> List[str]:
        return self.my_texts[-n:]
