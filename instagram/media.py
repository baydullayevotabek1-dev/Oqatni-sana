"""Rasm tayyorlash: generatsiya, yuklab olish, formatlash va internetga chiqarish.

Instagram rasmni fayl sifatida QABUL QILMAYDI — u rasmni ochiq (public) URL
orqali o'zi yuklab oladi. Shuning uchun har bir rasm uchun:

  1) rasm olinadi  — Gemini generatsiyasi / internetdagi manbadan / matnli
     kartochka (zaxira variant);
  2) JPEG'ga o'giriladi va Instagram talab qiladigan o'lchamga keltiriladi;
  3) ochiq manzilga chiqariladi — PUBLIC_BASE_URL (agentning o'z serveri)
     yoki IMGBB_API_KEY orqali.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import re
import time
import uuid
from pathlib import Path

import requests

from .config import MEDIA_DIR, Config

logger = logging.getLogger(__name__)

# Instagram talablari: JPEG, tomonlar nisbati 4:5 … 1.91:1, eni ≤ 1440px.
MAX_WIDTH = 1440
FEED_SIZE = (1080, 1350)          # 4:5 — lentada eng ko'p joy egallaydi
MIN_RATIO = 4 / 5
MAX_RATIO = 1.91
MAX_BYTES = 8 * 1024 * 1024

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

_gemini_client = None
_gemini_checked = False


def _client(cfg: Config):
    global _gemini_client, _gemini_checked
    if _gemini_checked:
        return _gemini_client
    _gemini_checked = True
    if not cfg.gemini_key:
        return None
    try:
        from google import genai

        _gemini_client = genai.Client(api_key=cfg.gemini_key)
    except Exception:
        logger.exception("Gemini mijozini ishga tushirib bo'lmadi.")
        _gemini_client = None
    return _gemini_client


def _new_path(suffix: str = ".jpg") -> Path:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:10]}{suffix}"
    return MEDIA_DIR / name


# --------------------------------------------------------------------- #
# 1) Rasm olish
# --------------------------------------------------------------------- #
def generate_image(cfg: Config, prompt: str) -> Path | None:
    """Gemini bilan rasm yaratadi. Muvaffaqiyatsiz bo'lsa None."""
    client = _client(cfg)
    if client is None or not prompt:
        return None

    full_prompt = (
        f"{prompt}\n\n"
        "Instagram feed post uchun kvadratga yaqin (4:5) vertikal rasm. "
        "Yuqori sifatli, professional, toza kompozitsiya. "
        "Rasmda matn (yozuv) bo'lmasin yoki juda kam bo'lsin."
    )
    try:
        response = client.models.generate_content(
            model=cfg.image_model, contents=full_prompt
        )
    except Exception as exc:
        logger.warning("Rasm generatsiyasi ishlamadi (%s).", exc)
        return None

    try:
        for candidate in response.candidates or []:
            for part in getattr(candidate.content, "parts", []) or []:
                inline = getattr(part, "inline_data", None)
                if inline is None or not getattr(inline, "data", None):
                    continue
                raw = inline.data
                if isinstance(raw, str):          # ba'zi versiyalarda base64 matn
                    raw = base64.b64decode(raw)
                suffix = mimetypes.guess_extension(
                    getattr(inline, "mime_type", "image/png") or "image/png"
                ) or ".png"
                path = _new_path(suffix)
                path.write_bytes(raw)
                logger.info("Rasm generatsiya qilindi: %s", path.name)
                return path
    except Exception:
        logger.exception("Generatsiya javobidan rasmni ajratib bo'lmadi.")
    logger.warning("Gemini rasm qaytarmadi.")
    return None


def download_image(url: str) -> Path | None:
    """Internetdagi rasmni yuklab oladi (manba maqolasining rasmi va h.k.)."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        resp = requests.get(url, timeout=45, stream=True,
                            headers={"User-Agent": "Mozilla/5.0 (ig-agent)"})
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "image" not in ctype:
            logger.warning("Manzil rasm emas (%s): %s", ctype, url)
            return None
        suffix = mimetypes.guess_extension(ctype.split(";")[0].strip()) or ".jpg"
        path = _new_path(suffix)
        size = 0
        with path.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    logger.warning("Rasm juda katta, tashlab yuborildi: %s", url)
                    fh.close()
                    path.unlink(missing_ok=True)
                    return None
                fh.write(chunk)
        return path
    except Exception as exc:
        logger.warning("Rasmni yuklab bo'lmadi (%s): %s", exc, url)
        return None


def _load_font(size: int):
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)      # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def text_card(title: str, subtitle: str = "", brand: str = "") -> Path | None:
    """Zaxira variant: matnli kartochka (rasm generatsiyasi ishlamaganda).

    Shunday qilib agent hech qachon "rasm yo'q" deb to'xtab qolmaydi.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("Pillow o'rnatilmagan — matnli kartochka yasab bo'lmadi.")
        return None

    if subtitle.strip().lower() == title.strip().lower():
        subtitle = ""

    width, height = FEED_SIZE
    image = Image.new("RGB", (width, height), "#0f172a")
    draw = ImageDraw.Draw(image)

    # yumshoq gradient fon
    top, bottom = (15, 23, 42), (37, 99, 235)
    for y in range(height):
        ratio = y / height
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)

    margin = 90
    title_font = _load_font(76)
    sub_font = _load_font(42)
    brand_font = _load_font(36)

    title_lines = _wrap(draw, title.strip(), title_font, width - 2 * margin)[:5]
    sub_lines = (_wrap(draw, subtitle.strip(), sub_font, width - 2 * margin)[:4]
                 if subtitle else [])

    block_height = len(title_lines) * 92 + (30 + len(sub_lines) * 56 if sub_lines else 0)
    y = max(margin, (height - block_height) // 2)

    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill="#ffffff")
        y += 92
    if sub_lines:
        y += 30
        for line in sub_lines:
            draw.text((margin, y), line, font=sub_font, fill="#dbeafe")
            y += 56

    if brand:
        draw.text((margin, height - margin - 40), brand.upper(),
                  font=brand_font, fill="#93c5fd")

    path = _new_path(".jpg")
    image.save(path, "JPEG", quality=92)
    logger.info("Matnli kartochka yaratildi: %s", path.name)
    return path


