"""Instagram agenti — asosiy fayl (CLI + jadval + HTTP server).

Buyruqlar:
    python -m instagram.agent check              sozlamalar va ulanishni tekshirish
    python -m instagram.agent post               hozir bitta post joylash
    python -m instagram.agent post --dry-run     joylamasdan, faqat ko'rsatish
    python -m instagram.agent comments           kommentlarga javob berish
    python -m instagram.agent comments --seed    eski kommentlarni "ko'rilgan" qilish
    python -m instagram.agent drafts             kutilayotgan postlar ro'yxati
    python -m instagram.agent approve <id>       kutilayotgan postni joylash
    python -m instagram.agent refresh-token      tokenni yana 60 kunga uzaytirish
    python -m instagram.agent run                24/7 rejim (jadval bo'yicha)
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from . import comments as comments_mod
from . import content, media, notify, store
from .api import InstagramClient, InstagramError
from .config import Config, ensure_dirs

logger = logging.getLogger("instagram.agent")

CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".mp4": "video/mp4",
}


# --------------------------------------------------------------------- #
# Post yaratish va joylash
# --------------------------------------------------------------------- #
def publish_stored_post(cfg: Config, api: InstagramClient, post: dict) -> dict | None:
    """Bazadagi tayyor postni Instagram'ga chiqaradi (rasm yoki Reels)."""
    media_type = (post.get("media_type") or "IMAGE").upper()

    image_url = post.get("image_url") or ""
    if not image_url and post.get("image_path"):
        image_path = Path(post["image_path"])
        if not image_path.exists():
            store.mark_failed(post["id"], "Rasm fayli topilmadi.")
            return None
        image_url = media.public_url(cfg, image_path)
        store.update_post(post["id"], image_url=image_url)

    if media_type == "REELS" and not post.get("video_url"):
        store.mark_failed(post["id"], "Video manzili yo'q.")
        return None
    if media_type != "REELS" and not image_url:
        store.mark_failed(post["id"], "Rasm manzili yo'q.")
        return None

    try:
        if media_type == "REELS":
            creation_id = api.create_reel_container(
                post["video_url"], post.get("caption", ""), cover_url=image_url
            )
            api.wait_until_ready(creation_id)
            result = api.publish(creation_id)
        else:
            result = api.publish_photo(image_url, post.get("caption", ""),
                                       post.get("alt_text", ""))
    except InstagramError as exc:
        message = f"{exc}. {exc.hint()}".strip()
        logger.error("Post joylanmadi: %s", message)
        store.mark_failed(post["id"], message)
        notify.notify_error(cfg, "post", message)
        return None

    permalink = result.get("permalink", "")
    store.mark_published(post["id"], str(result.get("id", "")), permalink)
    if post.get("source_url"):
        store.mark_source_used(post["source_url"], post.get("source_title", ""))
    logger.info("Post joylandi: %s", permalink or result.get("id"))
    notify.notify_published(cfg, permalink, post.get("caption", ""))
    return result


def _to_public_url(cfg: Config, value: str) -> str:
    """URL bo'lsa — o'zini, mahalliy fayl bo'lsa — ochiq manzilini qaytaradi."""
    if value.startswith(("http://", "https://")):
        return value
    path = Path(value)
    if not path.exists():
        raise FileNotFoundError(f"Fayl topilmadi: {value}")
    if path.parent.resolve() != media.MEDIA_DIR.resolve():
        media.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        target = media.MEDIA_DIR / path.name
        target.write_bytes(path.read_bytes())
        path = target
    return media.public_url(cfg, path)


