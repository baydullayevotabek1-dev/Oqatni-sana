"""Post kontentini tayyorlash: internetdan mavzu topish + Gemini bilan yozish.

Ikki manba:
  * INTERNET — .env dagi IG_RSS_FEEDS ro'yxatidagi RSS/Atom lentalar. Yangi
    (hali ishlatilmagan) maqola olinadi, uning sarlavhasi/qisqacha mazmuni
    Gemini'ga "ilhom" sifatida beriladi. Matn ko'chirilmaydi — o'z so'zlari
    bilan qayta yoziladi va manba caption oxirida ko'rsatiladi.
  * GENERATSIYA — lenta bo'lmasa yoki yangi maqola topilmasa, Gemini mavzu
    (IG_TOPIC) bo'yicha o'zi post o'ylab topadi.
"""

from __future__ import annotations

import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests

from . import store
from .config import Config

logger = logging.getLogger(__name__)

MAX_CAPTION = 2200
MAX_HASHTAGS = 25
USER_AGENT = "Mozilla/5.0 (compatible; instagram-agent/1.0)"

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


@dataclass
class Source:
    title: str = ""
    summary: str = ""
    url: str = ""
    image_url: str = ""


@dataclass
class PostDraft:
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    image_prompt: str = ""
    alt_text: str = ""
    title: str = ""
    source_url: str = ""
    source_title: str = ""
    source_image: str = ""

    def full_caption(self, cfg: Config) -> str:
        """Caption + CTA + manba + hashtaglar — Instagram limitiga sig'dirilgan."""
        parts = [self.caption.strip()]
        if cfg.cta and cfg.cta.lower() not in self.caption.lower():
            parts.append(cfg.cta.strip())
        if self.source_url:
            parts.append(f"Manba: {self.source_url}")

        tags = []
        seen = set()
        for tag in list(self.hashtags) + list(cfg.hashtags):
            tag = tag.strip().lstrip("#")
            tag = re.sub(r"[^0-9A-Za-z_Ѐ-ӿ]", "", tag)
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                tags.append(f"#{tag}")
            if len(tags) >= MAX_HASHTAGS:
                break
        if tags:
            parts.append(" ".join(tags))

        caption = "\n\n".join(p for p in parts if p)
        return caption[:MAX_CAPTION]


# --------------------------------------------------------------------- #
# Internetdan mavzu topish
# --------------------------------------------------------------------- #
def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_feed(url: str, limit: int = 10) -> list[Source]:
    """RSS yoki Atom lentadan maqolalarni o'qiydi."""
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        logger.warning("Lentani o'qib bo'lmadi (%s): %s", exc, url)
        return []

    items: list[Source] = []
    # RSS 2.0
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag not in ("item", "entry"):
            continue
        title = summary = link = image = ""
        for child in node:
            ctag = child.tag.split("}")[-1].lower()
            if ctag == "title" and not title:
                title = _strip_html(child.text or "")
            elif ctag in ("description", "summary", "content") and not summary:
                # itertext() — ichida HTML teglari bo'lsa ham hammasini oladi
                summary = _strip_html("".join(child.itertext()))[:1200]
            elif ctag == "link" and not link:
                link = (child.get("href") or child.text or "").strip()
            elif ctag in ("enclosure", "thumbnail", "content") and not image:
                candidate = child.get("url") or ""
                if candidate and re.search(r"\.(jpe?g|png|webp)", candidate, re.I):
                    image = candidate
        if title and link:
            items.append(Source(title=title, summary=summary, url=link,
                                image_url=image))
        if len(items) >= limit:
            break
    return items


def _og_image(page_url: str) -> str:
    """Maqola sahifasidan og:image rasmini topadi."""
    try:
        resp = requests.get(page_url, timeout=25, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        head = resp.text[:200_000]
    except Exception:
        return ""
    match = re.search(
        r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]*content=["\']([^"\']+)',
        head, re.I,
    ) or re.search(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']og:image["\']',
        head, re.I,
    )
    return html.unescape(match.group(1)) if match else ""


def pick_source(cfg: Config) -> Source | None:
    """Hali ishlatilmagan yangi maqolani tanlaydi."""
    for feed_url in cfg.rss_feeds:
        for item in fetch_feed(feed_url):
            if not item.url or store.is_source_used(item.url):
                continue
            if not item.image_url:
                item.image_url = _og_image(item.url)
            return item
    return None


# --------------------------------------------------------------------- #
# Gemini bilan post yozish
# --------------------------------------------------------------------- #
_POST_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "image_prompt": {"type": "string"},
        "alt_text": {"type": "string"},
    },
    "required": ["title", "caption", "hashtags", "image_prompt"],
}

_LANG_NAMES = {
    "uz": "o'zbek (lotin)",
    "ru": "rus",
    "en": "ingliz",
    "uz-cyrl": "o'zbek (kirill)",
}


def _language_name(code: str) -> str:
    return _LANG_NAMES.get(code.lower(), code)