# --------------------------------------------------------------------- #
# 2) Instagram formatiga keltirish
# --------------------------------------------------------------------- #
def prepare_jpeg(path: Path) -> Path:
    """JPEG'ga o'giradi, o'lchamini va tomonlar nisbatini talabga moslaydi."""
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow yo'q — rasm o'zgartirilmadi.")
        return path

    with Image.open(path) as img:
        img = img.convert("RGB")
        width, height = img.size
        ratio = width / height

        # tomonlar nisbati chegaradan chiqsa — kesamiz
        if ratio < MIN_RATIO:
            new_height = int(width / MIN_RATIO)
            top = (height - new_height) // 2
            img = img.crop((0, top, width, top + new_height))
        elif ratio > MAX_RATIO:
            new_width = int(height * MAX_RATIO)
            left = (width - new_width) // 2
            img = img.crop((left, 0, left + new_width, height))

        if img.width > MAX_WIDTH:
            new_height = int(img.height * MAX_WIDTH / img.width)
            img = img.resize((MAX_WIDTH, new_height), Image.LANCZOS)

        out = path if path.suffix.lower() in (".jpg", ".jpeg") else path.with_suffix(".jpg")
        quality = 92
        img.save(out, "JPEG", quality=quality, optimize=True)
        while out.stat().st_size > MAX_BYTES and quality > 50:
            quality -= 10
            img.save(out, "JPEG", quality=quality, optimize=True)

    if out != path:
        path.unlink(missing_ok=True)
    return out


# --------------------------------------------------------------------- #
# 3) Ochiq (public) manzilga chiqarish
# --------------------------------------------------------------------- #
def _upload_to_imgbb(cfg: Config, path: Path) -> str:
    data = base64.b64encode(path.read_bytes()).decode()
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": cfg.imgbb_key, "image": data, "name": path.stem},
        timeout=120,
    )
    payload = resp.json()
    if not payload.get("success"):
        raise RuntimeError(f"imgbb yuklashda xato: {payload}")
    return payload["data"]["url"]


def public_url(cfg: Config, path: Path) -> str:
    """Rasmni Instagram ko'ra oladigan manzilga chiqaradi.

    1-variant: agentning o'z HTTP serveri (PUBLIC_BASE_URL/media/<fayl>)
    2-variant: imgbb (bepul, IMGBB_API_KEY kerak)
    """
    if cfg.public_base_url:
        return f"{cfg.public_base_url}/media/{path.name}"
    if cfg.imgbb_key:
        return _upload_to_imgbb(cfg, path)
    raise RuntimeError(
        "Rasmni internetga chiqarib bo'lmadi: PUBLIC_BASE_URL yoki "
        "IMGBB_API_KEY sozlanmagan."
    )


# --------------------------------------------------------------------- #
# Umumiy funksiya
# --------------------------------------------------------------------- #
def build_image(cfg: Config, image_prompt: str = "", source_image: str = "",
                title: str = "", subtitle: str = "") -> Path | None:
    """Rasmni ustuvorlik tartibida tayyorlaydi va JPEG qilib qaytaradi.

    1) manbadagi tayyor rasm (agar berilgan bo'lsa)
    2) Gemini generatsiyasi
    3) matnli kartochka (zaxira)
    """
    path: Path | None = None
    if source_image:
        path = download_image(source_image)
    if path is None and image_prompt:
        path = generate_image(cfg, image_prompt)
    if path is None:
        path = text_card(title or cfg.topic or cfg.brand or "Yangilik",
                         subtitle, cfg.brand)
    if path is None:
        return None
    try:
        return prepare_jpeg(path)
    except Exception:
        logger.exception("Rasmni formatlashda xato — asl fayl ishlatiladi.")
        return path


def clean_old_media(days: int = 14) -> int:
    """Eski rasmlarni o'chiradi (diskni to'ldirmaslik uchun)."""
    if not MEDIA_DIR.exists():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for file in MEDIA_DIR.iterdir():
        try:
            if file.is_file() and file.stat().st_mtime < cutoff:
                file.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def safe_media_path(name: str) -> Path | None:
    """HTTP server uchun: fayl nomini tekshirib, xavfsiz yo'l qaytaradi."""
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", name):
        return None
    path = (MEDIA_DIR / name).resolve()
    try:
        path.relative_to(MEDIA_DIR.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None