def create_post(cfg: Config, api: InstagramClient, topic: str = "",
                caption_override: str = "", image_override: str = "",
                video_override: str = "", use_internet: bool = True,
                dry_run: bool = False, skip_approval: bool = False) -> dict | None:
    """To'liq sikl: mavzu -> matn -> rasm -> (tasdiq) -> joylash."""
    ensure_dirs()

    # 1) Matn
    if caption_override:
        draft = content.PostDraft(caption=caption_override, title=topic or "")
    else:
        draft = content.build_draft(cfg, topic_override=topic,
                                    use_internet=use_internet)
    full_caption = draft.full_caption(cfg)

    # 2) Rasm (Reels uchun bu — muqova; berilmasa Instagram o'zi kadr tanlaydi)
    image_path: Path | None = None
    direct_url = ""
    if image_override.startswith(("http://", "https://")):
        direct_url = image_override
    elif image_override:
        local = Path(image_override)
        if not local.exists():
            print(f"Rasm fayli topilmadi: {image_override}")
            return None
        image_path = media.prepare_jpeg(local)
    elif not video_override:
        image_path = media.build_image(
            cfg,
            image_prompt=draft.image_prompt,
            source_image=draft.source_image if cfg.use_source_image else "",
            title=draft.title or (draft.caption.splitlines() or [""])[0],
            subtitle=cfg.topic,
        )
    if image_path is None and not direct_url and not video_override:
        logger.error("Rasm tayyorlab bo'lmadi — post joylanmadi.")
        notify.notify_error(cfg, "post", "Rasm tayyorlab bo'lmadi.")
        return None

    # 2b) Video (Reels) — berilgan bo'lsa
    video_url = ""
    if video_override:
        try:
            video_url = _to_public_url(cfg, video_override)
        except Exception as exc:
            logger.error("Videoni tayyorlab bo'lmadi: %s", exc)
            return None

    post_id = store.create_post(
        caption=full_caption,
        image_path=str(image_path) if image_path else "",
        image_url=direct_url,
        alt_text=draft.alt_text,
        source_url=draft.source_url,
        source_title=draft.source_title,
        media_type="REELS" if video_url else "IMAGE",
        video_url=video_url,
    )

    if dry_run:
        print("─" * 60)
        print(f"QORALAMA #{post_id}")
        print("─" * 60)
        print(full_caption)
        print("─" * 60)
        print(f"Rasm: {image_path or direct_url}")
        if video_override:
            print(f"Video (Reels): {video_override}")
        if draft.source_url:
            print(f"Manba: {draft.source_url}")
        print("(dry-run — Instagram'ga joylanmadi)")
        return None

    # 3) Rasmni ochiq manzilga chiqarish (Reels uchun bu muqova bo'ladi)
    if not direct_url and image_path is not None:
        try:
            direct_url = media.public_url(cfg, image_path)
        except Exception as exc:
            store.mark_failed(post_id, str(exc))
            logger.error("%s", exc)
            notify.notify_error(cfg, "post", str(exc))
            return None
    store.update_post(post_id, image_url=direct_url)

    # 4) Tasdiq kerakmi?
    post = store.get_post(post_id)
    if cfg.require_approval and not skip_approval:
        store.update_post(post_id, status=store.STATUS_PENDING)
        sent = notify.ask_approval(cfg, post_id, post["approve_token"],
                                   direct_url or video_url, full_caption,
                                   is_video=bool(video_url) and not direct_url)
        if sent:
            logger.info("Post #%s tasdiq kutmoqda (Telegram'ga yuborildi).", post_id)
        else:
            logger.warning(
                "Post #%s tasdiq kutmoqda, lekin Telegram sozlanmagan. "
                "Joylash uchun: python -m instagram.agent approve %s",
                post_id, post_id,
            )
        return None

    return publish_stored_post(cfg, api, post)


