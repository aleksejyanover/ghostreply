"""Конфигурация: config.json + settings.json (из UI) + env. Приоритет: env > settings > config."""

import json
import os
from dataclasses import dataclass, field
from typing import List

_FIELDS = (
    "http_host", "http_port", "llm_url", "llm_api_key", "llm_model", "llm_timeout",
    "telegram_token", "telegram_link", "telegram_allowed_chats",
    "vk_token", "vk_link", "vk_group_id",
    "mode", "history_window", "data_dir",
)


@dataclass
class Config:
    http_host: str = "127.0.0.1"
    http_port: int = 8787

    # LLM (OpenAI-совместимый API: OpenAI, Groq, Together, локальный ollama и т.д.)
    llm_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 45

    # Telegram-адаптер (пустой токен = адаптер выключен)
    telegram_token: str = ""
    telegram_link: str = ""   # ссылка на бота/чат, для справки
    telegram_allowed_chats: List[str] = field(default_factory=list)  # пусто = все чаты

    # VK-адаптер (пустой токен = адаптер выключен)
    vk_token: str = ""        # токен сообщества с правом messages
    vk_link: str = ""         # ссылка на сообщество/чат, для справки
    vk_group_id: str = ""

    # auto = отправлять сразу, draft = возвращать черновик, но не слать
    mode: str = "auto"

    history_window: int = 40
    data_dir: str = ""


def _apply(cfg: Config, raw: dict) -> None:
    for key, value in (raw or {}).items():
        if key in _FIELDS and value is not None:
            setattr(cfg, key, value)


def default_data_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def load(config_path: str = "", data_dir: str = "") -> Config:
    cfg = Config()

    path = config_path or os.environ.get("GHOST_CONFIG", "")
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            _apply(cfg, json.load(f))

    if data_dir:
        cfg.data_dir = data_dir
    if not cfg.data_dir:
        cfg.data_dir = os.environ.get("GHOST_DATA_DIR") or default_data_dir()

    # Настройки, сохранённые через интерфейс
    settings = os.path.join(cfg.data_dir, "settings.json")
    if os.path.exists(settings):
        try:
            with open(settings, "r", encoding="utf-8") as f:
                _apply(cfg, json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

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
    if cfg.mode not in ("auto", "draft"):
        cfg.mode = "auto"
    return cfg


def save_settings(cfg: Config) -> str:
    """Сохранить настройки из UI в data/settings.json. Возвращает путь."""
    os.makedirs(cfg.data_dir, exist_ok=True)
    path = os.path.join(cfg.data_dir, "settings.json")
    data = {k: getattr(cfg, k) for k in _FIELDS if k != "data_dir"}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
