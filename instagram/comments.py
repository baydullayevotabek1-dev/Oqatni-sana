"""Kommentlarni o'qish va avtomatik javob berish.

Har safar oxirgi N ta post tekshiriladi. Yangi (hali javob berilmagan)
kommentlar Gemini'ga beriladi, u:
  * javob matnini yozadi (komment qaysi tilda bo'lsa — o'sha tilda),
  * yoki spam/haqorat bo'lsa "hide" (yashirish) deb belgilaydi,
  * yoki javob shart bo'lmasa "ignore" deydi.

Ikki marta javob bermaslik uchun har bir komment ID bazaga yoziladi.
Birinchi ishga tushirishda eski kommentlarga to'satdan javob yozib
yubormaslik uchun `--seed` rejimi bor: mavjud kommentlar "ko'rilgan" deb
belgilanadi, javob yozilmaydi.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re

from . import store
from .api import InstagramClient, InstagramError
from .config import Config

logger = logging.getLogger(__name__)

MAX_REPLY_CHARS = 300

_client = None
_client_checked = False


def _gemini(cfg: Config):
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    if not cfg.gemini_key:
        return None
    try:
        from google import genai

        _client = genai.Client(api_key=cfg.gemini_key)
    except Exception:
        logger.exception("Gemini mijozini ishga tushirib bo'lmadi.")
        _client = None
    return _client


_REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["reply", "hide", "ignore"]},
        "reply": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["action", "reply"],
}


def _parse_ts(raw: str) -> dt.datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _is_recent(raw: str, hours: int) -> bool:
    if hours <= 0:
        return True
    parsed = _parse_ts(raw)
    if parsed is None:
        return True
    age = dt.datetime.now(dt.timezone.utc) - parsed
    return age <= dt.timedelta(hours=hours)


def compose_reply(cfg: Config, comment_text: str, username: str,
                  post_caption: str) -> tuple[str, str]:
    """(action, reply) qaytaradi: reply / hide / ignore."""
    client = _gemini(cfg)
    if client is None:
        # Gemini yo'q — juda oddiy, xavfsiz javob
        return "reply", "Rahmat! 🙌"

    brand_line = f"Sahifa nomi: {cfg.brand}. " if cfg.brand else ""
    rules = f"\nQo'shimcha qoidalar: {cfg.extra_rules}" if cfg.extra_rules else ""
    prompt = (
        "Sen Instagram sahifasining SMM menejerisan va kommentlarga javob "
        f"yozasan. {brand_line}"
        f"Sahifa mavzusi: {cfg.topic or 'umumiy'}. Uslub: {cfg.tone}."
        f"{rules}\n\n"
        f"Post matni (qisqartirilgan): \"{post_caption[:600]}\"\n"
        f"Foydalanuvchi @{username} yozgan komment: \"{comment_text[:600]}\"\n\n"
        "Qaror qabul qil:\n"
        "- action=\"hide\": komment spam, reklama, haqorat, tahdid yoki "
        "firibgarlik bo'lsa (javob yozma).\n"
        "- action=\"ignore\": javob berish keraksiz bo'lsa (masalan bizning "
        "o'z javobimiz yoki ma'nosiz belgilar).\n"
        "- action=\"reply\": qolgan barcha holatda javob yoz.\n\n"
        "Javob qoidalari:\n"
        "* Komment qaysi tilda bo'lsa — SHU tilda javob ber (o'zbek/rus/"
        "ingliz; kirillcha yozsa — kirillcha).\n"
        f"* Qisqa: 1-2 jumla, {MAX_REPLY_CHARS} belgidan oshmasin.\n"
        "* Samimiy, hurmatli. 1-2 ta emoji bo'lsa yetarli.\n"
        "* Narx, muddat, mavjudlik yoki aniq raqamlarni O'YLAB TOPMA — "
        "bunday savolga \"Batafsil ma'lumot uchun Direct'ga yozing\" deb "
        "javob ber.\n"
        "* Salbiy/norozi kommentga bahslashmasdan, uzr so'rab, muammoni "
        "Direct'da hal qilishni taklif qil.\n"
        "* Foydalanuvchi ismini @ bilan takrorlashing shart emas."
    )

    try:
        response = client.models.generate_content(
            model=cfg.text_model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": _REPLY_SCHEMA,
            },
        )
        data = json.loads(response.text)
    except Exception as exc:
        logger.warning("Gemini komment javobini yozmadi (%s).", exc)
        return "ignore", ""

    action = str(data.get("action", "ignore")).lower()
    reply = re.sub(r"\s+", " ", str(data.get("reply", ""))).strip()
    if action == "reply" and not reply:
        action = "ignore"
    return action, reply[:MAX_REPLY_CHARS]


def _collect(media: dict, comments: list[dict], my_username: str,
             lookback_hours: int) -> list[dict]:
    """Javob berish kerak bo'lishi mumkin bo'lgan kommentlarni yig'adi.

    Ichma-ich (reply) kommentlar ham qo'shiladi — Instagram'da javob doim
    ustki (top-level) komment ostiga yoziladi, shuning uchun `reply_to`
    sifatida ustki kommentning ID si saqlanadi.
    """
    found: list[dict] = []
    for comment in comments:
        cid = str(comment.get("id", ""))
        username = str(comment.get("username", ""))
        if cid and username.lower() != my_username.lower():
            if _is_recent(comment.get("timestamp", ""), lookback_hours):
                found.append({
                    "id": cid,
                    "reply_to": cid,
                    "username": username,
                    "text": str(comment.get("text", "")),
                    "media_id": str(media.get("id", "")),
                })
        for reply in (comment.get("replies", {}) or {}).get("data", []):
            rid = str(reply.get("id", ""))
            r_user = str(reply.get("username", ""))
            if not rid or r_user.lower() == my_username.lower():
                continue
            if not _is_recent(reply.get("timestamp", ""), lookback_hours):
                continue
            found.append({
                "id": rid,
                "reply_to": cid,
                "username": r_user,
                "text": str(reply.get("text", "")),
                "media_id": str(media.get("id", "")),
            })
    return found


def run_once(cfg: Config, api: InstagramClient, seed: bool = False,
             dry_run: bool = False) -> dict:
    """Bitta tekshiruv sikli. Statistikani qaytaradi."""
    stats = {"checked": 0, "new": 0, "replied": 0, "hidden": 0,
             "ignored": 0, "errors": 0}

    try:
        my_username = str(api.account().get("username", ""))
        media_list = api.list_media(limit=cfg.comment_media_limit)
    except InstagramError as exc:
        logger.error("Postlar ro'yxatini olib bo'lmadi: %s %s", exc, exc.hint())
        stats["errors"] += 1
        return stats

    for media in media_list:
        media_id = str(media.get("id", ""))
        if not media_id:
            continue
        if not int(media.get("comments_count") or 0):
            continue
        try:
            comments = api.get_comments(media_id)
        except InstagramError as exc:
            logger.warning("Kommentlarni o'qib bo'lmadi (%s): %s", media_id, exc)
            stats["errors"] += 1
            continue

        stats["checked"] += len(comments)
        candidates = _collect(media, comments, my_username, cfg.comment_lookback_hours)
        already = store.handled_ids([c["id"] for c in candidates])
        fresh = [c for c in candidates if c["id"] not in already]
        stats["new"] += len(fresh)

        for item in fresh:
            if seed:
                store.save_reply(item["id"], media_id, item["username"],
                                 item["text"], "", "seen")
                continue

            action, reply_text = compose_reply(
                cfg, item["text"], item["username"], str(media.get("caption", ""))
            )
            logger.info("@%s: %r -> %s", item["username"], item["text"][:60], action)

            if dry_run:
                print(f"  @{item['username']}: {item['text'][:70]}")
                print(f"    -> [{action}] {reply_text}")
                continue

            try:
                if action == "reply":
                    api.reply_to_comment(item["reply_to"], reply_text)
                    stats["replied"] += 1
                elif action == "hide" and cfg.auto_hide_spam:
                    api.hide_comment(item["id"], True)
                    stats["hidden"] += 1
                else:
                    stats["ignored"] += 1
                store.save_reply(item["id"], media_id, item["username"],
                                 item["text"], reply_text, action)
            except InstagramError as exc:
                logger.error("Kommentga javob berilmadi: %s %s", exc, exc.hint())
                stats["errors"] += 1

    return stats
