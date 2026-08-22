"""Ovqat "+" hisoblovchi Telegram bot (to'liq avtomatik).

Jarayon (guruhdagi haqiqiy format bo'yicha):
  1. Kimdir menyuni chiqaradi — odatda Malika'dan FORWARD qilingan, har qatorda
     bitta ovqat (masalan: Мохора / Ош / Голупси). Bot buni AVTOMATIK aniqlaydi.
  2. Bollar ovqatga "+" yozadi — menyuga reply qilib (ovqatni belgilab/quote),
     yoki oddiy "Osh +", "Ош+" deб. Bot ovqatni topib sanaydi. "-" = kerak emas.
  3. Bot jonli hisob xabarini yangilab turadi; /hisob ham ishlaydi.

DIQQAT: to'liq avtomatik ishlashi uchun BotFather'da Group Privacy O'CHIRILGAN
bo'lishi shart (aks holda bot boshqa odamlarning xabarlarini ko'rmaydi).
"""

import datetime as dt
import html
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db
import gemini_intent
import match

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Menyu deb qabul qilinadigan matnda qatorlar soni chegaralari
MIN_MENU_LINES = 2
MAX_MENU_LINES = 8
MAX_LINE_LEN = 40


def _display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name


def _menu_lines(text: str, min_lines: int = MIN_MENU_LINES) -> list[str] | None:
    """Matn menyuga o'xshasa, ovqat nomlari ro'yxatini qaytaradi, aks holda None.

    min_lines — kamida nechta taom qatori bo'lishi kerak (forward qilingan
    xabarlar uchun 1 taom ham yetarli, chunki ba'zi kunlar bitta ovqat bo'ladi).
    """
    raw = [ln.strip(" \t.-•*") for ln in text.splitlines()]
    lines = [ln for ln in raw if ln]
    if not (1 <= len(lines) <= MAX_MENU_LINES):
        return None
    seen: set[str] = set()
    items: list[str] = []
    for ln in lines:
        # "+"/"-" bo'lgan qatorlar ovqat nomi emas
        if "+" in ln or ln in {"-", "—"}:
            return None
        if len(ln) > MAX_LINE_LEN:
            return None
        key = match.normalize(ln)
        if key and key not in seen:
            seen.add(key)
            items.append(ln)
    return items if len(items) >= min_lines else None


def _mention_html(member: dict) -> str:
    """Bitta a'zo uchun HTML mention (bosilsa profiliga olib boradi)."""
    if member["username"]:
        return f"@{member['username']}"
    name = html.escape(member["name"] or "a'zo")
    return f'<a href="tg://user?id={member["user_id"]}">{name}</a>'


def _build_mention_chunks(members: list[dict], max_len: int = 3500) -> list[str]:
    """A'zolar teglarini Telegram xabar uzunligiga sig'adigan bo'laklarga bo'ladi."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for m in members:
        tag = _mention_html(m)
        add_len = len(tag) + 1
        if current and current_len + add_len > max_len:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(tag)
        current_len += add_len
    if current:
        chunks.append(" ".join(current))
    return chunks


def _summary_text(session_id: int) -> str:
    counts = db.get_counts(session_id)
    lines = ["📊 Bugungi hisob:"]
    total = 0
    for c in counts:
        line = f"• {c['name']} — {c['count']} (+)"
        if c["minus_count"]:
            line += f" / {c['minus_count']} (-)"
        voters = ", ".join(c["voters"]) if c["voters"] else "—"
        line += f"\n   ✅ {voters}"
        if c["minus_count"]:
            line += f"\n   ❌ {', '.join(c['minus_voters'])}"
        lines.append(line)
        total += c["count"]
    lines.append(f"\nJami: {total} ta \"+\"")
    return "\n".join(lines)


async def _refresh_summary(context, chat_id: int, session) -> None:
    """Jonli hisob xabarini yangilaydi (bo'lmasa yangi yaratadi)."""
    text = _summary_text(session["id"])
    msg_id = session["summary_message_id"]
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=text
            )
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            # xabar o'chirilgan/tahrirlab bo'lmasa — yangisini yuboramiz
    sent = await context.bot.send_message(chat_id=chat_id, text=text)
    db.set_summary_message(session["id"], sent.message_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Salom! Men ovqat hisoblovchi botman.\n\n"
        "Menyuni odam chiqaradi (har qatorda bitta ovqat). Men avtomatik "
        "aniqlab, bollarning \"+\" larini sanayman.\n\n"
        "• \"+\" — ovqatni olaman (ovqat nomini yozib yoki menyuga reply qilib)\n"
        "• \"-\" — menga kerak emas\n"
        "• /hisob — joriy hisobni ko'rsataman\n"
        "• /royxat — menyu chiqqanda meni ham teglashini xohlasangiz, "
        "bir marta shuni yozing\n"
        "• /bekor — men xato ravishda yangi menyu deb tanib, hisobni "
        "yopib qo'ysam, shu bilan avvalgisini tiklayman\n\n"
        "⚠️ Guruhda hammani ko'rishim uchun BotFather'da Group Privacy "
        "O'CHIRILGAN bo'lishi kerak."
    )


