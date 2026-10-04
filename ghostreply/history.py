"""Хранилище истории чатов: JSONL на чат, потокобезопасное.

Формат записи:
  {"chat_id": "...", "sender": "me" | "peer", "text": "...", "ts": 1690000000}
"""

import json
import os
import threading
import time
from typing import Dict, List


class HistoryStore:
    def __init__(self, data_dir: str):
        self.dir = os.path.join(data_dir, "history")
        self._lock = threading.Lock()
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, chat_id: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(chat_id))
        return os.path.join(self.dir, safe + ".jsonl")

    def append(self, chat_id: str, sender: str, text: str, ts: float = 0) -> None:
        rec = {
            "chat_id": str(chat_id),
            "sender": "me" if sender in ("me", "self", True) else "peer",
            "text": text,
            "ts": float(ts or time.time()),
        }
        with self._lock:
            with open(self._path(chat_id), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def load(self, chat_id: str) -> List[Dict]:
        path = self._path(chat_id)
        if not os.path.exists(path):
            return []
        out = []
        with self._lock:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    def recent(self, chat_id: str, n: int) -> List[Dict]:
        return self.load(chat_id)[-n:]

    def import_messages(self, chat_id: str, messages: List[Dict]) -> int:
        """Импорт истории из мессенджера: [{sender, text, ts?}, ...]"""
        count = 0
        for m in messages:
            if not isinstance(m, dict) or not m.get("text"):
                continue
            self.append(chat_id, m.get("sender", "peer"), m["text"], m.get("ts", 0))
            count += 1
        return count

    def delete(self, chat_id: str) -> bool:
        with self._lock:
            try:
                os.remove(self._path(chat_id))
                return True
            except FileNotFoundError:
                return False

    def chats(self):
        return [
            os.path.splitext(f)[0]
            for f in sorted(os.listdir(self.dir))
            if f.endswith(".jsonl")
        ]

    def all_messages(self, limit_per_chat: int = 200) -> List[Dict]:
        """Сообщения из всех чатов — для глобального профиля стиля."""
        out: List[Dict] = []
        for cid in self.chats():
            out.extend(self.load(cid)[-limit_per_chat:])
        return out

    def export(self, chat_id: str) -> List[Dict]:
        """История чата для выгрузки: [{ts, sender, text}, ...]."""
        return [{"ts": m.get("ts"), "sender": m.get("sender"), "text": m.get("text")}
                for m in self.load(chat_id)]


def transcript(messages: List[Dict]) -> str:
    """История → текст для промпта."""
    lines = []
    for m in messages:
        who = "Я" if m.get("sender") == "me" else "Собеседник"
        lines.append(f"{who}: {m.get('text', '')}")
    return "\n".join(lines)


def strip_last_duplicate(messages: List[Dict], incoming: str) -> List[Dict]:
    """Убирает последнее сообщение, если это тот же incoming от собеседника
    (иначе оно попадает в промпт дважды — и как контекст, и как «последнее»)."""
    if messages and messages[-1].get("sender") == "peer" and \
            messages[-1].get("text") == incoming:
        return messages[:-1]
    return messages
