"""Alerting Telegram — même pattern que le notifier du pipeline HL :
TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID dans .env ; si absents, no-op
silencieux (log WARNING) — le scheduler continue sans alerting."""

from __future__ import annotations

import os

import httpx

from collectors.base import get_logger

log = get_logger("notify")
_PREFIX = {"info": "ℹ️", "warning": "⚠️", "error": "🔴"}


def send(text: str, level: str = "warning") -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        log.warning("telegram non configuré — alerte loggée seulement",
                    extra={"ctx": {"text": text[:120]}})
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat,
                             "text": f"{_PREFIX.get(level, '')} ROUGE — {text}"},
                       timeout=10)
        r.raise_for_status()
        return True
    except Exception as err:
        log.error("envoi telegram échoué", extra={"ctx": {"err": str(err)[:120]}})
        return False
