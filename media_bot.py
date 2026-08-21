"""
Ijtimoiy tarmoqlardan (Instagram, YouTube, Facebook, X, TikTok) video va rasm
yuklab beruvchi, shuningdek musiqani nomi bo'yicha yoki audio/video fayl
orqali (Shazam kabi) topib beruvchi Telegram bot.

Katta hajmli fayllarni (2 GB gacha) yuborish uchun bu bot LOCAL Telegram Bot
API serveriga ulanadi (oddiy api.telegram.org emas). Local server sozlash
yo'riqnomasi DEPLOY.md faylida.
"""

import os
import re
import logging
import tempfile
import asyncio
from pathlib import Path

import requests
import yt_dlp
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest

# ==================== SOZLAMALAR ====================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Local Bot API server manzili, masalan: telegram-api.railway.internal:8081
# Bo'sh qoldirilsa, oddiy (bulutli) api.telegram.org ishlatiladi — bu holda
# 50 MB dan katta fayllar yuborilmaydi.
LOCAL_API_HOST = os.environ.get("LOCAL_API_HOST", "").strip()

# Xavfsizlik chegarasi — Telegram local server 2000 MB (2 GB) ga ruxsat
# beradi, biz ehtiyot shart sifatida biroz pastroq chegara qo'yamiz.
MAX_FILESIZE_MB = int(os.environ.get("MAX_FILESIZE_MB", "1900"))

# Musiqani audio/video fayl orqali tanib olish uchun AudD.io API kaliti.
# dashboard.audd.io orqali bepul olinadi. Bo'sh bo'lsa, bu funksiya o'chiq
# turadi (boshqa hammasi ishlayveradi).
AUDD_API_TOKEN = os.environ.get("AUDD_API_TOKEN", "").strip()
AUDD_URL = "https://api.audd.io/"

# YouTube serverlardan (Railway kabi) kelgan so'rovlarni tez-tez blokladi
# ("Sign in to confirm you're not a bot"). Buni aylanib o'tish uchun haqiqiy
# YouTube hisobidan eksport qilingan cookies (Netscape formatida, to'liq matn)
# shu o'zgaruvchiga joylashtiriladi. Bo'sh bo'lsa, cookie'siz urinib ko'radi
# (ba'zi videolar baribir ishlashi mumkin).
YOUTUBE_COOKIES_CONTENT = os.environ.get("YOUTUBE_COOKIES", "").strip()
YOUTUBE_COOKIES_FILE = Path(tempfile.gettempdir()) / "youtube_cookies.txt"
if YOUTUBE_COOKIES_CONTENT:
    YOUTUBE_COOKIES_FILE.write_text(YOUTUBE_COOKIES_CONTENT, encoding="utf-8")

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "media_bot_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

URL_PATTERN = re.compile(r"https?://\S+")

PLATFORM_NAMES = {
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "twitter.com": "X (Twitter)",
    "x.com": "X (Twitter)",
    "tiktok.com": "TikTok",
}

# YouTube'ning "Sign in to confirm you're not a bot" bloklashini kamaytirish
# uchun turli mijoz (client) turlarini sinab ko'ramiz — bu keng tarqalgan,
# rasmiy yt-dlp tavsiya etadigan usul.
YOUTUBE_EXTRACTOR_ARGS = {"youtube": {"player_client": ["ios", "android", "web"]}}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def detect_platform(url: str) -> str:
    for domain, name in PLATFORM_NAMES.items():
        if domain in url:
            return name
    return "Noma'lum manba"


def format_duration(seconds) -> str:
    if not seconds:
        return "N/A"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def build_caption(title: str, uploader: str, duration) -> str:
    uploader_display = uploader if uploader else "Noma'lum"
    return (
        f"🎬 {title}\n"
        f"👤 {uploader_display}\n"
        f"⏱ {format_duration(duration)}"
    )


# ==================== YUKLAB OLISH (yt-dlp) ====================

