# Media Yuklab Beruvchi Bot — To'liq Sozlash Yo'riqnomasi

Bu bot Instagram, YouTube, Facebook, X (Twitter) va TikTok'dan video/rasm
yuklab, Telegram orqali yuboradi. Katta hajmli fayllarni (2 GB gacha)
yuborish uchun **ikkita xizmat** kerak bo'ladi:

1. **Local Bot API Server** — Telegram'ning o'zi taqdim etadigan dastur,
   50 MB o'rniga 2 GB gacha fayl yuborish imkonini beradi.
2. **Bizning bot kodimiz** (`media_bot.py`) — yuklab olish va yuborish
   mantig'i.

Ikkalasi ham Railway'da, **bitta loyihada, ikkita alohida xizmat**
sifatida ishga tushiriladi.

---

## 1-qadam: Yangi Telegram bot yaratish

1. **@BotFather** ga o'ting, `/newbot` yuboring.
2. Nom va username bering (masalan `MeningMediaBotim_bot`).
3. Olingan tokenni saqlab qo'ying.

## 2-qadam: Telegram API ID va Hash olish

Bu — BotFather tokenidan **butunlay boshqa** narsa, Local Bot API Server
uchun kerak:

1. [my.telegram.org](https://my.telegram.org) ga o'ting.
2. Telefon raqamingiz orqali kiring (SMS kod keladi).
3. **API development tools** ni tanlang.
4. Bo'sh forma chiqadi — "App title" va "Short name" ga istalgan nom yozing
   (masalan "MediaBot"), qolganini bo'sh qoldirsangiz ham bo'ladi.
5. **Create application** bosing.
6. Sizga **api_id** (raqam) va **api_hash** (harf-raqamli qator) beriladi —
   ikkalasini ham saqlab qo'ying.

## 3-qadam: GitHub'ga fayllarni yuklash

Quyidagi fayllarni (`media_bot.py`, `requirements.txt`, `Procfile`,
`nixpacks.toml`) yangi GitHub repository'ga yuklang — xuddi avvalgi
botda qilganingizdek ("uploading an existing file" orqali).

> 💡 Bu **butunlay yangi** repository bo'lishi kerak (avvalgi vazifa
> botidan alohida), chunki bu mustaqil bot.

## 4-qadam: Railway'da yangi loyiha yaratish

1. [railway.app](https://railway.app) da **New Project** → **Deploy from
   GitHub repo** → yangi repository'ingizni tanlang.
2. Bu bizning **bot xizmati** (masalan nomini `media-bot` deb o'zgartiring —
   xizmat ustiga bosib, **Settings** → **Service Name**).

## 5-qadam: Local Bot API Server xizmatini qo'shish

Endi shu loyihaga **ikkinchi xizmat** qo'shamiz:

1. Loyiha maydonida (canvas) **Ctrl+K** (Mac: **⌘K**) bosing, qidiruv
   oynasiga **"empty service"** yozing va tanlang. (Yoki bo'sh joyga
   o'ng tugma bosib, **"Empty Service"** ni tanlang.)
2. Yaratilgan xizmat ustiga bosing → **Settings** bo'limiga o'ting.
3. **Source** qismida **Docker Image** ni tanlang va quyidagi nomni kiriting:
   ```
   evilfreelancer/docker-telegram-bot-api:latest
   ```
4. Xizmat nomini `telegram-api` deb o'zgartiring (**Settings** →
   **Service Name**) — bu keyingi qadamda kerak bo'ladi.
5. **Variables** bo'limiga o'ting, quyidagilarni qo'shing:

   | Kalit nomi | Qiymati |
   |---|---|
   | `TELEGRAM_API_ID` | 2-qadamda olingan api_id |
   | `TELEGRAM_API_HASH` | 2-qadamda olingan api_hash |
   | `TELEGRAM_LOCAL` | `true` |

6. **Bu xizmat uchun ochiq (public) domen yaratmang** — u faqat bizning
   bot xizmatimiz uchun, ichki tarmoqda ishlaydi, tashqi internetga
   chiqarish shart emas va xavfsizroq.

### Volume qo'shish (Local Bot API Server uchun)

Fayllarni vaqtincha saqlash uchun bu xizmatga ham Volume kerak:

1. Yana **Ctrl+K** → **"volume"** → shu `telegram-api` xizmatiga ulang.
2. **Mount path**: `/var/lib/telegram-bot-api`

## 6-qadam: Ikki xizmatni bir-biriga ulash

Railway'da bir loyihadagi xizmatlar bir-biriga **ichki tarmoq (private
networking)** orqali `<xizmat-nomi>.railway.internal` manzili bilan
ulanadi — bu bepul va tashqi internetga chiqmaydi.

