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


def _summary_text(session_id: int, chat_id: int) -> str:
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

    # Hali ovoz bermaganlar
    pending = db.get_pending_voters(session_id, chat_id)
    if pending:
        pending_tags = []
        for m in pending:
            if m["username"]:
                pending_tags.append(f"@{m['username']}")
            else:
                pending_tags.append(m["name"])
        lines.append(f"\n⏳ Hali ovoz bermaganlar ({len(pending)}):\n   {', '.join(pending_tags)}")
    else:
        lines.append("\n🎉 Hamma ovoz berdi!")

    return "\n".join(lines)


def _chef_summary_text(session_id: int, chef_tag: str) -> str:
    counts = db.get_counts(session_id)
    lines = [f"👨‍🍳 {chef_tag} Bugungi umumiy ovqat buyurtmalari:\n"]
    total = 0
    for c in counts:
        line = f"• {c['name']} — {c['count']} ta (+)"
        if c["minus_count"]:
            line += f" / {c['minus_count']} ta (-)"
        lines.append(line)
        total += c["count"]
    lines.append(f"\n📦 Jami: {total} ta ovqat")
    lines.append("\n🔒 *Eslatma: Ushbu hisobot to'liq anonim (foydalanuvchilar ismlari kiritilmagan).*")
    return "\n".join(lines)


async def _refresh_chef_summary(context, session) -> tuple[bool, str]:
    """Oshpaz guruhiga anonim jonli hisobni yuboradi yoki yangilaydi."""
    chef_config = db.get_chef_config()
    if chef_config is None:
        return False, "Oshpaz guruhi hali sozlanmagan. Oshpaz guruhida `/set_chef @povor_username` deb yozing."

    chef_chat_id = chef_config["chat_id"]
    chef_tag = chef_config["tag"]
    text = _chef_summary_text(session["id"], chef_tag)

    msg_id = dict(session).get("chef_message_id")
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chef_chat_id, message_id=msg_id, text=text
            )
            return True, "Oshpaz guruhidagi hisobot yangilandi."
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return True, "Hisobot o'zgarmadi."
            # xabar o'chirilgan bo'lsa yangi yuboramiz
        except Exception as e:
            logger.warning("Oshpaz xabarini tahrirlashda xato: %s", e)

    try:
        sent = await context.bot.send_message(chat_id=chef_chat_id, text=text)
        db.set_chef_message(session["id"], sent.message_id)
        return True, "Oshpaz guruhiga hisobot jonli yuborildi."
    except Exception as e:
        logger.error("Oshpaz guruhiga xabar yuborishda xato: %s", e)
        return False, f"Oshpaz guruhiga xabar yuborib bo'lmadi: {e}. Bot Oshpaz guruhiga a'zo qilinganini va yozish huquqi borligini tekshiring."



