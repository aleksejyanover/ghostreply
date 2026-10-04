"""Общий статус мостов и лент событий (кто, что написал и что ответил)."""

import threading
import time
from collections import deque
from typing import Dict, List, Optional


class StatusHub:
    def __init__(self, maxlen: int = 300):
        self._lock = threading.Lock()
        self._status: Dict[str, Dict] = {}
        self._log: deque = deque(maxlen=maxlen)

    def set(self, name: str, running: bool = False, error: str = "",
            configured: bool = False) -> None:
        with self._lock:
            st = self._status.get(name, {})
            st.update({"running": running, "error": error,
                       "configured": configured, "ts": time.time()})
            if running and not error:
                st["error"] = ""
            self._status[name] = st

    def event(self, source: str, chat: str, direction: str, text: str) -> None:
        """direction: in | out | draft | error"""
        with self._lock:
            self._log.append({
                "ts": time.time(), "source": source, "chat": chat,
                "direction": direction, "text": (text or "")[:200],
            })

    def snapshot(self, log_limit: int = 0) -> Dict:
        with self._lock:
            out = {"adapters": {k: dict(v) for k, v in self._status.items()}}
            if log_limit:
                out["log"] = list(self._log)[-log_limit:]
            else:
                out["log"] = list(self._log)
        return out

    def get(self, name: str) -> Optional[Dict]:
        with self._lock:
            st = self._status.get(name)
            return dict(st) if st else None
