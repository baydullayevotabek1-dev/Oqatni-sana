# Oqat Sana Bot — ovqat "+" hisoblovchi Telegram bot

Ish guruhida har kuni ovqat menyusi chiqqanda, kim qaysi ovqatni tanlaganini
("+" qo'yganini) avtomatik hisoblaydigan bot.

## Qanday ishlaydi

1. Guruhda kimdir menyuni yuboradi (forward yoki ko'p qatorli yozuv, har
   qatorda bitta ovqat; bitta ovqatli kunlarda forward qilingan bitta qator
   ham yetarli). Bot buni **avtomatik** aniqlaydi — buyruq shart emas.
2. Bot menyuni raqamlab qayta chiqaradi va bilingan barcha a'zolarni teglaydi.
3. Har kim kerakli ovqatni erkin uslubda yozadi — **"+"**, ovqat nomi, tartib
   raqami ("2"), yoki oddiy gap bilan ("somsa ham qoshib qoy"). Kerak bo'lmasa
   shunga mos "-" yoki "kerak emas" deb yozadi. Xabarlar **Gemini AI** orqali
   tushuniladi (GEMINI_API_KEY sozlangan bo'lsa) — kalit bo'lmasa yoki xato
   bersa, bot mahalliy oddiy qoidalarga (nom/raqam moslash) qaytadi.
4. Bot jonli hisob xabarini yangilab boradi — har ovqat uchun "+" va "-"
   sonlari alohida ko'rsatiladi. `/hisob` bilan ham ko'rish mumkin.
5. Har kuni soat **10:45** (Toshkent vaqti) da bot avtomatik hisobotni
   guruhga va Oshpaz guruhiga yuboradi — sessiya yopilmaydi, undan keyin ham ovoz berish
   davom etadi.
6. **Oshpaz guruhiga anonim yuborish:** Oshpaz guruhida `/set_chef @shef_povor` deb
   yozilsa, bot o'sha guruhga faqat anonim umumiy ovqatlar sonini jonli yuborib turadi.

Buyruqlar: `/start` (yordam), `/hisob`, `/tugat` (menyuni yopish), `/royxat`
(hali gapirmagan a'zo o'zini teglash ro'yxatiga qo'shadi), `/set_chef` (oshpaz guruhini sozlash), `/povor` (oshpaz guruhiga anonim hisobotni qo'lda yangilash).

## Fayllar

| Fayl | Vazifasi |
|------|----------|
| `bot.py` | Asosiy bot: handlerlar, menyu aniqlash, health-server, kunlik hisobot |
| `db.py` | SQLite ma'lumotlar bazasi funksiyalari |
| `match.py` | Kirill/lotin transliteratsiya, nom/raqam bo'yicha ovqat topish (Gemini ishlamasa fallback) |
| `gemini_intent.py` | Gemini AI orqali xabar ma'nosini (ovqat + intent) aniqlash |
| `requirements.txt` | Kutubxonalar |
| `render.yaml` | Render.com uchun tayyor konfiguratsiya |
| `.env` | Bot tokeni va Gemini key (o'zingiz yaratasiz, git'ga tushmaydi) |
| `data.db` | Hisob ma'lumotlari (avtomatik yaratiladi) |

## Mahalliy (lokal) ishga tushirish

### 1. Botni yaratish (BotFather)

1. Telegramda [@BotFather](https://t.me/BotFather) ni oching → `/newbot` → nom va username bering.
2. Berilgan **tokenni** nusxalang.
3. **Muhim:** bot guruhdagi hamma xabarlarni ko'rishi uchun:
   BotFather → `/mybots` → botni tanlang → **Bot Settings → Group Privacy → Turn off**.

### 2. Sozlash va ishga tushirish

```bash
pip install -r requirements.txt
```

`.env.example` faylini `.env` deb nusxalab, tokenni qo'ying:

```
BOT_TOKEN=123456789:ABC...sizning_tokeningiz
GEMINI_API_KEY=AIzaSy...sizning_keyingiz
```

`GEMINI_API_KEY` — [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
dan bepul olinadi (kredit karta shart emas). Bo'lmasa ham bot ishlayveradi,
faqat erkin yozilgan xabarlarni yomonroq tushunadi.

```bash
python bot.py
```

Botni guruhingizga a'zo qilib qo'shing va sinab ko'ring. To'xtatish: `Ctrl+C`.

## 24/7 hosting — Render.com (bepul tarif)

Kompyuteringiz doim yonib turishi shart bo'lmasligi uchun botni Render'ga
joylash mumkin. Render'ning bepul tarifi faqat "Web Service" turini qo'llab-
quvvatlaydi (background worker emas), shuning uchun bot ichida kichik
HTTP-server ham ishga tushadi (`bot.py`dagi `PORT` o'zgaruvchisi orqali) —
bu Render'ga botni "web service" sifatida tanishtiradi.

**Muhim cheklov:** bepul tarifda xizmat 15 daqiqa harakatsizlikdan keyin
"uxlab qoladi" va uyg'onishi ~30-60 soniya vaqt oladi — shu payt bot
xabarlarga javob bermaydi. Buni oldini olish uchun 4-qadamda tashqi "ping"
xizmati sozlanadi.

### 1. GitHub'ga joylash

```bash
git init
git add .
git commit -m "Oqat sana bot"
git branch -M main
git remote add origin <GITHUB_REPO_URL>
git push -u origin main
```

(`.env` va `data.db` `.gitignore` orqali repo'ga tushmaydi — token xavfsiz qoladi.)

### 2. Render'da servis yaratish

1. [render.com](https://render.com) → **New +** → **Web Service**.
2. GitHub repoingizni tanlang.
3. Sozlamalar:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
   - **Plan:** Free
4. **Environment** bo'limida qo'shing: `BOT_TOKEN` va `GEMINI_API_KEY`.
5. **Create Web Service** — Render avtomatik `PORT` o'zgaruvchisini beradi,
   bot buni o'zi o'qib, health-server'ni ishga tushiradi.

(`render.yaml` fayli allaqachon tayyor — "New + → Blueprint" orqali ham
avtomatik sozlash mumkin.)

### 3. Servisni "uxlab qolishdan" saqlash

Bepul [UptimeRobot](https://uptimerobot.com) (yoki cron-job.org) akkount
oching va yangi **HTTP(s) monitor** yarating:
- **URL:** Render bergan manzil (masalan `https://oqat-sana-bot.onrender.com`)
- **Interval:** 5 daqiqa

Shu monitor har 5 daqiqada so'rov yuborib, xizmatni doim uyg'oq tutadi.

### 4. Mahalliy botni to'xtatish

Render'da ishga tushgach, **kompyuteringizdagi botni to'xtating** (Ctrl+C) —
ikkita nusxa bir vaqtda ishlasa, Telegram xato beradi (bitta token — bitta
faol ulanish).

**Eslatma:** bepul tarifda ma'lumotlar bazasi doim doimiy saqlanishi
kafolatlanmaydi (Render qayta deploy qilganda diск tozalanadi). Agar kunlik
hisob tarixi muhim bo'lsa, keyinchalik to'lovli "Persistent Disk" qo'shish
mumkin.
