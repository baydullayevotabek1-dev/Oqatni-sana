# Instagram agenti — avtomatik post va komment

Instagram akkauntingizga **o'zi post joylaydigan** va **kelgan kommentlarga
o'zi javob yozadigan** agent. Post matni va rasmi Gemini AI orqali
yaratiladi; xohlasangiz mavzuni internetdan (RSS lentalardan) oladi.

Telegram boti bilan bir repoda turadi, lekin **mustaqil ishlaydi** — alohida
ishga tushiriladi, alohida bazasi bor.

---

## ⚠️ Eng muhim shart: akkaunt turi

Instagram rasmiy API orqali post joylashga **faqat Business yoki Creator
(Professional)** akkauntlarga ruxsat beradi. Oddiy shaxsiy akkauntga API
orqali post joylab bo'lmaydi.

Shaxsiy akkauntga "yashirin" yo'l bilan (instagrapi va shunga o'xshash
norasmiy kutubxonalar orqali) post qo'yish texnik jihatdan mumkin, lekin:

* Instagram qoidalarini buzadi;
* akkaunt **bloklanishi** (ban) ehtimoli juda yuqori;
* parolingizni uchinchi tomon kodiga berishni talab qiladi.

Shuning uchun bu agent **faqat rasmiy API** bilan ishlaydi. Akkauntni
Professional qilish bepul va 1 daqiqa vaqt oladi (pastda ko'rsatilgan).

### Agent nima qila oladi va qila olmaydi

| Qila oladi | Qila olmaydi |
|---|---|
| Feed'ga rasm post qilish | Shaxsiy (personal) akkauntga post qilish |
| Reels (video) joylash | Direct (DM) xabarlarga javob berish* |
| Karusel (bir nechta rasm) | Boshqa odamlarning postlariga komment yozish |
| Kommentlarni o'qib, javob yozish | Obunachi yig'ish / like bosish (API bermaydi) |
| Spam kommentni yashirish | Stories'ga interaktiv stikerlar qo'yish |
| Kunlik jadval bo'yicha ishlash | Kuniga 25 tadan ko'p post (API limiti) |

\* DM uchun alohida `instagram_business_manage_messages` ruxsati va Meta
tekshiruvi kerak — bu agentga kiritilmagan.

---

## 1-qadam. Akkauntni Professional qilish

Instagram ilovasida: **Profil → ☰ → Settings and privacy → Account type and
tools → Switch to professional account** → *Business* (yoki *Creator*).

## 2-qadam. Token olish (Meta developer)

1. [developers.facebook.com](https://developers.facebook.com) ga kiring →
   **My Apps → Create app**.
2. Use case sifatida **"Other" → "Business"** ni tanlang (yoki to'g'ridan-
   to'g'ri **Instagram** use case bo'lsa — uni).
3. Ilova ichida **Products → Instagram → API setup with Instagram business
   login** bo'limini oching.
4. **"1. Generate access tokens"** qismida **Add account** tugmasi bilan o'z
   Instagram akkauntingizni ulang, so'ng **Generate token** bosing.
   Chiqqan uzun matn — bu sizning **60 kunlik** tokeningiz.
5. Ruxsatlar (scopes) ro'yxatida quyidagilar belgilangan bo'lsin:
   * `instagram_business_basic`
   * `instagram_business_content_publish`
   * `instagram_business_manage_comments`
6. Xuddi shu sahifada **Instagram account ID** ko'rsatilgan bo'ladi. Uni
   ko'rmasangiz, brauzerda quyidagini oching (tokenni qo'yib):
   `https://graph.instagram.com/v23.0/me?fields=id,username&access_token=TOKEN`

> **Eslatma:** ilova "Development" rejimida bo'lsa ham, **o'z akkauntingizga**
> post joylash ishlaydi. Meta tekshiruvi (App Review) faqat boshqa odamlarning
> akkauntlariga xizmat ko'rsatmoqchi bo'lsangiz kerak bo'ladi.

<details>
<summary>Facebook Page orqali ulanish (eski usul) — kerak bo'lsa</summary>

Agar Instagram akkauntingiz Facebook sahifasiga ulangan bo'lsa va shu yo'ldan
borishni xohlasangiz:

1. Graph API Explorer'da `instagram_basic`, `instagram_content_publish`,
   `instagram_manage_comments`, `pages_show_list`, `pages_read_engagement`
   ruxsatlari bilan token oling va uni uzoq muddatlisiga almashtiring.
2. `GET /me/accounts` → sahifa ID sini oling.
3. `GET /{page-id}?fields=instagram_business_account` → Instagram user ID.
4. `.env` da: `IG_LOGIN=facebook`, `IG_USER_ID=<instagram_business_account id>`.
</details>

## 3-qadam. `.env` ni to'ldirish

Repo ildizidagi `.env` fayliga (yo'q bo'lsa `.env.example` dan nusxa oling):

```bash
IG_ACCESS_TOKEN=IGQVJ...            # 2-qadamdagi token
IG_USER_ID=17841400000000000        # Instagram account ID
GEMINI_API_KEY=AIzaSy...            # aistudio.google.com/apikey (bepul)

PUBLIC_BASE_URL=https://ig-agent.onrender.com   # agentning ochiq manzili

IG_BRAND=HAABR
IG_TOPIC=Videokuzatuv kameralari va xavfsizlik tizimlari
IG_LANGUAGE=uz
IG_TONE=do'stona, ishonchli, professional
IG_CTA=Narx va batafsil ma'lumot uchun Direct'ga yozing.
IG_HASHTAGS=haabr,videokuzatuv,kamera,toshkent
IG_POST_TIMES=10:00,18:00
```

### `PUBLIC_BASE_URL` nega kerak?

Instagram rasmni fayl sifatida qabul qilmaydi — u rasmni **ochiq internet
manzilidan o'zi yuklab oladi**. Agent shu sababli o'zida kichik HTTP-server
ko'taradi va yaratgan rasmini `PUBLIC_BASE_URL/media/<fayl>.jpg` manzilida
beradi. Render'ga joylashtirsangiz, bu manzil sizga bepul beriladi.

Agar server manzili bo'lmasa, muqobil variant — [imgbb.com](https://imgbb.com)
dan bepul API key olib `IMGBB_API_KEY=...` deb yozish. U holda rasm imgbb'ga
yuklanadi.

## 4-qadam. Lokal sinash

```bash
pip install -r instagram/requirements.txt

python -m instagram.agent check                 # ulanish va sozlamalar
python -m instagram.agent post --dry-run        # matn+rasm ko'rish (joylamaydi)
python -m instagram.agent comments --seed       # eski kommentlarni "ko'rilgan" qilish
python -m instagram.agent post --now            # haqiqiy post joylash
```

> **`--seed` ni birinchi ishga tushirishda albatta bajaring** — aks holda agent
> eski postlaringizdagi barcha kommentlarga birdaniga javob yozib yuboradi.

## 5-qadam. 24/7 ishlashi uchun Render.com

`render.yaml` faylida `instagram-agent` servisi allaqachon tayyor:

1. [render.com](https://render.com) → **New + → Blueprint** → repoyingizni
   tanlang (yoki **New + → Web Service** qo'lda: Build `pip install -r
   instagram/requirements.txt`, Start `python -m instagram.agent run`).
2. **Environment** bo'limiga yuqoridagi o'zgaruvchilarni qo'shing.
3. Servis yaratilgach, Render bergan manzilni (`https://…onrender.com`)
   `PUBLIC_BASE_URL` ga yozing va servisni qayta deploy qiling.
4. Bepul tarif 15 daqiqa harakatsizlikdan keyin uxlab qoladi — shuning uchun
   [UptimeRobot](https://uptimerobot.com) da har 5 daqiqada shu manzilga
   so'rov yuboradigan monitor qo'shing. (Uxlab qolgan servisdan Instagram
   rasmni yuklay olmaydi!)

**Diqqat:** bepul tarifda disk doimiy emas — har deploy'da `instagram.db`
tozalanishi mumkin. Bu holda agent qaysi kommentga javob berganini unutadi.
Muhim bo'lsa, pullik "Persistent Disk" qo'shing.

---

## Tasdiqlash rejimi (tavsiya etiladi — birinchi hafta uchun)

Agent postni o'zi joylashidan oldin sizga Telegram'da ko'rsatib, ruxsat
so'rashi mumkin:

```bash
IG_REQUIRE_APPROVAL=true
IG_NOTIFY_CHAT_ID=123456789      # o'z Telegram chat ID'ingiz (@userinfobot)
BOT_TOKEN=...                    # mavjud botingiz tokeni ishlayveradi
```

Telegram'ga rasm + matn va ikkita tugma keladi: **✅ Joylash** / **❌ Bekor**.
Tugma bosilganda agent postni Instagram'ga chiqaradi. (Tugmalar oddiy havola —
shuning uchun `PUBLIC_BASE_URL` bu rejimda majburiy.)

Telegram sozlanmagan bo'lsa, post baza'da kutib turadi:
`python -m instagram.agent drafts` bilan ko'rib, `approve <id>` bilan
joylashingiz mumkin.

---

## Kontent qayerdan olinadi

**Matn.** Gemini `IG_TOPIC`, `IG_TONE`, `IG_LANGUAGE` va oxirgi 6 ta postni
hisobga olib yozadi (takrorlanmaslik uchun). Narx, muddat kabi aniq faktlarni
o'ylab topmaslik qoidasi promptga kiritilgan.

**Mavzu — internetdan.** `IG_RSS_FEEDS` ga vergul bilan RSS manzillar yozsangiz,
agent hali ishlatilmagan yangilikni oladi va uni **o'z so'zlari bilan qayta
yozadi** (ko'chirmaydi), caption oxirida manbani ko'rsatadi:

```bash
IG_RSS_FEEDS=https://kun.uz/uz/news/rss,https://www.gazeta.uz/uz/rss/
```

**Rasm** quyidagi tartibda tayyorlanadi:

1. `--image` bilan berilgan rasm;
2. Gemini generatsiyasi (`IG_IMAGE_MODEL`);
3. manbadagi rasm — faqat `IG_USE_SOURCE_IMAGE=true` bo'lsa
   (⚠️ boshqa saytning rasmi mualliflik huquqi bilan himoyalangan bo'lishi
   mumkin, shuning uchun sukut bo'yicha o'chirilgan);
4. matnli kartochka — hech biri ishlamasa, agent baribir chiroyli fon + sarlavha
   bilan kartochka yasaydi.

**Kommentlar.** Har `IG_COMMENT_POLL_MINUTES` daqiqada oxirgi 10 ta post
tekshiriladi. Har bir yangi komment Gemini'ga beriladi: u javob matnini
komment tilida yozadi, spam/haqoratni `hide` deb belgilaydi, keraksiz
kommentni `ignore` qiladi. Narx so'ralsa — "Direct'ga yozing" deb javob
beradi (raqam o'ylab topmaydi). Javob berilgan komment ID bazaga yoziladi,
shuning uchun ikki marta javob bermaydi.

---

## Buyruqlar

| Buyruq | Vazifasi |
|---|---|
| `check` | Sozlamalar, token, akkaunt va sutkalik limitni tekshirish |
| `post` | Hozir bitta post joylash |
| `post --dry-run` | Joylamasdan, matn va rasmni ko'rsatish |
| `post --topic "..."` | Shu safargi mavzuni qo'lda berish |
| `post --caption "..." --image rasm.jpg` | To'liq qo'lda post |
| `post --video reel.mp4` | Reels joylash |
| `post --no-internet` | RSS'siz, faqat generatsiya |
| `post --now` | Tasdiq so'ramasdan darrov joylash |
| `comments` | Kommentlarga bir marta javob berish |
| `comments --seed` | Javob yozmasdan, mavjud kommentlarni "ko'rilgan" qilish |
| `comments --dry-run` | Javoblarni faqat ekranga chiqarish |
| `drafts` | Oxirgi postlar va kutilayotgan qoralamalar |
| `approve <id>` | Kutilayotgan postni joylash |
| `refresh-token` | Tokenni yana 60 kunga uzaytirish |
| `run` | 24/7 rejim: jadval bo'yicha post + kommentlar |

## Sozlamalar to'liq ro'yxati

| O'zgaruvchi | Sukut bo'yicha | Ma'nosi |
|---|---|---|
| `IG_ACCESS_TOKEN` | — | Instagram tokeni (majburiy) |
| `IG_USER_ID` | `me` | Instagram akkaunt ID |
| `IG_LOGIN` | `instagram` | `instagram` yoki `facebook` |
| `IG_GRAPH_VERSION` | `v23.0` | API versiyasi (xato bersa yangilang) |
| `PUBLIC_BASE_URL` | — | Agentning ochiq manzili (rasm shu yerdan beriladi) |
| `IMGBB_API_KEY` | — | Muqobil rasm hosting |
| `GEMINI_API_KEY` | — | Matn va rasm generatsiyasi |
| `IG_TEXT_MODEL` | `gemini-2.5-flash` | Matn modeli |
| `IG_IMAGE_MODEL` | `gemini-2.5-flash-image` | Rasm modeli |
| `IG_BRAND` | — | Brend/sahifa nomi |
| `IG_TOPIC` | — | Sahifa mavzusi |
| `IG_LANGUAGE` | `uz` | Post tili (`uz`, `ru`, `en`, `uz-cyrl`) |
| `IG_TONE` | do'stona… | Yozish uslubi |
| `IG_CTA` | — | Har postga qo'shiladigan chaqiruv |
| `IG_HASHTAGS` | — | Doimiy hashtaglar (vergul bilan) |
| `IG_EXTRA_RULES` | — | Promptga qo'shimcha qoidalar |
| `IG_RSS_FEEDS` | — | Mavzu olinadigan RSS lentalar |
| `IG_USE_SOURCE_IMAGE` | `false` | Manbadagi rasmni olish (mualliflik huquqi!) |
| `IG_POST_TIMES` | `10:00,18:00` | Post vaqtlari |
| `IG_TIMEZONE` | `Asia/Tashkent` | Vaqt zonasi |
| `IG_MAX_POSTS_PER_DAY` | `2` | Kunlik post chegarasi |
| `IG_AUTO_REPLY` | `true` | Kommentlarga avtomatik javob |
| `IG_COMMENT_POLL_MINUTES` | `15` | Kommentlarni tekshirish oralig'i |
| `IG_COMMENT_LOOKBACK_HOURS` | `72` | Shu soatdan eski kommentga javob berilmaydi |
| `IG_COMMENT_MEDIA_LIMIT` | `10` | Nechta oxirgi post tekshiriladi |
| `IG_AUTO_HIDE_SPAM` | `false` | Spam kommentni avtomatik yashirish |
| `IG_REQUIRE_APPROVAL` | `false` | Postdan oldin Telegram'da tasdiq so'rash |
| `IG_NOTIFY_CHAT_ID` | — | Xabarlar yuboriladigan Telegram chat |
| `IG_TELEGRAM_TOKEN` | `BOT_TOKEN` | Xabar yuboradigan bot tokeni |
| `PORT` | `8080` | HTTP server porti |

---

## Tokenni yangilash (har 60 kunda)

Instagram tokeni 60 kun amal qiladi. Muddati tugashidan oldin:

```bash
python -m instagram.agent refresh-token
```

Chiqqan yangi tokenni `.env` va Render sozlamalariga qo'ying. (Token 60 kun
ichida kamida bir marta ishlatilgan bo'lishi kerak — agent har kuni ishlab
tursa, bu shart bajariladi.)

## Limitlar va ehtiyot choralari

* **24 soatda 25 ta post** — API chegarasi (`check` buyrug'i qolganini ko'rsatadi).
* **Caption** 2200 belgi, **hashtag** 30 tadan ko'p bo'lmasin (agent 25 tada to'xtaydi).
* Rasm **JPEG** bo'lishi, tomonlar nisbati 4:5 … 1.91:1 orasida bo'lishi kerak —
  agent buni o'zi to'g'rilaydi.
* Instagram haddan tashqari bir xil, tez-tez yoziladigan kommentlarni **spam**
  deb hisoblashi mumkin. Shuning uchun javoblar AI orqali har safar boshqacha
  yoziladi va oraliq 15 daqiqadan kam qilinmagan.
* Internetdan olingan matn **ko'chirilmaydi**, qayta yoziladi. Boshqa saytning
  **rasmini** olish esa sukut bo'yicha o'chirilgan — yoqsangiz, mualliflik
  huquqiga o'zingiz javob berasiz.
* AI xato yozishi mumkin. Muhim brend uchun birinchi kunlarda
  `IG_REQUIRE_APPROVAL=true` bilan ishlating.

## Tez-tez uchraydigan xatolar

| Xato | Sababi va yechimi |
|---|---|
| `code 190` | Token eskirgan → `refresh-token` yoki yangi token oling |
| `code 200` / `code 10` | Ruxsat yetishmayapti → ilovada scope'larni yoqing |
| `code 100, subcode 2207003` | Rasm URL'ini Instagram ocholmadi → `PUBLIC_BASE_URL` noto'g'ri yoki servis uxlab qolgan |
| `code 9007` | Sutkalik 25 ta limit tugagan |
| `The user is not an Instagram Business` | Akkaunt Professional emas → 1-qadam |
| Rasm o'rniga matnli kartochka chiqyapti | Gemini rasm modeli ishlamadi → `GEMINI_API_KEY` va `IG_IMAGE_MODEL` ni tekshiring |

## Fayllar

| Fayl | Vazifasi |
|---|---|
| `agent.py` | CLI, jadval, HTTP server, tasdiqlash havolalari |
| `api.py` | Instagram Graph API mijozi |
| `content.py` | RSS'dan mavzu + Gemini bilan matn yozish |
| `media.py` | Rasm generatsiyasi, formatlash, ochiq manzilga chiqarish |
| `comments.py` | Kommentlarni o'qish va javob berish |
| `store.py` | SQLite: postlar, javoblar, ishlatilgan manbalar |
| `notify.py` | Telegram orqali xabar va tasdiq so'rash |
| `config.py` | Barcha sozlamalar |