async def royxat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Bu buyruq faqat guruhda ishlaydi.")
        return
    user = message.from_user
    db.upsert_member(message.chat_id, user.id, user.full_name, user.username)
    await message.reply_text(
        f"✅ {_display_name(user)}, endi menyu chiqqanda sizni ham teglayman."
    )


async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    for user in message.new_chat_members or []:
        if not user.is_bot:
            db.upsert_member(message.chat_id, user.id, user.full_name, user.username)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or message.chat.type not in (
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    ):
        return

    user = message.from_user
    if user is None or user.is_bot:
        return  # botlarning (jumladan o'zimizning) xabarlarini e'tiborsiz qoldiramiz

    chat_id = message.chat_id
    db.upsert_member(chat_id, user.id, user.full_name, user.username)

    text = message.text or message.caption or ""
    if not text.strip():
        return

    # --- 1) Menyu aniqlash: FORWARD qilingan yoki ko'p qatorli, reply emas ---
    # Forward qilingan xabar uchun 1 taom ham yetarli (ba'zi kunlar bitta ovqat
    # bo'ladi); forward qilinmagan matn uchun kamida MIN_MENU_LINES qator kerak.
    # Forward qilinmagan holatda esa, tasodifiy oddiy xabar/eslatma ("Obed",
    # miqdor haqida eslatma va h.k.) noto'g'ri "yangi menyu" deb qabul qilinib,
    # joriy sessiyani yopib qo'ymasligi uchun Gemini orqali tasdiqlanadi —
    # Gemini "ha, bu menyu" demaguncha yangi sessiya OCHILMAYDI.
    is_forwarded = getattr(message, "forward_origin", None) is not None
    if not message.reply_to_message:
        menu = _menu_lines(text, min_lines=1 if is_forwarded else MIN_MENU_LINES)
        if menu is not None and not is_forwarded:
            if gemini_intent.is_menu(menu) is not True:
                menu = None
        if menu is not None:
            session_id = db.create_menu(chat_id, message.message_id, menu)
            session = db.get_open_session(chat_id)

            menu_text = (
                "🍽 Bugungi menyu:\n"
                + "\n".join(f"{i}. {n}" for i, n in enumerate(menu, 1))
                + "\n\nKerakligiga \"+\" yozing (\"-\" — kerak emas)."
            )
            await context.bot.send_message(chat_id=chat_id, text=menu_text)

            members = [m for m in db.get_members(chat_id) if m["user_id"] != user.id]
            for chunk in _build_mention_chunks(members):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📣 {chunk}",
                    parse_mode=ParseMode.HTML,
                )

            await _refresh_summary(context, chat_id, session)
            logger.info("Menyu: chat=%s session=%s ovqatlar=%s", chat_id, session_id, menu)
            return

    # --- 2) Ovoz sifatida qayta ishlash ---
    session = db.get_open_session(chat_id)
    if session is None:
        return  # hali menyu yo'q

    items = db.get_items(session["id"])
    item_names = [it["name"] for it in items]

    # Reply konteksti: avval Telegram'ning "aniq belgilangan matn" (quote)
    # xususiyatini tekshiramiz; odamlar ko'pincha esa oddiy "Reply" bosib,
    # matn belgilamasdan javob beradi — bunda reply_to_message ning TO'LIQ
    # matnini olamiz, aks holda uning "+"/"-" i qaysi ovqatga tegishli
    # ekanini bilolmay, ovoz yo'qolib qoladi.
    quote = getattr(message, "quote", None)
    quote_text = quote.text if quote is not None and quote.text else None
    if not quote_text and message.reply_to_message is not None:
        quote_text = message.reply_to_message.text or message.reply_to_message.caption

    gemini_result = gemini_intent.interpret(text, item_names, quote_text)

    if gemini_result is not None:
        intent, numbers = gemini_result
        matched = [items[n - 1] for n in numbers if 1 <= n <= len(items)]
        has_plus = intent == "plus"
        has_minus = intent == "minus"
        if intent == "none" and not matched:
            return
    else:
        # Gemini ishlamasa (key yo'q yoki xato) — mahalliy qoidalarga qaytamiz.
        has_plus = "+" in text
        has_minus = "-" in text

        if quote_text:
            matched = match.match_items(quote_text, items)
            if not matched:
                matched = match.match_items(text, items)
        else:
            matched = match.match_items(text, items)

        if not matched:
            matched = match.match_by_number(text, items)

        if not matched and len(items) == 1 and (has_plus or has_minus):
            matched = items

        if not matched and not (has_plus or has_minus):
            return

    changed = False

    if has_minus and not has_plus:
        # "-" = kerak emas. Aniq ovqat bo'lsa o'shani "-" deb belgilaymiz,
        # bo'lmasa foydalanuvchining shu sessiondagi barcha ovozlarini tozalaymiz.
        if matched:
            for it in matched:
                if db.set_vote(it["id"], user.id, _display_name(user), -1):
                    changed = True
        else:
            changed = db.remove_all_votes(session["id"], user.id) > 0
    else:
        # "+" — ovqatga ovoz.
        if matched and (has_plus or len(matched) == 1):
            for it in matched:
                if db.set_vote(it["id"], user.id, _display_name(user), 1):
                    changed = True

    if changed:
        await _refresh_summary(context, chat_id, session)