# --------------------------------------------------------------------- #
# HTTP server: rasmlarni ulashish + tasdiqlash havolalari + health-check
# --------------------------------------------------------------------- #
def _make_handler(cfg: Config, api: InstagramClient):
    approve_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "ig-agent"

        def log_message(self, fmt, *args):        # noqa: A003
            logger.debug("HTTP %s", fmt % args)

        def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8",
                  head_only: bool = False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _html(self, code: int, text: str):
            body = (f"<!doctype html><meta charset='utf-8'>"
                    f"<div style='font:18px/1.5 system-ui;padding:40px;text-align:center'>"
                    f"{text}</div>").encode()
            self._send(code, body, "text/html; charset=utf-8")

        def _handle(self, head_only: bool = False):
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            if path in ("/", "/healthz"):
                self._send(200, b"ok", head_only=head_only)
                return

            if path.startswith("/media/"):
                file_path = media.safe_media_path(path[len("/media/"):])
                if file_path is None:
                    self._send(404, b"not found", head_only=head_only)
                    return
                ctype = CONTENT_TYPES.get(file_path.suffix.lower(),
                                          "application/octet-stream")
                self._send(200, file_path.read_bytes(), ctype, head_only=head_only)
                return

            if path in ("/ig/approve", "/ig/reject"):
                token = (query.get("token") or [""])[0]
                with approve_lock:
                    post = store.get_post_by_token(token)
                    if not post or not token:
                        self._html(404, "❌ Bunday so'rov topilmadi (havola eskirgan).")
                        return
                    if post["status"] == store.STATUS_PUBLISHED:
                        link = post["permalink"]
                        self._html(200, f"ℹ️ Bu post allaqachon joylangan.<br>"
                                        f"<a href='{link}'>{link}</a>")
                        return
                    if post["status"] != store.STATUS_PENDING:
                        self._html(200, f"ℹ️ Bu postning holati: <b>{post['status']}</b>")
                        return
                    if path == "/ig/reject":
                        store.update_post(post["id"], status=store.STATUS_REJECTED)
                        self._html(200, "🗑 Post bekor qilindi.")
                        return
                    # "publishing" deb belgilab qo'yamiz — tugma ikki marta
                    # bosilsa ham post bir marta joylanadi
                    store.update_post(post["id"], status=store.STATUS_PUBLISHING)

                result = publish_stored_post(cfg, api, post)
                if result:
                    link = result.get("permalink", "")
                    self._html(200, f"✅ Post joylandi.<br><a href='{link}'>{link}</a>")
                else:
                    fresh = store.get_post(post["id"]) or {}
                    self._html(500, f"⚠️ Joylab bo'lmadi: {fresh.get('error', '')}")
                return

            self._send(404, b"not found", head_only=head_only)

        def do_GET(self):                          # noqa: N802
            try:
                self._handle()
            except Exception:
                logger.exception("HTTP so'rovda xato")
                try:
                    self._send(500, b"error")
                except Exception:
                    pass

        def do_HEAD(self):                         # noqa: N802
            try:
                self._handle(head_only=True)
            except Exception:
                logger.exception("HTTP HEAD so'rovda xato")

    return Handler


def start_server(cfg: Config, api: InstagramClient) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", cfg.port), _make_handler(cfg, api))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("HTTP server ishga tushdi (port=%s).", cfg.port)
    return server


# --------------------------------------------------------------------- #
# 24/7 rejim
# --------------------------------------------------------------------- #
def _published_today(cfg: Config) -> int:
    now = dt.datetime.now(cfg.tz)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = midnight.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    return store.count_published_since(since)


