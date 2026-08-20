"""
Ijtimoiy tarmoqlardan (Instagram, YouTube, Facebook, X, TikTok) video va rasm
yuklab beruvchi Telegram bot.

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


# ==================== YUKLAB OLISH (yt-dlp) ====================

def download_media(url: str, user_id: str) -> dict:
    """yt-dlp orqali videoni/rasmni yuklab oladi. Natija: {"path", "type", "title"}.

    Bu funksiya bloklovchi (sinxron) — asosiy event loop'ni band qilmaslik
    uchun uni alohida thread'da chaqirish kerak (asyncio.to_thread orqali).
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
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)

        # Video+audio birlashtirilganda kengaytma o'zgargan bo'lishi mumkin.
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
        }


# ==================== TELEGRAM BUYRUQLARI ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Salom! 👋 Men Instagram, YouTube, Facebook, X (Twitter) va TikTok'dan "
        "video/rasm yuklab beruvchi botman.\n\n"
        "📎 Shunchaki video havolasini (link) menga yuboring — yuklab olib, "
        "shu yerga jo'nataman.\n\n"
        f"📦 Maksimal fayl hajmi: {MAX_FILESIZE_MB} MB\n\n"
        "⚠️ Eslatma: faqat mualliflik huquqi ruxsat bergan yoki shaxsiy "
        "foydalanish uchun kontentni yuklab oling."
    )
    await update.message.reply_text(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    match = URL_PATTERN.search(text)
    if not match:
        await update.message.reply_text(
            "Iltimos, video havolasini (link) yuboring. Masalan: "
            "https://www.instagram.com/reel/..."
        )
        return

    url = match.group(0)
    user_id = str(update.effective_user.id)
    platform = detect_platform(url)

    status_msg = await update.message.reply_text(f"⏳ {platform}'dan yuklab olinmoqda...")
    await update.message.reply_chat_action("upload_video")

    try:
        result = await asyncio.to_thread(download_media, url, user_id)
    except yt_dlp.utils.DownloadError as e:
        error_text = str(e)
        if "max-filesize" in error_text.lower() or "file is larger" in error_text.lower():
            await status_msg.edit_text(
                f"❌ Fayl {MAX_FILESIZE_MB} MB dan katta, yubora olmayman."
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

    filepath = result["path"]
    try:
        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)

        if file_size_mb > MAX_FILESIZE_MB:
            await status_msg.edit_text(
                f"❌ Fayl juda katta ({file_size_mb:.0f} MB), yubora olmayman."
            )
            return

        await status_msg.edit_text(f"📤 Yuborilmoqda... ({file_size_mb:.1f} MB)")
        await update.message.reply_chat_action(
            "upload_photo" if result["type"] == "photo" else "upload_video"
        )

        with open(filepath, "rb") as f:
            if result["type"] == "photo":
                await update.message.reply_photo(photo=f, caption=result["title"][:1024])
            else:
                await update.message.reply_video(
                    video=f,
                    caption=result["title"][:1024],
                    supports_streaming=True,
                    write_timeout=1800,
                    read_timeout=1800,
                )

        await status_msg.delete()
    finally:
        # Vaqtinchalik faylni tozalaymiz — diskni to'ldirmasligi uchun.
        try:
            os.remove(filepath)
        except OSError:
            pass


# ==================== ASOSIY FUNKSIYA ====================

def build_application() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN muhit o'zgaruvchisi topilmadi. DEPLOY.md ni ko'ring.")

    # Katta fayllar yuborish uzoq vaqt olishi mumkin — timeout'larni oshiramiz.
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

    return builder.build()


def main() -> None:
    app = build_application()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    logger.info("Media bot ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