1. **Bot xizmatiga** (`media-bot`) qayting → **Variables**.
2. Quyidagilarni qo'shing:

   | Kalit nomi | Qiymati |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | 1-qadamda olingan bot tokeni |
   | `LOCAL_API_HOST` | `telegram-api.railway.internal:8081` |
   | `MAX_FILESIZE_MB` | `1900` (ixtiyoriy, standart shu) |

   > Agar `telegram-api` xizmatiga boshqa nom bergan bo'lsangiz, shu
   > nomga mos ravishda `LOCAL_API_HOST` qiymatini o'zgartiring.

3. Saqlagach, Railway ikkala xizmatni ham avtomatik qayta ishga tushiradi.

## 7-qadam: Tekshirish

1. **`telegram-api`** xizmatining **Deployments → View Logs** bo'limida
   xatosiz ishga tushganini tekshiring.
2. **`media-bot`** xizmatining loglarida **"Local Bot API server
   ishlatilmoqda: telegram-api.railway.internal:8081"** degan qatorni
   ko'rishingiz kerak.
3. Telegram'da botingizga o'ting, `/start` yuboring, so'ng biror
   Instagram/YouTube/TikTok video havolasini yuboring va sinab ko'ring.

---

## 8-qadam (ixtiyoriy): Musiqani fayl orqali tanish (Shazam kabi)