async def _refresh_summary(context, chat_id: int, session) -> None:
    """Jonli hisob xabarini yangilaydi (bo'lmasa yangi yaratadi)."""
    text = _summary_text(session["id"], chat_id)
    msg_id = session["summary_message_id"]
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id, text=text
            )
        except BadRequest as e:
            if "not modified" in str(e).lower():
                pass
            else:
                sent = await context.bot.send_message(chat_id=chat_id, text=text)
                db.set_summary_message(session["id"], sent.message_id)
    else:
        sent = await context.bot.send_message(chat_id=chat_id, text=text)
        db.set_summary_message(session["id"], sent.message_id)

    await _refresh_chef_summary(context, session)



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Salom! Men ovqat hisoblovchi botman.\n\n"
        "Menyuni odam yuboradi (yoki forward qiladi). Men uni avtomatik "
        "aniqlab, jamoa a'zolarining ovozlarini (+/-) hisoblayman.\n\n"
        "Buyruqlar:\n"
        "• /hisob — joriy hisobotni va hali ovoz bermaganlarni ko'rsatish\n"
        "• /royxat — menyu yuborilganda sizni ham teglashlari uchun ro'yxatga qo'shilish\n"
        "• /chiqish — teglash ro'yxatidan chiqish\n"
        "• /eslat — hali ovoz bermagan a'zolarni teglab eslatish\n"
        "• /menyu — qo'lda menyu yaratish (masalan: `/menyu Osh, Somsa`)\n"
        "• /bekor — xato aniqlangan oxirgi menyuni va uning sessiyasini bekor qilish\n"
        "• /set_chef — oshpaz guruhini sozlash (masalan: `/set_chef @shef_povor`)\n"
        "• /povor — oshpaz guruhiga hisobotni qayta yuborish/yangilash\n"
        "• /tugat — joriy menyuni yopish\n\n"
        "Ovoz berish usullari:\n"
        "• Menyu xabariga reply qilib yoki ovqat nomini yozib \"+\" yoki \"-\" belgilarini yozish (erkin matnlar ham Gemini AI orqali tushuniladi)."
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
        if menu is not None:
            is_menu_val = gemini_intent.is_menu(menu)
            # Agar Gemini ishlagan bo'lsa va False qaytargan bo'lsa, rad etamiz.
            # Agar Gemini bo'lmasa (None) va xabar forward qilinmagan bo'lsa ham rad etamiz (ehtiyotkorlik).
            # Ammo Gemini bo'lmasa (None) va xabar forward qilingan bo'lsa, mahalliy qoidaga ko'ra menyu deb hisoblayveramiz.
            if is_menu_val is False or (is_menu_val is None and not is_forwarded):
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
    await message.reply_text(_summary_text(session["id"], message.chat_id))


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
    cancelled_session = db.reopen_previous_session(chat_id)
    if cancelled_session is not None:
        session = db.get_open_session(chat_id)
        await message.reply_text(
            "↩️ Oxirgi menyu bekor qilindi, avvalgi sessiya (va ovozlar) tiklandi."
        )
        summary_msg_id = cancelled_session["summary_message_id"]
        if summary_msg_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=summary_msg_id)
            except Exception as e:
                logger.warning("Summary xabarni o'chirib bo'lmadi: %s", e)
        await _refresh_summary(context, chat_id, session)
    else:
        await message.reply_text("Bekor qilinadigan avvalgi sessiya topilmadi.")


async def post_daily_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Har kuni soat 10:45 (Toshkent vaqti) da ochiq sessiyalar bo'yicha
    hisobotni guruhga yuboradi. Sessiya YOPILMAYDI — shundan keyin ham
    "+"/"-" davom etaveradi va jonli hisob yangilanib boradi."""
    for session in db.get_all_open_sessions():
        text = "⏰ Soat 10:45 hisoboti:\n\n" + _summary_text(session["id"], session["chat_id"])
        await context.bot.send_message(chat_id=session["chat_id"], text=text)


async def menyu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Qo'lda menyu yaratish buyrug'i."""
    message = update.message
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Bu buyruq faqat guruhda ishlaydi.")
        return

    # /menyu buyrug'idan keyingi matnni olamiz
    args = context.args
    if not args:
        await message.reply_text(
            "Format: `/menyu ovqat1, ovqat2, ...` yoki har bir ovqatni yangi qatordan yozing:\n"
            "`/menyu`\n`Osh`\n`Somsa`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    raw_text = message.text.split(None, 1)[1]
    if "\n" in raw_text:
        raw_items = raw_text.splitlines()
    else:
        raw_items = raw_text.split(",")

    menu = [ln.strip(" \t.-•*") for ln in raw_items if ln.strip()]
    if not menu:
        await message.reply_text("Hech qanday ovqat nomi topilmadi.")
        return

    chat_id = message.chat_id
    session_id = db.create_menu(chat_id, message.message_id, menu)
    session = db.get_open_session(chat_id)

    menu_text = (
        "🍽 Bugungi menyu (qo'lda yaratildi):\n"
        + "\n".join(f"{i}. {n}" for i, n in enumerate(menu, 1))
        + "\n\nKerakligiga \"+\" yozing (\"-\" — kerak emas)."
    )
    await context.bot.send_message(chat_id=chat_id, text=menu_text)

    # A'zolarni teglash
    members = [m for m in db.get_members(chat_id) if m["user_id"] != message.from_user.id]
    for chunk in _build_mention_chunks(members):
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📣 {chunk}",
            parse_mode=ParseMode.HTML,
        )

    await _refresh_summary(context, chat_id, session)
    logger.info("Qo'lda menyu yaratildi: chat=%s session=%s ovqatlar=%s", chat_id, session_id, menu)