def generate_draft(cfg: Config, source: Source | None = None,
                   topic_override: str = "") -> PostDraft:
    """Gemini orqali caption, hashtag va rasm promptini yaratadi."""
    topic = topic_override or cfg.topic or "umumiy foydali maslahatlar"
    recent = store.recent_captions(6)
    client = _gemini(cfg)

    if client is None:
        return _fallback_draft(cfg, source, topic)

    brand_line = f"Akkaunt/brend nomi: {cfg.brand}.\n" if cfg.brand else ""
    recent_block = ""
    if recent:
        joined = "\n".join(f"- {c.splitlines()[0][:120]}" for c in recent)
        recent_block = (
            "\nOxirgi postlar (MAVZUSI VA BOSHLANISHI TAKRORLANMASIN):\n"
            f"{joined}\n"
        )
    source_block = ""
    if source:
        source_block = (
            "\nQuyidagi yangilik ilhom manbasi. Matnini KO'CHIRMA — faqat "
            "asosiy g'oyani o'z so'zlaring bilan, o'quvchiga foydali qilib "
            "qayta yoz:\n"
            f"Sarlavha: {source.title}\n"
            f"Qisqacha: {source.summary[:800]}\n"
        )
    extra = f"\nQo'shimcha qoidalar: {cfg.extra_rules}\n" if cfg.extra_rules else ""

    prompt = (
        "Sen tajribali SMM mutaxassisisan va Instagram uchun post yozasan.\n"
        f"{brand_line}"
        f"Sahifa mavzusi: {topic}.\n"
        f"Yozish tili: {_language_name(cfg.language)}.\n"
        f"Uslub: {cfg.tone}.\n"
        f"{source_block}{recent_block}{extra}\n"
        "Quyidagilarni tayyorla:\n"
        "1) title — postning qisqa sarlavhasi (5-8 so'z, rasmga yozish uchun).\n"
        "2) caption — Instagram matni. Birinchi qator diqqatni tortadigan "
        "ilgak (hook) bo'lsin. 400-900 belgi. Qisqa xatboshilar, kerak "
        "bo'lsa 3-5 ta punktli ro'yxat. Emojidan o'lchov bilan foydalan "
        "(3-6 ta). Oxirida o'quvchiga savol yoki harakatga chaqiruv. "
        "Hashtaglarni caption ichiga YOZMA — ular alohida maydonda.\n"
        "3) hashtags — 8-15 ta mos hashtag, '#' belgisisiz, faqat so'zlar.\n"
        "4) image_prompt — shu postga mos rasmni generatsiya qilish uchun "
        "ingliz tilidagi batafsil tavsif (fotorealistik yoki zamonaviy "
        "grafik uslub; rasmda yozuv bo'lmasin).\n"
        "5) alt_text — ko'zi ojiz foydalanuvchilar uchun rasm tavsifi "
        "(1 jumla).\n\n"
        "MUHIM: yolg'on faktlar, aniq raqamlar yoki narx o'ylab topma. "
        "Va'da qilib bo'lmaydigan gaplarni yozma."
    )

    try:
        response = client.models.generate_content(
            model=cfg.text_model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": _POST_SCHEMA,
            },
        )
        data = json.loads(response.text)
    except Exception as exc:
        logger.warning("Gemini post yozib bermadi (%s) — zaxira variant.", exc)
        return _fallback_draft(cfg, source, topic)

    draft = PostDraft(
        caption=str(data.get("caption", "")).strip(),
        hashtags=[str(t) for t in data.get("hashtags", []) if str(t).strip()],
        image_prompt=str(data.get("image_prompt", "")).strip(),
        alt_text=str(data.get("alt_text", "")).strip(),
        title=str(data.get("title", "")).strip(),
    )
    if source:
        draft.source_url = source.url
        draft.source_title = source.title
        draft.source_image = source.image_url
    if not draft.caption:
        return _fallback_draft(cfg, source, topic)
    return draft


def _fallback_draft(cfg: Config, source: Source | None, topic: str) -> PostDraft:
    """Gemini ishlamasa — hech bo'lmasa oddiy, xavfsiz post."""
    if source:
        caption = f"{source.title}\n\n{source.summary[:500]}".strip()
        title = source.title
    else:
        title = cfg.topic or cfg.brand or "Yangilik"
        caption = f"{title}\n\nBugungi yangilik va foydali ma'lumotlar uchun sahifamizni kuzatib boring."
    return PostDraft(
        caption=caption,
        hashtags=list(cfg.hashtags),
        image_prompt="",
        alt_text=title,
        title=title,
        source_url=source.url if source else "",
        source_title=source.title if source else "",
        source_image=source.image_url if source else "",
    )


def build_draft(cfg: Config, topic_override: str = "",
                use_internet: bool = True) -> PostDraft:
    """Post uchun to'liq qoralama: manba topish + matn yozish."""
    source = None
    if use_internet and cfg.rss_feeds:
        try:
            source = pick_source(cfg)
        except Exception:
            logger.exception("Manba tanlashda xato — generatsiyaga o'tamiz.")
    if source:
        logger.info("Internetdan mavzu olindi: %s", source.title[:80])
    return generate_draft(cfg, source, topic_override)