def download_media(url: str, user_id: str) -> dict:
    """Havoladan video/rasmni yuklab oladi. Natija: path/type/title/uploader/duration.

    Bloklovchi (sinxron) funksiya — asyncio.to_thread orqali chaqiriladi.
    """
    outtmpl = str(DOWNLOAD_DIR / f"{user_id}_%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": (
            f"bestvideo[filesize<{MAX_FILESIZE_MB}M]+bestaudio/"
            f"best[filesize<{MAX_FILESIZE_MB}M]/best"
        ),
        "merge_output_format": "mp4",
        "max_filesize": MAX_FILESIZE_MB * 1024 * 1024,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
    }
    if YOUTUBE_COOKIES_CONTENT:
        ydl_opts["cookiefile"] = str(YOUTUBE_COOKIES_FILE)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)

        if not os.path.exists(filepath):
            base = os.path.splitext(filepath)[0]
            for ext in ("mp4", "mkv", "webm", "jpg", "jpeg", "png", "webp"):
                candidate = f"{base}.{ext}"
                if os.path.exists(candidate):
                    filepath = candidate
                    break

        ext = os.path.splitext(filepath)[1].lower()
        media_type = "photo" if ext in (".jpg", ".jpeg", ".png", ".webp") else "video"

        return {
            "path": filepath,
            "type": media_type,
            "title": info.get("title") or "Media",
            "uploader": info.get("uploader") or "Noma'lum",
            "duration": info.get("duration"),
        }


def download_audio_by_query(query: str, user_id: str) -> dict:
    """Nom bo'yicha YouTube'dan qidirib, audio (mp3) formatda yuklab oladi.

    Bloklovchi (sinxron) funksiya — asyncio.to_thread orqali chaqiriladi.
    """
    outtmpl = str(DOWNLOAD_DIR / f"{user_id}_search_%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }
    if YOUTUBE_COOKIES_CONTENT:
        ydl_opts["cookiefile"] = str(YOUTUBE_COOKIES_FILE)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=True)
        if "entries" in info:
            info = info["entries"][0]
        filepath = ydl.prepare_filename(info)

        # Audio ekstraktsiyadan keyin kengaytma .mp3 ga o'zgaradi.
        base = os.path.splitext(filepath)[0]
        mp3_path = f"{base}.mp3"
        if os.path.exists(mp3_path):
            filepath = mp3_path

        return {
            "path": filepath,
            "type": "audio",
            "title": info.get("title") or "Musiqa",
            "uploader": info.get("uploader") or "Noma'lum",
            "duration": info.get("duration"),
        }


# ==================== MUSIQANI TANIB OLISH (AudD.io) ====================

def recognize_song(filepath: str) -> dict:
    """AudD.io orqali fayldagi musiqani tanib oladi. Topilmasa None qaytaradi."""
    with open(filepath, "rb") as f:
        response = requests.post(
            AUDD_URL,
            data={"api_token": AUDD_API_TOKEN, "return": "apple_music,spotify"},
            files={"file": f},
            timeout=60,
        )
    response.raise_for_status()
    data = response.json()
    return data.get("result")


# ==================== YORDAMCHI: NATIJANI YUBORISH ====================

async def send_download_result(message, status_msg, result: dict) -> None:
    """Yuklab olingan video/rasm/audio faylni yuboradi va vaqtinchalik faylni tozalaydi."""
    filepath = result["path"]
    try:
        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)

        if file_size_mb > MAX_FILESIZE_MB:
            await status_msg.edit_text(f"❌ Fayl juda katta ({file_size_mb:.0f} MB), yubora olmayman.")
            return

        caption = build_caption(result["title"], result.get("uploader"), result.get("duration"))
        duration = result.get("duration")

        await status_msg.edit_text(f"📤 Yuborilmoqda... ({file_size_mb:.1f} MB)")

        with open(filepath, "rb") as f:
            if result["type"] == "photo":
                await message.reply_chat_action("upload_photo")
                await message.reply_photo(photo=f, caption=caption)
            elif result["type"] == "audio":
                await message.reply_chat_action("upload_voice")
                await message.reply_audio(
                    audio=f,
                    caption=caption,
                    title=result["title"][:64],
                    performer=(result.get("uploader") or "")[:64],
                    duration=int(duration) if duration else None,
                    write_timeout=1800,
                    read_timeout=1800,
                )
            else:
                await message.reply_chat_action("upload_video")
                await message.reply_video(
                    video=f,
                    caption=caption,
                    supports_streaming=True,
                    write_timeout=1800,
                    read_timeout=1800,
                )

        await status_msg.delete()
    finally:
        try:
            os.remove(filepath)
        except OSError:
            pass