def run_forever(cfg: Config, api: InstagramClient) -> None:
    ensure_dirs()
    store.init_db()
    start_server(cfg, api)

    logger.info(
        "Agent ishga tushdi. Post vaqtlari: %s (%s). Kommentlar har %s daqiqada.",
        ", ".join(t.strftime("%H:%M") for t in cfg.post_times) or "—",
        cfg.timezone, cfg.comment_poll_minutes,
    )

    last_comment_check = 0.0
    last_cleanup_day = ""

    while True:
        try:
            now = dt.datetime.now(cfg.tz)

            # 1) Jadval bo'yicha post
            for slot in cfg.post_times:
                key = f"posted:{now.date().isoformat()}:{slot.strftime('%H%M')}"
                if store.get_state(key):
                    continue
                scheduled = now.replace(hour=slot.hour, minute=slot.minute,
                                        second=0, microsecond=0)
                # vaqti kelgan bo'lsa (lekin 2 soatdan ko'p kechikmagan bo'lsa)
                if not (scheduled <= now <= scheduled + dt.timedelta(hours=2)):
                    continue
                if _published_today(cfg) >= cfg.max_posts_per_day:
                    logger.info("Bugungi post limiti (%s) to'ldi.", cfg.max_posts_per_day)
                    store.set_state(key, "limit")
                    continue
                logger.info("Jadval bo'yicha post tayyorlanmoqda (%s)…",
                            slot.strftime("%H:%M"))
                store.set_state(key, now.isoformat(timespec="seconds"))
                try:
                    create_post(cfg, api)
                except Exception as exc:
                    logger.exception("Post yaratishda kutilmagan xato")
                    notify.notify_error(cfg, "post", str(exc))

            # 2) Kommentlar
            if cfg.auto_reply and (
                time.monotonic() - last_comment_check >= cfg.comment_poll_minutes * 60
            ):
                last_comment_check = time.monotonic()
                try:
                    stats = comments_mod.run_once(cfg, api)
                    if stats["new"]:
                        logger.info("Kommentlar: %s", stats)
                except Exception as exc:
                    logger.exception("Kommentlarni tekshirishda xato")
                    notify.notify_error(cfg, "komment", str(exc))

            # 3) Kuniga bir marta eski rasmlarni tozalash
            today = now.date().isoformat()
            if today != last_cleanup_day:
                last_cleanup_day = today
                removed = media.clean_old_media()
                if removed:
                    logger.info("%s ta eski rasm o'chirildi.", removed)

        except KeyboardInterrupt:
            logger.info("To'xtatildi.")
            return
        except Exception:
            logger.exception("Asosiy siklda kutilmagan xato")

        time.sleep(30)


# --------------------------------------------------------------------- #
# Buyruqlar
# --------------------------------------------------------------------- #
def cmd_check(cfg: Config, api: InstagramClient) -> int:
    print("=== Sozlamalar ===")
    print(f"  Login turi     : {cfg.login_type}  ({cfg.api_base}/{cfg.graph_version})")
    print(f"  Token          : {'bor (' + str(len(cfg.access_token)) + ' belgi)' if cfg.access_token else 'YO‘Q'}")
    print(f"  Gemini         : {'bor' if cfg.has_gemini else 'YO‘Q'}")
    print(f"  Rasm hosting   : {cfg.public_base_url or ('imgbb' if cfg.imgbb_key else 'YO‘Q')}")
    print(f"  Mavzu          : {cfg.topic or '—'}")
    print(f"  Post vaqtlari  : {', '.join(t.strftime('%H:%M') for t in cfg.post_times) or '—'} ({cfg.timezone})")
    print(f"  Kommentlar     : {'avtomatik javob yoqilgan' if cfg.auto_reply else 'o‘chirilgan'}")
    print(f"  Tasdiqlash     : {'kerak (Telegram)' if cfg.require_approval else 'kerak emas'}")
    print(f"  RSS lentalar   : {len(cfg.rss_feeds)} ta")

    problems = cfg.problems()
    if problems:
        print("\n=== Diqqat ===")
        for item in problems:
            print(f"  • {item}")

    if not cfg.access_token:
        print("\nToken yo'q — Instagram'ga ulanib bo'lmadi.")
        return 1

    print("\n=== Instagram ===")
    try:
        account = api.account()
        print(f"  Akkaunt        : @{account.get('username')} (id={account.get('id')})")
        if account.get("account_type"):
            print(f"  Turi           : {account['account_type']}")
        print(f"  Obunachilar    : {account.get('followers_count', '—')}")
        print(f"  Postlar        : {account.get('media_count', '—')}")
    except InstagramError as exc:
        print(f"  ❌ Ulanmadi: {exc}")
        if exc.hint():
            print(f"     → {exc.hint()}")
        return 1

    try:
        limit = api.publishing_limit()
        quota = limit.get("config", {}).get("quota_total", 25)
        used = limit.get("quota_usage", 0)
        print(f"  24 soatlik limit: {used}/{quota}")
    except InstagramError as exc:
        print(f"  (limitni o'qib bo'lmadi: {exc})")

    print("\n✅ Hammasi tayyor.")
    return 0


