"""Gemini AI orqali foydalanuvchi xabarining ma'nosini aniqlash.

Qo'lda yozilgan qoidalar (regex, kalit so'z) o'rniga, har bir xabar Gemini'ga
yuborilib, u xabar qaysi ovqat(lar)ga tegishli va foydalanuvchi uni
OLMOQCHImi ("+") yoki KERAK EMASmi ("-") ekanini aniqlaydi. Bu rasmga
reply, xato yozuv, raqam, erkin gap kabi barcha holatlarni bitta joyda
tushunadi — alohida qoida yozish shart bo'lmaydi.

GEMINI_API_KEY .env faylida bo'lmasa yoki so'rov xato bersa, None
qaytariladi — chaqiruvchi (bot.py) mahalliy (match.py) qoidalarga qaytadi.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_MODEL = "gemini-3.6-flash"

_client = None
_client_checked = False


def _get_client():
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        from google import genai

        _client = genai.Client(api_key=api_key)
    except Exception:
        logger.exception("Gemini mijozini ishga tushirib bo'lmadi.")
        _client = None
    return _client


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["plus", "minus", "none"],
        },
        "item_numbers": {
            "type": "array",
            "items": {"type": "integer"},
        },
    },
    "required": ["intent", "item_numbers"],
}


def interpret(
    text: str, item_names: list[str], quote_text: str | None = None
) -> tuple[str, list[int]] | None:
    """Xabarni tahlil qiladi.

    Qaytaradi: (intent, item_numbers) — intent "plus"/"minus"/"none",
    item_numbers — menyudagi 1-indeksli tartib raqamlar ro'yxati.
    Gemini mavjud bo'lmasa yoki xato bersa — None.
    """
    client = _get_client()
    if client is None or not item_names:
        return None

    menu_block = "\n".join(f"{i}. {name}" for i, name in enumerate(item_names, 1))
    quote_block = f'\nFoydalanuvchi reply qilgan xabar: "{quote_text}"' if quote_text else ""

    prompt = (
        "Sen ovqat buyurtma botisan. Ish guruhida bugungi ovqatlar menyusi "
        "chiqadi, odamlar erkin uslubda javob yozadi (o'zbek/rus tillarida, "
        "kirill/lotin aralash, xato yozuv, faqat raqam, \"+\"/\"-\" belgilar "
        "va h.k. bo'lishi mumkin).\n\n"
        f"Bugungi menyu:\n{menu_block}\n"
        f"{quote_block}\n"
        f'Foydalanuvchi xabari: "{text}"\n\n'
        "Aniqla:\n"
        "- intent: agar xabar biror ovqatni OLISHNI (xohlashni) bildirsa "
        "\"plus\"; ovqat KERAK EMASLIGINI bildirsa \"minus\"; ovqatga "
        "umuman aloqasi bo'lmagan xabar (salomlashish, boshqa mavzu) "
        "bo'lsa \"none\".\n"
        "- item_numbers: xabarda nazarda tutilgan ovqat(lar)ning yuqoridagi "
        "menyudagi tartib raqami(lari).\n\n"
        "MUHIM: agar foydalanuvchi xabari faqat \"+\" yoki \"-\" (yoki shunga "
        "o'xshash, aniq ovqat nomi/raqami YO'Q) bo'lsa, lekin u biror xabarga "
        "REPLY qilgan bo'lsa ('Foydalanuvchi reply qilgan xabar' qismiga "
        "qarang) — o'sha reply qilingan xabarda nechta ovqat tilga olingan "
        "bo'lsa, ularning HAMMASINI item_numbers ga qo'shing (chunki "
        "foydalanuvchi aynan o'sha xabardagi narsa(lar)ga javob bermoqda). "
        "Faqat reply qilingan xabarda umuman ovqat nomi bo'lmasa (masalan "
        "odamlarni belgilash/tag xabari bo'lsa), item_numbers bo'sh qoladi."
    )

    try:
        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": _RESPONSE_SCHEMA,
            },
        )
        data = json.loads(response.text)
        intent = data.get("intent")
        if intent not in ("plus", "minus", "none"):
            intent = "none"
        numbers = [n for n in data.get("item_numbers", []) if isinstance(n, int)]
        return intent, numbers
    except Exception as e:
        logger.warning("Gemini so'rovi muvaffaqiyatsiz (%s), mahalliy qoidaga qaytamiz.", e)
        return None


_MENU_SCHEMA = {
    "type": "object",
    "properties": {"is_menu": {"type": "boolean"}},
    "required": ["is_menu"],
}


def is_menu(lines: list[str]) -> bool | None:
    """Berilgan qatorlar haqiqatan ham ovqat/taom nomlari ro'yxatimi, yoki
    shunchaki oddiy xabar/eslatmami — shuni aniqlaydi.

    Forward qilinmagan (odam to'g'ridan-to'g'ri yozgan) ko'p qatorli
    xabarlarni "yangi menyu" deb noto'g'ri qabul qilib, joriy sessiyani
    yopib qo'ymaslik uchun ishlatiladi. Gemini mavjud bo'lmasa yoki xato
    bersa — None (chaqiruvchi bunda ehtiyotkorlik bilan "menyu emas" deb
    hisoblashi kerak).
    """
    client = _get_client()
    if client is None or not lines:
        return None

    lines_block = "\n".join(f"- {ln}" for ln in lines)
    prompt = (
        "Quyidagi qatorlar Telegram guruhidagi bir xabardan olingan:\n"
        f"{lines_block}\n\n"
        "Bu haqiqatan ham BUGUNGI OVQAT/TAOM MENYUSI (taom nomlari ro'yxati, "
        "masalan \"Osh\", \"Somsa\", \"Kotlet mix\")mi? Yoki bu shunchaki "
        "oddiy xabar, eslatma, sarlavha yoki boshqa mavzu (masalan \"Obed\" "
        "so'zi yolg'iz, miqdor haqida eslatma, salomlashish va h.k.)mi?\n"
        "is_menu: taom nomlari ro'yxati bo'lsa true, aks holda false."
    )

    try:
        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": _MENU_SCHEMA,
            },
        )
        data = json.loads(response.text)
        return bool(data.get("is_menu"))
    except Exception as e:
        logger.warning("Gemini menyu-tasdiqlash so'rovi muvaffaqiyatsiz (%s).", e)
        return None
