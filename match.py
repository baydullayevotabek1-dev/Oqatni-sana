"""Ovqat nomlarini moslashtirish: kirill/lotin transliteratsiya va matndan topish.

Odamlar ovqatni turlicha yozadi: "Ош", "Osh", "osh +", "Мохора", "Moxora".
Bu modul nomlarni bir ko'rinishga keltirib (normalize) solishtiradi.
"""

import re

# Kirill -> lotin (rus + o'zbek harflari)
_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ў": "o", "ғ": "g", "қ": "q", "ҳ": "h", "ё": "yo", "ї": "i", "є": "e",
}


def _translit(text: str) -> str:
    out = []
    for ch in text:
        low = ch.lower()
        out.append(_CYR.get(low, low))
    return "".join(out)


def normalize(text: str) -> str:
    """Matnni solishtirish uchun bir ko'rinishga keltiradi.

    - kirillni lotinga o'giradi
    - faqat a-z, 0-9 qoldiradi (bo'shliq, tinish belgilarini tashlaydi)
    - x -> h (Moxora/Mohora bir xil bo'lsin), sh/ch saqlanadi
    """
    t = _translit(text.lower())
    t = t.replace("x", "h")
    t = re.sub(r"[^a-z0-9]+", "", t)
    return t


def _tokens(text: str) -> list[str]:
    """Matnni normallashtirilgan so'zlarga ajratadi (+ va - ni olib tashlab)."""
    # translit qilamiz, so'ng harf-raqam bo'laklariga bo'lamiz
    t = _translit(text.lower()).replace("x", "h")
    return [tok for tok in re.split(r"[^a-z0-9]+", t) if tok]


def item_keys(name: str) -> tuple[str, str]:
    """Ovqat nomidan (to'liq_norm, birinchi_soz_norm) qaytaradi.

    Masalan "Голупси (ток перец)" -> ("golupsitokperets", "golupsi").
    """
    full = normalize(name)
    toks = _tokens(name)
    first = toks[0] if toks else full
    return full, first


def match_items(text: str, items: list[dict]) -> list[dict]:
    """Matn ichida qaysi ovqat(lar) tilga olinganini topadi.

    items — har biri {"id", "name", "full", "first"} ko'rinishidagi lug'atlar.
    Mos kelgan ovqatlar ro'yxatini qaytaradi (mos yo'q bo'lsa — bo'sh).
    """
    msg_tokens = set(_tokens(text))
    msg_norm = normalize(text)
    if not msg_norm:
        return []

    matched = []
    for it in items:
        first = it["first"]
        full = it["full"]
        hit = False
        # 1) birinchi so'z matn so'zlari ichida bormi (eng ishonchli)
        if first and first in msg_tokens:
            hit = True
        # 2) to'liq nom matn ichida (bo'shliqsiz) bormi
        elif full and len(full) >= 3 and full in msg_norm:
            hit = True
        # 3) birinchi so'z uzun bo'lsa va matn ichida uchrasa
        elif first and len(first) >= 4 and first in msg_norm:
            hit = True
        if hit:
            matched.append(it)
    return matched


def match_by_number(text: str, items: list[dict]) -> list[dict]:
    """Matnda "1", "2" kabi ovqat tartib raqami yozilgan bo'lsa, o'sha
    o'rindagi ovqat(lar)ni qaytaradi (1-indeksli, menyu tartibi bo'yicha).

    Masalan 3 ta ovqatli menyuda "2" yoki "2+" -> ikkinchi ovqat.
    """
    matched: list[dict] = []
    seen_ids: set = set()
    for tok in re.findall(r"\d+", text):
        idx = int(tok)
        if 1 <= idx <= len(items):
            it = items[idx - 1]
            if it["id"] not in seen_ids:
                matched.append(it)
                seen_ids.add(it["id"])
    return matched
