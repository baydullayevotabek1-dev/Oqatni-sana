"""Telegram orqali xabardor qilish va post tasdiqlash.

Bu yerda Telegram bot POLLING qilmaydi (mavjud ovqat boti bilan urishmasligi
uchun) — faqat HTTP API orqali xabar yuboriladi. Tasdiqlash tugmalari oddiy
havola (URL) tugmalari: ular agentning o'z serveridagi
`/ig/approve?token=…` manziliga olib boradi.
"""

from __future__ import annotations

import json
import logging

import requests

from .config import Config

logger = logging.getLogger(__name__)
TIMEOUT = 30


def _api(cfg: Config, method: str, payload: dict) -> dict | None:
    if not cfg.can_notify:
        return None
    url = f"https://api.telegram.org/bot{cfg.telegram_token}/{method}"
    try:
        resp = requests.post(url, data=payload, timeout=TIMEOUT)
        data = resp.json()
        if not data.get("ok"):
            logger.warning("Telegram xatosi (%s): %s", method, data.get("description"))
            return None
        return data.get("result")
    except Exception as exc:
        logger.warning("Telegram'ga xabar yuborilmadi (%s): %s", method, exc)
        return None


def send_message(cfg: Config, text: str, buttons: list[list[dict]] | None = None) -> None:
    payload = {
        "chat_id": cfg.notify_chat_id,
        "text": text[:4000],
        "disable_web_page_preview": "true",
    }
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    _api(cfg, "sendMessage", payload)


def send_photo(cfg: Config, photo_url: str, caption: str,
               buttons: list[list[dict]] | None = None) -> None:
    payload = {
        "chat_id": cfg.notify_chat_id,
        "photo": photo_url,
        "caption": caption[:1000],
    }
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    if _api(cfg, "sendPhoto", payload) is None:
        # Rasm yuborilmasa, hech bo'lmasa matn ketsin
        send_message(cfg, f"{caption[:3500]}\n\nRasm: {photo_url}", buttons)


def ask_approval(cfg: Config, post_id: int, token: str, media_url: str,
                 caption: str, is_video: bool = False) -> bool:
    """Postni Telegram'ga tasdiq uchun yuboradi. True — yuborildi."""
    if not cfg.can_notify or not cfg.public_base_url:
        return False
    base = cfg.public_base_url
    buttons = [[
        {"text": "✅ Joylash", "url": f"{base}/ig/approve?token={token}"},
        {"text": "❌ Bekor", "url": f"{base}/ig/reject?token={token}"},
    ]]
    text = f"🆕 Instagram post #{post_id} tayyor:\n\n{caption}"
    if is_video or not media_url:
        send_message(cfg, f"{text}\n\n🎬 {media_url}".strip(), buttons)
    else:
        send_photo(cfg, media_url, text, buttons)
    return True


def notify_published(cfg: Config, permalink: str, caption: str) -> None:
    first_line = caption.strip().splitlines()[0] if caption.strip() else ""
    send_message(
        cfg,
        f"✅ Instagram'ga post joylandi.\n\n{first_line}\n\n{permalink}".strip(),
    )


def notify_error(cfg: Config, what: str, error: str) -> None:
    send_message(cfg, f"⚠️ Instagram agent xatosi ({what}):\n{error[:1500]}")