async def eslat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ovoz bermagan guruh a'zolariga eslatish buyrug'i."""
    message = update.message
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Bu buyruq faqat guruhda ishlaydi.")
        return

    chat_id = message.chat_id
    session = db.get_open_session(chat_id)
    if session is None:
        await message.reply_text("Hozircha faol menyu yo'q.")
        return

    pending = db.get_pending_voters(session["id"], chat_id)
    if not pending:
        await message.reply_text("Hamma ovoz berib bo'ldi! 🎉")
        return

    await message.reply_text("🔔 Ovoz bermaganlar, iltimos belgilang:")
    chunks = _build_mention_chunks(pending)
    for chunk in chunks:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📣 {chunk}",
            parse_mode=ParseMode.HTML,
        )


async def chiqish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Teglash ro'yxatidan o'chish buyrug'i."""
    message = update.message
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply_text("Bu buyruq faqat guruhda ishlaydi.")
        return

    user = message.from_user
    db.delete_member(message.chat_id, user.id)
    await message.reply_text(
        f"❌ {_display_name(user)}, sizni teglash ro'yxatidan o'chirdim."
    )


async def set_chef(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Oshpaz guruhi va oshpaz tegini sozlash buyrug'i."""
    message = update.message
    args = context.args or []

    target_chat_id = None
    tag = "@shef_povor"

    if args and (args[0].startswith("-") or args[0].isdigit()):
        try:
            target_chat_id = int(args[0])
            if len(args) > 1:
                tag = args[1]
        except ValueError:
            pass

    if target_chat_id is None:
        if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await message.reply_text(
                "Iltimos, bu buyruqni Oshpaz guruhining o'zida yuboring (`/set_chef @username`), "
                "yoki chat ID bilan yozing: `/set_chef -100xxxxxxxxxx @username`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        target_chat_id = message.chat_id
        if args:
            tag = args[0]

    if not tag.startswith("@") and not tag.startswith("http"):
        tag = f"@{tag}"

    db.set_chef_config(target_chat_id, tag)
    chat_title = getattr(message.chat, "title", None) or "Oshpaz guruhi"
    await message.reply_text(
        f"✅ Oshpaz guruhi saqlandi!\n"
        f"📍 Chat: {html.escape(chat_title)} (`{target_chat_id}`)\n"
        f"👨‍🍳 Oshpaz tegi: {tag}\n\n"
        "Endi menyu ochilib ovoz berilganda, anonim hisobot jonli ravishda shu guruhga kelib turadi.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def povor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Oshpaz guruhiga anonim hisobotni qo'lda qayta yuborish/yangilash buyrug'i."""
    message = update.message
    session = db.get_open_session(message.chat_id)
    if session is None:
        await message.reply_text("Hozircha faol menyu yo'q.")
        return

    ok, detail = await _refresh_chef_summary(context, session)
    if ok:
        await message.reply_text(f"👨‍🍳 {detail}")
    else:
        await message.reply_text(f"⚠️ Xatolik: {detail}")


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
    app.add_handler(CommandHandler("menyu", menyu))
    app.add_handler(CommandHandler("eslat", eslat))
    app.add_handler(CommandHandler("chiqish", chiqish))

    for cmd in ("set_chef", "set_povor", "set_povar", "set_shef", "set_chef_group"):
        app.add_handler(CommandHandler(cmd, set_chef))

    for cmd in ("povor", "povar", "oshpaz", "shef", "chef"):
        app.add_handler(CommandHandler(cmd, povor))

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