# ==================== TELEGRAM BUYRUQLARI ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Salom! 👋 Men video/musiqa yuklab beruvchi va topib beruvchi botman.\n\n"
        "📎 Instagram, YouTube, Facebook, X yoki TikTok havolasini yuboring — "
        "video/rasmni yuklab beraman.\n\n"
        "🎵 Qo'shiq nomini yozing (masalan \"Dildora Sevgi\") — men uni topib, "
        "audio qilib yuboraman.\n\n"
        "🎧 Audio, video yoki ovozli xabar yuboring — undagi musiqani tanib, "
        "sifatli faylini topib beraman.\n\n"
        "⚠️ Eslatma: faqat mualliflik huquqi ruxsat bergan yoki shaxsiy "
        "foydalanish uchun kontentni yuklab oling."
    )
    await update.message.reply_text(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    url = URL_PATTERN.search(text).group(0)
    user_id = str(update.effective_user.id)
    platform = detect_platform(url)

    status_msg = await update.message.reply_text(f"⏳ {platform}'dan yuklab olinmoqda...")
    await update.message.reply_chat_action("upload_video")

    try:
        result = await asyncio.to_thread(download_media, url, user_id)
    except yt_dlp.utils.DownloadError as e:
        error_text = str(e)
        if "max-filesize" in error_text.lower() or "file is larger" in error_text.lower():
            await status_msg.edit_text(f"❌ Fayl {MAX_FILESIZE_MB} MB dan katta, yubora olmayman.")
        elif "sign in" in error_text.lower() or "confirm" in error_text.lower():
            await status_msg.edit_text(
                "❌ YouTube hozircha bu videoni berishni istamayapti (botlarga qarshi "
                "himoya). Boshqa video bilan urinib ko'ring yoki birozdan so'ng qayta "
                "urinib ko'ring."
            )
        else:
            await status_msg.edit_text(
                "❌ Yuklab olishda xato. Havola to'g'riligini yoki kontent "
                "ochiq (public) ekanligini tekshiring."
            )
        logger.error(f"yt-dlp xatosi: {error_text[:500]}")
        return
    except Exception as e:
        logger.error(f"Kutilmagan xato: {e}")
        await status_msg.edit_text("❌ Kutilmagan xato yuz berdi. Qayta urinib ko'ring.")
        return

    await send_download_result(update.message, status_msg, result)


async def handle_text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Havola bo'lmagan matnli xabarni musiqa qidiruvi sifatida qayta ishlaydi."""
    query = (update.message.text or "").strip()
    if not query:
        return

    user_id = str(update.effective_user.id)
    status_msg = await update.message.reply_text(f"🔍 Qidirilmoqda: {query}")
    await update.message.reply_chat_action("upload_voice")

    try:
        result = await asyncio.to_thread(download_audio_by_query, query, user_id)
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Qidiruv xatosi: {str(e)[:500]}")
        await status_msg.edit_text(
            "❌ Topilmadi yoki yuklab bo'lmadi. Boshqa nom bilan urinib ko'ring, "
            "yoki to'g'ridan-to'g'ri video havolasini yuboring."
        )
        return
    except Exception as e:
        logger.error(f"Kutilmagan xato: {e}")
        await status_msg.edit_text("❌ Kutilmagan xato yuz berdi. Qayta urinib ko'ring.")
        return

    await send_download_result(update.message, status_msg, result)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    if URL_PATTERN.search(text):
        await handle_link(update, context)
    else:
        await handle_text_search(update, context)


async def handle_media_recognition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Yuborilgan audio/video/ovozli xabardagi musiqani AudD.io orqali tanib, topib beradi."""
    if not AUDD_API_TOKEN:
        await update.message.reply_text(
            "🎧 Musiqani fayl orqali tanish funksiyasi hali sozlanmagan "
            "(AUDD_API_TOKEN yo'q). Administrator DEPLOY.md ga qarasin."
        )
        return

    message = update.message
    tg_file = None
    if message.voice:
        tg_file = await message.voice.get_file()
    elif message.audio:
        tg_file = await message.audio.get_file()
    elif message.video_note:
        tg_file = await message.video_note.get_file()
    elif message.video:
        tg_file = await message.video.get_file()
    elif message.document and message.document.mime_type and (
        message.document.mime_type.startswith("audio/")
        or message.document.mime_type.startswith("video/")
    ):
        tg_file = await message.document.get_file()

    if tg_file is None:
        return

    user_id = str(update.effective_user.id)
    status_msg = await message.reply_text("🎧 Musiqa aniqlanmoqda...")

    local_path = DOWNLOAD_DIR / f"recognize_{user_id}_{tg_file.file_unique_id}"
    await tg_file.download_to_drive(custom_path=str(local_path))

    try:
        result = await asyncio.to_thread(recognize_song, str(local_path))
    except Exception as e:
        logger.error(f"AudD xatosi: {e}")
        await status_msg.edit_text("❌ Aniqlashda xato yuz berdi. Qayta urinib ko'ring.")
        return
    finally:
        try:
            os.remove(local_path)
        except OSError:
            pass

    if not result:
        await status_msg.edit_text("😕 Kechirasiz, bu musiqani aniqlay olmadim.")
        return

    artist = result.get("artist", "")
    title = result.get("title", "")
    query = f"{artist} - {title}".strip(" -") or title or artist

    await status_msg.edit_text(f"🎵 Topildi: {artist} — {title}\n⏳ Yuklab olinmoqda...")
    await message.reply_chat_action("upload_voice")

    try:
        download_result = await asyncio.to_thread(download_audio_by_query, query, user_id)
    except Exception as e:
        logger.error(f"Audio yuklashda xato: {e}")
        await status_msg.edit_text(
            f"🎵 Aniqlandi: {artist} — {title}\nLekin audio faylni yuklab bo'lmadi."
        )
        return

    await send_download_result(message, status_msg, download_result)


# ==================== ASOSIY FUNKSIYA ====================

def build_application() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN muhit o'zgaruvchisi topilmadi. DEPLOY.md ni ko'ring.")

    request = HTTPXRequest(
        connect_timeout=60,
        read_timeout=1800,
        write_timeout=1800,
        pool_timeout=60,
    )

    builder = Application.builder().token(TELEGRAM_BOT_TOKEN).request(request)

    if LOCAL_API_HOST:
        base_url = f"http://{LOCAL_API_HOST}/bot"
        base_file_url = f"http://{LOCAL_API_HOST}/file/bot"
        builder = builder.base_url(base_url).base_file_url(base_file_url)
        logger.info(f"Local Bot API server ishlatilmoqda: {LOCAL_API_HOST}")
    else:
        logger.warning(
            "LOCAL_API_HOST sozlanmagan — oddiy api.telegram.org ishlatiladi "
            f"(50 MB limit bilan). MAX_FILESIZE_MB={MAX_FILESIZE_MB} bo'lsa ham, "
            "amalda 50 MB dan katta fayllar yuborilmaydi."
        )

    if not AUDD_API_TOKEN:
        logger.warning(
            "AUDD_API_TOKEN sozlanmagan — audio/video fayl orqali musiqa "
            "tanish funksiyasi o'chiq turadi."
        )

    if YOUTUBE_COOKIES_CONTENT:
        logger.info("YouTube cookies topildi — bot login sessiyasi bilan ishlaydi.")
    else:
        logger.warning(
            "YOUTUBE_COOKIES sozlanmagan — YouTube ba'zi videolarni "
            "\"Sign in to confirm you're not a bot\" deb bloklashi mumkin. "
            "DEPLOY.md dagi yo'riqnomaga qarang."
        )

    return builder.build()


def main() -> None:
    app = build_application()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(
        filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE
        | filters.Document.AUDIO | filters.Document.VIDEO,
        handle_media_recognition,
    ))

    logger.info("Media bot ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