async def hisob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    session = db.get_open_session(message.chat_id)
    if session is None:
        await message.reply_text("Hozircha menyu aniqlanmadi.")
        return
    await message.reply_text(_summary_text(session["id"]))


async def tugat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if db.close_session(message.chat_id):
        await message.reply_text("Menyu yopildi.")
    else:
        await message.reply_text("Ochiq menyu yo'q edi.")


async def bekor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bot xato ravishda yangi menyu deb tanib, joriy sessiyani noto'g'ri
    yopib qo'yganda — shu buyruq bilan avvalgi sessiya qayta tiklanadi."""
    message = update.message
    chat_id = message.chat_id
    if db.reopen_previous_session(chat_id):
        session = db.get_open_session(chat_id)
        await message.reply_text(
            "↩️ Oxirgi menyu bekor qilindi, avvalgi sessiya (va ovozlar) tiklandi."
        )
        await _refresh_summary(context, chat_id, session)
    else:
        await message.reply_text("Bekor qilinadigan avvalgi sessiya topilmadi.")


async def post_daily_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Har kuni soat 10:45 (Toshkent vaqti) da ochiq sessiyalar bo'yicha
    hisobotni guruhga yuboradi. Sessiya YOPILMAYDI — shundan keyin ham
    "+"/"-" davom etaveradi va jonli hisob yangilanib boradi."""
    for session in db.get_all_open_sessions():
        text = "⏰ Soat 10:45 hisoboti:\n\n" + _summary_text(session["id"])
        await context.bot.send_message(chat_id=session["chat_id"], text=text)


class _HealthHandler(BaseHTTPRequestHandler):
    """Render (yoki boshqa hosting) 'web service' sifatida tan olishi va
    uxlab qolmasligi uchun tashqi ping xizmatlari (masalan UptimeRobot)
    urib turadigan minimal HTTP endpoint."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot ishlab turibdi.")

    def log_message(self, format, *args):
        pass  # HTTP so'rovlarini bot.log ga chiqarmaymiz


def _start_health_server() -> None:
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.serve_forever()


def main() -> None:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN topilmadi. .env fayliga tokenni qo'ying.")

    if os.getenv("PORT"):
        # Render kabi hostinglar $PORT beradi va shu portni tinglashni talab
        # qiladi (aks holda "web service" sifatida ishga tushmaydi).
        threading.Thread(target=_start_health_server, daemon=True).start()
        logger.info("Health-check server ishga tushdi (port=%s).", os.getenv("PORT"))

    db.init_db()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("hisob", hisob))
    app.add_handler(CommandHandler("tugat", tugat))
    app.add_handler(CommandHandler("royxat", royxat))
    app.add_handler(CommandHandler("bekor", bekor))
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members)
    )
    # Buyruq bo'lmagan barcha matnli xabarlar (menyu yoki ovoz)
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            on_message,
        )
    )

    if app.job_queue is None:
        logger.warning(
            "job_queue mavjud emas — kunlik 10:45 hisoboti ishlamaydi "
            "('python-telegram-bot[job-queue]' o'rnatilganini tekshiring)."
        )
    else:
        app.job_queue.run_daily(
            post_daily_report,
            time=dt.time(hour=10, minute=45, tzinfo=ZoneInfo("Asia/Tashkent")),
        )

    logger.info("Bot ishga tushdi. To'xtatish uchun Ctrl+C.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
