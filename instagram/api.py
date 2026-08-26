"""Instagram Graph API mijozi.

Rasmiy API bilan ishlaydi (Business yoki Creator akkaunt talab qilinadi).
Qo'llab-quvvatlanadigan amallar:

  * akkaunt ma'lumotini olish va sutkalik post limitini bilish
  * rasm / karusel / Reels joylash (ikki bosqich: container -> publish)
  * oxirgi postlarni va ulardagi kommentlarni o'qish
  * kommentga javob yozish, kommentni yashirish yoki o'chirish
  * uzoq muddatli tokenni yangilash (60 kun)

Instagram rasmni o'zi yuklab olgani uchun `image_url` ochiq (public) HTTPS
manzil bo'lishi shart — buni `media.py` hal qiladi.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .config import Config

logger = logging.getLogger(__name__)

TIMEOUT = 60


class InstagramError(RuntimeError):
    """Graph API qaytargan xato (tushunarli xabar bilan)."""

    def __init__(self, message: str, code: int | None = None,
                 subcode: int | None = None, error_type: str = "",
                 status: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.subcode = subcode
        self.error_type = error_type
        self.status = status

    def hint(self) -> str:
        """Tez-tez uchraydigan xatolar uchun o'zbekcha maslahat."""
        code, sub = self.code, self.subcode
        if code == 190:
            return ("Token eskirgan yoki bekor qilingan. Yangi uzoq muddatli "
                    "token oling (README: 'Tokenni yangilash').")
        if code in (10, 200, 803):
            return ("Ruxsat (permission) yetishmayapti. Meta ilovangizda "
                    "instagram_business_basic, "
                    "instagram_business_content_publish va "
                    "instagram_business_manage_comments ruxsatlari yoqilganini "
                    "tekshiring.")
        if code == 9007 or (code == 100 and sub == 2207050):
            return ("Sutkalik post limiti (24 soatda 25 ta) tugagan yoki rasm "
                    "formati mos emas — JPEG bo'lishi kerak.")
        if code == 100 and sub in (2207003, 2207004, 2207020):
            return ("Instagram rasm URL'ini yuklab ololmadi. Manzil ochiq "
                    "(public) HTTPS bo'lishi va to'g'ridan-to'g'ri .jpg "
                    "faylni qaytarishi kerak.")
        if code == 4 or code == 17 or code == 32:
            return "So'rovlar chastotasi limitiga yetildi — biroz kutib qayta urinib ko'ring."
        if code == 24 or self.error_type == "OAuthException":
            return ("Akkaunt Business/Creator turida emas yoki ilovaga ulanmagan. "
                    "Instagram: Settings -> Account type -> Professional.")
        return ""