def cmd_drafts(cfg: Config) -> int:
    rows = store.list_posts(limit=15)
    if not rows:
        print("Hali post yo'q.")
        return 0
    for row in rows:
        first = (row["caption"].splitlines() or [""])[0][:60]
        print(f"#{row['id']:<4} {row['status']:<10} {row['created_at'][:16]}  {first}")
        if row["status"] == store.STATUS_PENDING:
            print(f"      joylash: python -m instagram.agent approve {row['id']}")
        if row["error"]:
            print(f"      xato: {row['error']}")
    return 0


def cmd_approve(cfg: Config, api: InstagramClient, post_id: int) -> int:
    post = store.get_post(post_id)
    if not post:
        print(f"#{post_id} topilmadi.")
        return 1
    if post["status"] == store.STATUS_PUBLISHED:
        print(f"#{post_id} allaqachon joylangan: {post['permalink']}")
        return 0
    result = publish_stored_post(cfg, api, post)
    if result:
        print(f"✅ Joylandi: {result.get('permalink', '')}")
        return 0
    print("❌ Joylab bo'lmadi (yuqoridagi xatoga qarang).")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m instagram.agent",
        description="Instagram uchun avtomatik post va komment agenti.",
    )
    parser.add_argument("--verbose", action="store_true", help="batafsil loglar")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="sozlamalar va ulanishni tekshirish")

    post_cmd = sub.add_parser("post", help="hozir bitta post joylash")
    post_cmd.add_argument("--topic", default="", help="shu safargi mavzu")
    post_cmd.add_argument("--caption", default="", help="tayyor matn (generatsiya qilinmaydi)")
    post_cmd.add_argument("--image", default="", help="rasm URL yoki fayl yo'li")
    post_cmd.add_argument("--video", default="",
                          help="Reels uchun video URL yoki fayl yo'li (.mp4)")
    post_cmd.add_argument("--no-internet", action="store_true",
                          help="RSS lentalardan mavzu olinmasin")
    post_cmd.add_argument("--dry-run", action="store_true",
                          help="joylamasdan, faqat ko'rsatish")
    post_cmd.add_argument("--now", action="store_true",
                          help="tasdiq so'ramasdan darrov joylash")

    comments_cmd = sub.add_parser("comments", help="kommentlarga javob berish")
    comments_cmd.add_argument("--seed", action="store_true",
                              help="javob yozmasdan, mavjud kommentlarni ko'rilgan qilish")
    comments_cmd.add_argument("--dry-run", action="store_true",
                              help="javoblarni faqat ekranga chiqarish")

    sub.add_parser("drafts", help="oxirgi postlar va kutilayotgan qoralamalar")

    approve_cmd = sub.add_parser("approve", help="kutilayotgan postni joylash")
    approve_cmd.add_argument("post_id", type=int)

    sub.add_parser("refresh-token", help="tokenni yana 60 kunga uzaytirish")
    sub.add_parser("run", help="24/7 rejim: jadval bo'yicha post + kommentlar")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    cfg = Config.from_env()
    ensure_dirs()
    store.init_db()
    api = InstagramClient(cfg)

    if args.command == "check":
        return cmd_check(cfg, api)

    if args.command == "post":
        create_post(
            cfg, api,
            topic=args.topic,
            caption_override=args.caption,
            image_override=args.image,
            video_override=args.video,
            use_internet=not args.no_internet,
            dry_run=args.dry_run,
            skip_approval=args.now,
        )
        return 0

    if args.command == "comments":
        stats = comments_mod.run_once(cfg, api, seed=args.seed, dry_run=args.dry_run)
        print(f"Kommentlar: {stats}")
        return 0

    if args.command == "drafts":
        return cmd_drafts(cfg)

    if args.command == "approve":
        return cmd_approve(cfg, api, args.post_id)

    if args.command == "refresh-token":
        try:
            data = api.refresh_token()
        except InstagramError as exc:
            print(f"❌ {exc}")
            return 1
        print("✅ Yangi token (uni .env / Render sozlamalariga qo'ying):")
        print(data.get("access_token", ""))
        print(f"Amal qilish muddati: ~{int(data.get('expires_in', 0)) // 86400} kun")
        return 0

    if args.command == "run":
        run_forever(cfg, api)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
