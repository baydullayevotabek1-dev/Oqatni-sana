"""Instagram agentining barcha sozlamalari (.env / muhit o'zgaruvchilari).

Bitta joyda yig'ilgan: kalitlar, kontent uslubi, jadval, komment qoidalari.
`Config.from_env()` chaqirilganda hamma narsa o'qiladi va tekshiriladi.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / "media"
DB_PATH = BASE_DIR / "instagram.db"

# Graph API versiyasi ~2 yilda eskiradi. Xato bersa .env da IG_GRAPH_VERSION
# ni yangisiga o'zgartirish yetarli.
DEFAULT_GRAPH_VERSION = "v23.0"


def _get(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value else default


def _bool(name: str, default: bool = False) -> bool:
    raw = _get(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "ha", "y")


def _int(name: str, default: int) -> int:
    raw = _get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _csv(name: str, default: tuple[str, ...] = ()) -> list[str]:
    raw = _get(name)
    if not raw:
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_times(raw: str) -> list[dt.time]:
    """ "10:00, 18:30" -> [time(10, 0), time(18, 30)] """
    times: list[dt.time] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour_str, _, minute_str = part.partition(":")
            times.append(dt.time(int(hour_str), int(minute_str or 0)))
        except ValueError:
            continue
    return sorted(set(times))


@dataclass
class Config:
    # --- Instagram kirish ---
    access_token: str = ""
    user_id: str = "me"
    login_type: str = "instagram"          # instagram | facebook
    api_base: str = "https://graph.instagram.com"
    graph_version: str = DEFAULT_GRAPH_VERSION

    # --- Rasmni internetga chiqarish (Instagram rasmni URL orqali oladi) ---
    public_base_url: str = ""              # masalan https://ig-agent.onrender.com
    imgbb_key: str = ""

    # --- Sun'iy intellekt ---
    gemini_key: str = ""
    text_model: str = "gemini-2.5-flash"
    image_model: str = "gemini-2.5-flash-image"

    # --- Brend / kontent uslubi ---
    brand: str = ""
    topic: str = ""
    language: str = "uz"
    tone: str = "do'stona, ishonchli, professional"
    cta: str = ""
    hashtags: list[str] = field(default_factory=list)
    extra_rules: str = ""
    rss_feeds: list[str] = field(default_factory=list)
    use_source_image: bool = False     # manbadagi tayyor rasmni olish (mualliflik huquqi!)

    # --- Jadval ---
    post_times: list[dt.time] = field(default_factory=list)
    timezone: str = "Asia/Tashkent"
    max_posts_per_day: int = 2

    # --- Kommentlar ---
    auto_reply: bool = True
    comment_poll_minutes: int = 15
    comment_lookback_hours: int = 72
    comment_media_limit: int = 10
    auto_hide_spam: bool = False

    # --- Tasdiqlash va xabardor qilish (Telegram orqali) ---
    require_approval: bool = False
    telegram_token: str = ""
    notify_chat_id: str = ""

    # --- Server ---
    port: int = 8080

    @classmethod
    def from_env(cls) -> "Config":
        login_type = (_get("IG_LOGIN", "instagram") or "instagram").lower()
        if login_type not in ("instagram", "facebook"):
            login_type = "instagram"
        default_base = (
            "https://graph.instagram.com"
            if login_type == "instagram"
            else "https://graph.facebook.com"
        )
        return cls(
            access_token=_get("IG_ACCESS_TOKEN"),
            user_id=_get("IG_USER_ID", "me" if login_type == "instagram" else ""),
            login_type=login_type,
            api_base=_get("IG_API_BASE", default_base).rstrip("/"),
            graph_version=_get("IG_GRAPH_VERSION", DEFAULT_GRAPH_VERSION),
            public_base_url=_get("PUBLIC_BASE_URL").rstrip("/"),
            imgbb_key=_get("IMGBB_API_KEY"),
            gemini_key=_get("GEMINI_API_KEY"),
            text_model=_get("IG_TEXT_MODEL", "gemini-2.5-flash"),
            image_model=_get("IG_IMAGE_MODEL", "gemini-2.5-flash-image"),
            brand=_get("IG_BRAND"),
            topic=_get("IG_TOPIC"),
            language=_get("IG_LANGUAGE", "uz"),
            tone=_get("IG_TONE", "do'stona, ishonchli, professional"),
            cta=_get("IG_CTA"),
            hashtags=_csv("IG_HASHTAGS"),
            extra_rules=_get("IG_EXTRA_RULES"),
            rss_feeds=_csv("IG_RSS_FEEDS"),
            use_source_image=_bool("IG_USE_SOURCE_IMAGE", False),
            post_times=parse_times(_get("IG_POST_TIMES", "10:00,18:00")),
            timezone=_get("IG_TIMEZONE", "Asia/Tashkent"),
            max_posts_per_day=_int("IG_MAX_POSTS_PER_DAY", 2),
            auto_reply=_bool("IG_AUTO_REPLY", True),
            comment_poll_minutes=max(1, _int("IG_COMMENT_POLL_MINUTES", 15)),
            comment_lookback_hours=_int("IG_COMMENT_LOOKBACK_HOURS", 72),
            comment_media_limit=_int("IG_COMMENT_MEDIA_LIMIT", 10),
            auto_hide_spam=_bool("IG_AUTO_HIDE_SPAM", False),
            require_approval=_bool("IG_REQUIRE_APPROVAL", False),
            telegram_token=_get("IG_TELEGRAM_TOKEN") or _get("BOT_TOKEN"),
            notify_chat_id=_get("IG_NOTIFY_CHAT_ID"),
            port=_int("PORT", 8080),
        )

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception:
            return ZoneInfo("UTC")

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_key)

    @property
    def can_host_media(self) -> bool:
        return bool(self.public_base_url or self.imgbb_key)

    @property
    def can_notify(self) -> bool:
        return bool(self.telegram_token and self.notify_chat_id)

    def problems(self) -> list[str]:
        """Sozlamalardagi kamchiliklarni (xato bo'lishi mumkin joylarni) qaytaradi."""
        issues: list[str] = []
        if not self.access_token:
            issues.append("IG_ACCESS_TOKEN yo'q — Instagram'ga ulanib bo'lmaydi.")
        if not self.user_id:
            issues.append("IG_USER_ID yo'q (Facebook login uchun majburiy).")
        if not self.gemini_key:
            issues.append(
                "GEMINI_API_KEY yo'q — matn va rasm generatsiyasi ishlamaydi "
                "(faqat qo'lda berilgan caption/rasm bilan post qilish mumkin)."
            )
        if not self.can_host_media:
            issues.append(
                "PUBLIC_BASE_URL ham, IMGBB_API_KEY ham yo'q — o'zi yaratgan "
                "rasmni Instagram ko'ra oladigan manzilga chiqarib bo'lmaydi."
            )
        if self.require_approval and not self.can_notify:
            issues.append(
                "IG_REQUIRE_APPROVAL=true, lekin Telegram (BOT_TOKEN + "
                "IG_NOTIFY_CHAT_ID) sozlanmagan — tasdiq so'rovi yuborilmaydi."
            )
        if self.require_approval and not self.public_base_url:
            issues.append(
                "Tasdiqlash tugmalari ishlashi uchun PUBLIC_BASE_URL kerak."
            )
        if not self.post_times:
            issues.append("IG_POST_TIMES bo'sh — avtomatik post joylanmaydi.")
        return issues


def ensure_dirs() -> None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