class InstagramClient:
    """Graph API ustidagi yupqa qatlam."""

    def __init__(self, cfg: Config, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        self._me_cache: dict[str, Any] | None = None

    # ------------------------------------------------------------------ #
    # Past daraja
    # ------------------------------------------------------------------ #
    def _url(self, path: str) -> str:
        path = path.lstrip("/")
        return f"{self.cfg.api_base}/{self.cfg.graph_version}/{path}"

    def _request(self, method: str, path: str, params: dict | None = None,
                 data: dict | None = None, retries: int = 2) -> dict:
        params = dict(params or {})
        data = dict(data or {})
        if method.upper() in ("GET", "DELETE"):
            params["access_token"] = self.cfg.access_token
        else:
            data["access_token"] = self.cfg.access_token

        url = self._url(path)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.request(
                    method, url, params=params or None, data=data or None,
                    timeout=TIMEOUT,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise InstagramError(f"Tarmoq xatosi: {exc}") from exc

            try:
                payload = resp.json()
            except ValueError:
                payload = {}

            if resp.status_code >= 400 or "error" in payload:
                err = payload.get("error", {}) if isinstance(payload, dict) else {}
                error = InstagramError(
                    err.get("message") or f"HTTP {resp.status_code}",
                    code=err.get("code"),
                    subcode=err.get("error_subcode"),
                    error_type=err.get("type", ""),
                    status=resp.status_code,
                )
                # 429 / 5xx — vaqtinchalik, qayta urinamiz
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    logger.warning("Instagram %s qaytardi, qayta urinamiz…", resp.status_code)
                    time.sleep(3 * (attempt + 1))
                    last_error = error
                    continue
                raise error

            return payload if isinstance(payload, dict) else {"data": payload}

        raise InstagramError(f"So'rov bajarilmadi: {last_error}")

    def _get(self, path: str, **params) -> dict:
        return self._request("GET", path, params=params)

    def _post(self, path: str, **data) -> dict:
        return self._request("POST", path, data=data)

    # ------------------------------------------------------------------ #
    # Akkaunt
    # ------------------------------------------------------------------ #
    def account(self, refresh: bool = False) -> dict:
        """Akkaunt ma'lumoti: id, username, followers_count, media_count."""
        if self._me_cache is not None and not refresh:
            return self._me_cache
        fields = "id,username,media_count"
        if self.cfg.login_type == "instagram":
            fields += ",account_type,followers_count,profile_picture_url"
        else:
            fields += ",followers_count,profile_picture_url"
        self._me_cache = self._get(self.cfg.user_id or "me", fields=fields)
        return self._me_cache

    @property
    def ig_id(self) -> str:
        """Publish endpointlari uchun akkaunt ID (kerak bo'lsa aniqlanadi)."""
        if self.cfg.user_id and self.cfg.user_id != "me":
            return self.cfg.user_id
        return str(self.account().get("id", "me"))

    def publishing_limit(self) -> dict:
        """Oxirgi 24 soatda nechta post joylanganini qaytaradi."""
        data = self._get(
            f"{self.ig_id}/content_publishing_limit",
            fields="config,quota_usage",
        )
        items = data.get("data") or [{}]
        return items[0] if items else {}

    def refresh_token(self) -> dict:
        """Instagram Login tokenini yana 60 kunga uzaytiradi."""
        if self.cfg.login_type != "instagram":
            raise InstagramError(
                "Facebook login tokeni boshqacha yangilanadi — README ga qarang."
            )
        resp = self.session.get(
            "https://graph.instagram.com/refresh_access_token",
            params={
                "grant_type": "ig_refresh_token",
                "access_token": self.cfg.access_token,
            },
            timeout=TIMEOUT,
        )
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400 or "error" in payload:
            err = payload.get("error", {})
            raise InstagramError(err.get("message", "Tokenni yangilab bo'lmadi"),
                                 code=err.get("code"))
        return payload

    # ------------------------------------------------------------------ #
    # Post joylash
    # ------------------------------------------------------------------ #
    def create_image_container(self, image_url: str, caption: str = "",
                               alt_text: str = "",
                               is_carousel_item: bool = False) -> str:
        data: dict[str, Any] = {"image_url": image_url}
        if caption and not is_carousel_item:
            data["caption"] = caption
        if alt_text:
            data["alt_text"] = alt_text[:1000]
        if is_carousel_item:
            data["is_carousel_item"] = "true"
        return str(self._post(f"{self.ig_id}/media", **data)["id"])

    def create_carousel_container(self, children: list[str], caption: str = "") -> str:
        data: dict[str, Any] = {
            "media_type": "CAROUSEL",
            "children": ",".join(children),
        }
        if caption:
            data["caption"] = caption
        return str(self._post(f"{self.ig_id}/media", **data)["id"])

    def create_reel_container(self, video_url: str, caption: str = "",
                              cover_url: str = "", share_to_feed: bool = True) -> str:
        data: dict[str, Any] = {"media_type": "REELS", "video_url": video_url}
        if caption:
            data["caption"] = caption
        if cover_url:
            data["cover_url"] = cover_url
        data["share_to_feed"] = "true" if share_to_feed else "false"
        return str(self._post(f"{self.ig_id}/media", **data)["id"])

    def create_story_container(self, image_url: str) -> str:
        return str(self._post(
            f"{self.ig_id}/media", media_type="STORIES", image_url=image_url
        )["id"])

    def container_status(self, creation_id: str) -> str:
        data = self._get(creation_id, fields="status_code,status")
        return str(data.get("status_code", "")).upper()

    def wait_until_ready(self, creation_id: str, timeout: int = 300,
                         interval: int = 5) -> None:
        """Container tayyor bo'lishini kutadi (video/Reels uchun majburiy)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.container_status(creation_id)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(
                    f"Media tayyorlanmadi (status={status}). Rasm/video "
                    "formatini tekshiring."
                )
            time.sleep(interval)
        raise InstagramError("Media tayyorlanishini kutish vaqti tugadi.")

    def publish(self, creation_id: str) -> dict:
        """Tayyor containerni Instagram'ga chiqaradi. media_id qaytadi."""
        result = self._post(f"{self.ig_id}/media_publish", creation_id=creation_id)
        media_id = str(result.get("id", ""))
        if not media_id:
            raise InstagramError("Publish javobida media id yo'q.")
        return self.media_info(media_id)

    def publish_photo(self, image_url: str, caption: str = "",
                      alt_text: str = "") -> dict:
        """Rasm joylashning to'liq sikli: container -> kutish -> publish."""
        creation_id = self.create_image_container(image_url, caption, alt_text)
        # Rasm odatda darrov tayyor bo'ladi, lekin sekin serverlarda kutish kerak.
        try:
            self.wait_until_ready(creation_id, timeout=120, interval=3)
        except InstagramError as exc:
            logger.warning("Container statusini o'qib bo'lmadi (%s), publish'ga o'tamiz.", exc)
        return self.publish(creation_id)

    def media_info(self, media_id: str) -> dict:
        return self._get(
            media_id,
            fields="id,caption,media_type,media_url,permalink,timestamp,"
                   "like_count,comments_count",
        )

    def list_media(self, limit: int = 10) -> list[dict]:
        data = self._get(
            f"{self.ig_id}/media",
            fields="id,caption,media_type,permalink,timestamp,comments_count",
            limit=limit,
        )
        return list(data.get("data", []))

    # ------------------------------------------------------------------ #
    # Kommentlar
    # ------------------------------------------------------------------ #
    def get_comments(self, media_id: str, limit: int = 50) -> list[dict]:
        data = self._get(
            f"{media_id}/comments",
            fields="id,text,username,timestamp,like_count,hidden,"
                   "replies{id,text,username,timestamp}",
            limit=limit,
        )
        return list(data.get("data", []))

    def reply_to_comment(self, comment_id: str, message: str) -> dict:
        return self._post(f"{comment_id}/replies", message=message[:2200])

    def hide_comment(self, comment_id: str, hide: bool = True) -> dict:
        return self._post(comment_id, hide="true" if hide else "false")

    def delete_comment(self, comment_id: str) -> dict:
        return self._request("DELETE", comment_id)
