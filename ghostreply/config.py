"""Конфигурация: config.json + переменные окружения (env имеет приоритет)."""

import json
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    # HTTP API
    http_host: str = "127.0.0.1"
    http_port: int = 8787

    # LLM (OpenAI-совместимый API: OpenAI, Groq, Together, локальный ollama и т.д.)
    llm_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 45

    # Telegram-адаптер (пустой токен = адаптер выключен)
    telegram_token: str = ""
    telegram_allowed_chats: List[str] = field(default_factory=list)  # пусто = все чаты

    # auto = отправлять сразу, draft = возвращать черновик, но не слать
    mode: str = "auto"

    # Сколько последних сообщений истории отдавать в промпт
    history_window: int = 40

    data_dir: str = ""


def load(config_path: str = "", data_dir: str = "") -> Config:
    raw = {}
    path = config_path or os.environ.get("GHOST_CONFIG", "")
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

    cfg = Config()
    for key, value in raw.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)

    env_map = {
        "GHOST_HTTP_HOST": "http_host",
        "GHOST_HTTP_PORT": "http_port",
        "GHOST_LLM_URL": "llm_url",
        "GHOST_LLM_API_KEY": "llm_api_key",
        "GHOST_LLM_MODEL": "llm_model",
        "GHOST_TELEGRAM_TOKEN": "telegram_token",
        "GHOST_MODE": "mode",
        "GHOST_DATA_DIR": "data_dir",
    }
    for env, attr in env_map.items():
        if os.environ.get(env):
            setattr(cfg, attr, os.environ[env])

    allowed = os.environ.get("GHOST_TELEGRAM_ALLOWED_CHATS", "")
    if allowed:
        cfg.telegram_allowed_chats = [c.strip() for c in allowed.split(",") if c.strip()]

    cfg.http_port = int(cfg.http_port)
    cfg.llm_timeout = int(cfg.llm_timeout)
    cfg.history_window = int(cfg.history_window)

    if data_dir:
        cfg.data_dir = data_dir
    if not cfg.data_dir:
        cfg.data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
        )
    return cfg