Agar foydalanuvchi audio, video yoki ovozli xabar yuborsa, bot undagi
musiqani **AudD.io** xizmati orqali tanib, keyin uni to'liq holda topib
yuboradi. Bu funksiya ixtiyoriy — sozlanmasa, botning qolgan qismi (havola
orqali yuklash, nom bo'yicha qidirish) baribir ishlayveradi.

1. [dashboard.audd.io](https://dashboard.audd.io) ga o'ting, ro'yxatdan o'ting.
2. Boshqaruv panelida **API Token** ni toping va nusxalang.
3. `media-bot` xizmatida **Variables** ga o'ting, qo'shing:

   | Kalit nomi | Qiymati |
   |---|---|
   | `AUDD_API_TOKEN` | AudD dashboard'idan olingan token |

4. Saqlagach, Railway avtomatik qayta deploy qiladi.

> 💡 AudD'ning bepul tarifi cheklangan so'rovlar soniga ega. Agar
> ko'proq foydalanish kerak bo'lsa, dashboard'da tarifni oshirish
> mumkin.



Oddiy Telegram Bot API (`api.telegram.org`) orqali bot faqat **50 MB**
gacha fayl yubora oladi. Local Bot API Server esa Telegram'ning o'zi
taqdim etgan, siz o'zingizning serveringizda ishga tushiradigan dastur —
u orqali bu chegara **2 GB**gacha ko'tariladi. Bizning bot kodimiz endi
`api.telegram.org` o'rniga shu local serverga ulanadi.

## Muammo yuzaga kelsa

- **`telegram-api` xizmati ishga tushmayapti** — `TELEGRAM_API_ID` va
  `TELEGRAM_API_HASH` to'g'ri kiritilganini tekshiring (ID — raqam,
  Hash — harf-raqamli qator, ikkalasi ham qo'shtirnoqsiz).
- **Bot "Local Bot API server ishlatilmoqda" deb yozmayapti** — `media-bot`
  xizmatida `LOCAL_API_HOST` o'zgaruvchisi to'g'ri yozilganini va
  `telegram-api` xizmat nomi bilan mos kelishini tekshiring.
- **Yuklab olishda xato** — ba'zi Instagram/Facebook postlari shaxsiy
  (private) yoki himoyalangan bo'lishi mumkin, bunday kontentni bot
  yuklab ololmaydi (bu — Telegram cheklovi emas, platformaning o'zi).
- **ffmpeg xatosi** — `nixpacks.toml` fayli to'g'ri yuklanganini
  tekshiring, bu ffmpeg dasturini avtomatik o'rnatadi.

## 9-qadam: YouTube blokini aylanib o'tish (cookies)

YouTube server orqali (Railway kabi) kelgan so'rovlarni tez-tez
**"Sign in to confirm you're not a bot"** deb bloklaydi — bu bizning
kodimizdagi xato emas, bu YouTube'ning o'zi serverlardan kelgan
so'rovlarga qo'ygan cheklovi va deyarli barcha shunga o'xshash botlarga
ta'sir qiladi. Eng ishonchli yechim — haqiqiy YouTube hisobidan
**cookies** (login sessiyasi) ishlatish.

⚠️ Tavsiya: buning uchun **asosiy shaxsiy hisobingizni emas**, alohida
(zaxira) Google hisobini ishlatgan ma'qul.

1. Brauzeringizga **"Get cookies.txt LOCALLY"** kengaytmasini o'rnating
   (Chrome yoki Firefox uchun, Chrome/Firefox do'konida qidiring).
2. Shu (zaxira) hisobingiz bilan [youtube.com](https://youtube.com) ga kiring.
3. YouTube sahifasida turib, kengaytma ikonkasini bosing → **Export**
   (yoki **Copy**) → `youtube.com` uchun cookie'larni oling.
4. Chiqqan matnni (bir necha qatordan iborat, "Netscape HTTP Cookie
   File" bilan boshlanadi) to'liq nusxalang.
5. Railway'da `media-bot` xizmati → **Variables** → yangi o'zgaruvchi
   qo'shing:

   | Kalit nomi | Qiymati |
   |---|---|
   | `YOUTUBE_COOKIES` | nusxalangan to'liq cookie matni (bir necha qator) |

   Railway'ning Variables maydoni ko'p qatorli matnni qabul qiladi —
   shunchaki to'liq matnni joylashtiring (paste).

6. Saqlagach, Railway avtomatik qayta deploy qiladi.

> 💡 Cookie'lar vaqti-vaqti bilan (bir necha hafta-oydan keyin) eskirib
> qolishi mumkin — agar YouTube yuklab olish yana bloklana boshlasa, shu
> qadamlarni takrorlab, yangi cookie oling.

## 10-qadam: ffmpeg muammosi (agar davom etsa)

Bu loyihada endi **Dockerfile** bor — bu ffmpeg'ni Railway'ning
Nixpacks/Railpack avtomatik aniqlashiga bog'liq bo'lmagan holda,
to'liq ishonchli o'rnatadi. Railway odatda `Dockerfile` mavjudligini
o'zi payqab, qurilish usulini avtomatik shunga almashtiradi.

Agar build loglarida hali ham Nixpacks/Railpack ishlatilayotganini
ko'rsangiz:

1. `media-bot` xizmati → **Settings** → **Build** bo'limiga o'ting.
2. **Builder** qiymati **Dockerfile** ekanligini tekshiring (agar
   boshqacha bo'lsa, qo'lda **Dockerfile**ni tanlang).
3. Qayta deploy qiling.

## Muhim eslatma: mualliflik huquqi

Bu bot faqat **shaxsiy foydalanish** yoki mualliflik huquqi egasi ruxsat
bergan kontent uchun mo'ljallangan. Boshqalarning kontentini ruxsatsiz
yuklab olib tarqatish ko'plab platformalarning foydalanish shartlarini
va ba'zi hollarda mualliflik huquqi qonunlarini buzishi mumkin — botni
mas'uliyat bilan ishlating.
