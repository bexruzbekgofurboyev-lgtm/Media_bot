"""
Ijtimoiy tarmoqlardan (Instagram, YouTube, Facebook, X, TikTok) video va rasm
yuklab beruvchi, shuningdek musiqani nomi bo'yicha yoki audio/video fayl
orqali (Shazam kabi) topib beruvchi Telegram bot.

Katta hajmli fayllarni (2 GB gacha) yuborish uchun bu bot LOCAL Telegram Bot
API serveriga ulanadi.
"""

import os
import re
import gc
import logging
import tempfile
import asyncio
import shutil
import subprocess
from pathlib import Path
from shazamio import Shazam
import requests
import yt_dlp

from telegram import (
    Update,
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from telegram.request import HTTPXRequest

# ============================================================
# SOZLAMALAR
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Local Telegram Bot API
LOCAL_API_HOST = os.environ.get("LOCAL_API_HOST", "").strip()

# Maksimal fayl hajmi
MAX_FILESIZE_MB = int(os.environ.get("MAX_FILESIZE_MB", "1900"))

# Admin Telegram chat ID
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()

# Feedback holati: foydalanuvchi ID'lari
PENDING_FEEDBACK = set()

# ============================================================
# DOWNLOAD PAPKA
# ============================================================

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "media_bot_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# URL PATTERNS & PLATFORMS
# ============================================================

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

# ============================================================
# YOUTUBE OPTIONS & HEADERS
# ============================================================

YOUTUBE_EXTRACTOR_ARGS = {
    "youtube": {
        "player_client": ["tv", "android", "web"],
        "po_token": ["web", "mweb"],
    }
}

YOUTUBE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Bir vaqtning o'zida faqat bitta katta download.
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(5)

# user_id -> {"url": "..."}
PENDING_YOUTUBE = {}

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# ============================================================
# SYSTEM CHECK
# ============================================================

def check_system_dependencies() -> None:
    try:
        logger.info(f"yt-dlp version: {yt_dlp.version.__version__}")
    except Exception:
        logger.warning("yt-dlp versionini aniqlab bo'lmadi.")

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        logger.info(f"FFmpeg topildi: {ffmpeg_path}")
    else:
        logger.warning("FFmpeg topilmadi! Ovoz ajratish ishlamaydi.")

# ============================================================
# YORDAMCHI FUNKSIYALAR
# ============================================================

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
    return f"🎬 {title}\n👤 {uploader_display}\n⏱ {format_duration(duration)}"

# ============================================================
# YOUTUBE FORMATS
# ============================================================

YOUTUBE_FORMATS = {
    "360": "bestvideo[height<=360]+bestaudio/best[height<=360]",
    "480": "bestvideo[height<=480]+bestaudio/best[height<=480]",
    "720": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "1440": "bestvideo[height<=1440]+bestaudio/best[height<=1440]",
    "2160": "bestvideo[height<=2160]+bestaudio/best[height<=2160]",
    "audio": "bestaudio/best",
}

QUALITY_LABELS = {
    "360": "360p",
    "480": "480p",
    "720": "720p",
    "1080": "1080p",
    "1440": "1440p (2K)",
    "2160": "2160p (4K)",
    "audio": "🎵 MP3",
}

FOUR_K_MAX_MB = 1900

def get_max_filesize_mb(quality: str) -> int:
    if quality == "2160":
        return FOUR_K_MAX_MB
    return MAX_FILESIZE_MB

def get_youtube_options(outtmpl: str, quality: str = "720", audio_only: bool = False) -> dict:
    ydl_opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
        "buffersize": 1024 * 1024,
        "http_chunk_size": 10 * 1024 * 1024,
        "http_headers": YOUTUBE_HEADERS,
        "proxy": "socks5://127.0.0.1:40000",
        "username": "oauth2", # OAuth2 orqali blokirovkani aylanib o'tish
        "ignoreerrors": True, # Xatolarni qulatib yubormaslik uchun
    }

    if audio_only:
        ydl_opts.update({
            "format": YOUTUBE_FORMATS["audio"],
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        ydl_opts.update({
            "format": YOUTUBE_FORMATS.get(quality, YOUTUBE_FORMATS["720"]),
            "merge_output_format": "mp4",
            "max_filesize": get_max_filesize_mb(quality) * 1024 * 1024,
            "keepvideo": False,
        })
    
    return ydl_opts

# ============================================================
# MEDIA DOWNLOAD (Video/Rasm)
# ============================================================

def download_media(url: str, user_id: str, quality: str = "720") -> dict:
    outtmpl = str(DOWNLOAD_DIR / f"{user_id}_%(id)s.%(ext)s")

    if "youtube.com" in url or "youtu.be" in url:
        ydl_opts = get_youtube_options(outtmpl=outtmpl, quality=quality, audio_only=False)
    else:
        ydl_opts = {
            "outtmpl": outtmpl,
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "max_filesize": MAX_FILESIZE_MB * 1024 * 1024,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "buffersize": 1024 * 1024,
            "http_chunk_size": 10 * 1024 * 1024,
            "keepvideo": False,
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        logger.info(f"Yuklanmoqda: {url} | Sifat: {quality}")
        
        try:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise yt_dlp.utils.DownloadError("YouTube kontentni bermadi. (OAUTH2 tokenni terminal orqali ulang yoki bot kuting)")
        except Exception as e:
            raise yt_dlp.utils.DownloadError(f"Yuklashda xato yuz berdi: {str(e)}")

        filepath = ydl.prepare_filename(info)

        # Kengaytmani to'g'irlash
        if not os.path.exists(filepath):
            base = os.path.splitext(filepath)[0]
            for ext in ("mp4", "mkv", "webm", "jpg", "jpeg", "png", "webp"):
                candidate = f"{base}.{ext}"
                if os.path.exists(candidate):
                    filepath = candidate
                    break

        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Fayl topilmadi: {filepath}")

        ext = os.path.splitext(filepath)[1].lower()
        media_type = "photo" if ext in (".jpg", ".jpeg", ".png", ".webp") else "video"

        return {
            "path": filepath,
            "type": media_type,
            "title": info.get("title") or "Media",
            "uploader": info.get("uploader") or "Noma'lum",
            "duration": info.get("duration"),
        }

# ============================================================
# AUDIO SEARCH
# ============================================================

def download_audio_by_query(query: str, user_id: str) -> dict:
    outtmpl = str(DOWNLOAD_DIR / f"{user_id}_search_%(id)s.%(ext)s")
    ydl_opts = get_youtube_options(outtmpl=outtmpl, audio_only=True)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        logger.info(f"YouTube audio qidiruv: {query}")
        
        try:
            info = ydl.extract_info(f"ytsearch1:{query}", download=True)
            if not info or ("entries" in info and not info["entries"]):
                raise yt_dlp.utils.DownloadError("Qidiruv natijasi bo'sh qaytdi.")
            
            if "entries" in info and info["entries"]:
                if info["entries"][0] is not None:
                    info = info["entries"][0]
                else:
                    raise yt_dlp.utils.DownloadError("Topilgan video tafsilotlari shikastlangan.")
        except Exception as e:
            raise yt_dlp.utils.DownloadError(f"Audio qidirishda xato: {str(e)}")

        filepath = ydl.prepare_filename(info)
        base = os.path.splitext(filepath)[0]
        mp3_path = f"{base}.mp3"

        if os.path.exists(mp3_path):
            filepath = mp3_path

        if not os.path.exists(filepath):
            raise FileNotFoundError("Yuklangan MP3 fayl topilmadi.")

        return {
            "path": filepath,
            "type": "audio",
            "title": info.get("title") or "Musiqa",
            "uploader": info.get("uploader") or "Noma'lum",
            "duration": info.get("duration"),
        }

# ============================================================
# SHAZAM RECOGNITION
# ============================================================

RECOGNITION_CLIP_SECONDS = 25

def extract_recognition_clip(input_path: str) -> str:
    output_path = f"{input_path}_clip.mp3"
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-t", str(RECOGNITION_CLIP_SECONDS),
                "-vn", "-acodec", "libmp3lame",
                "-ar", "44100", "-ac", "2", output_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        if result.returncode == 0 and os.path.exists(output_path):
            return output_path
    except Exception as e:
        logger.warning(f"ffmpeg xatosi: {e}")
    return input_path

async def recognize_song(filepath: str) -> dict | None:
    shazam = Shazam()
    out = await shazam.recognize(filepath)
    track = out.get("track")
    if not track:
        return None
    return {
        "title": track.get("title", ""),
        "artist": track.get("subtitle", ""),
    }

# ============================================================
# ADMIN & FEEDBACK
# ============================================================

def get_user_info(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "👤 Noma'lum"
    full_name = user.full_name or "Noma'lum"
    username = f"@{user.username}" if user.username else "yo'q"
    return f"👤 {full_name} | 🔹 {username} | 🆔 {user.id}"

async def send_to_admin(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, disable_web_page_preview=True)
        except Exception as e:
            logger.exception(f"Adminga yuborishda xato: {e}")

async def notify_admin_about_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, platform: str) -> None:
    user_info = get_user_info(update)
    await send_to_admin(context, f"🔗 YANGI LINK\n\n{user_info}\n🌐 {platform}\n🔗 {url}")

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    PENDING_FEEDBACK.add(update.effective_user.id)
    await update.message.reply_text("✍️ Feedback yozing. Keyingi xabaringiz adminga yuboriladi.")

async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in PENDING_FEEDBACK:
        return
    feedback = (update.message.text or "").strip()
    if not feedback:
        await update.message.reply_text("❌ Xabar bo'sh.")
        return
    await send_to_admin(context, f"📩 FEEDBACK\n\n{get_user_info(update)}\n💬 {feedback}")
    PENDING_FEEDBACK.discard(user_id)
    await update.message.reply_text("✅ Yuborildi. Rahmat!")

# ============================================================
# INLINE KEYBOARDS
# ============================================================

def youtube_quality_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("360p", callback_data="ytq:360"), InlineKeyboardButton("480p", callback_data="ytq:480")],
        [InlineKeyboardButton("720p", callback_data="ytq:720"), InlineKeyboardButton("1080p", callback_data="ytq:1080")],
        [InlineKeyboardButton("2K (1440p)", callback_data="ytq:1440"), InlineKeyboardButton("4K (2160p)", callback_data="ytq:2160")],
        [InlineKeyboardButton("🎵 MP3", callback_data="ytq:audio")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ============================================================
# SENDER (Fayllarni yuborish)
# ============================================================

async def download_and_send(message, status_msg, url: str, user_id: str, quality: str) -> None:
    result = None
    async with DOWNLOAD_SEMAPHORE:
        await status_msg.edit_text(f"⏳ Yuklanmoqda... Sifat: {QUALITY_LABELS.get(quality, quality)}")
        try:
            result = await asyncio.to_thread(download_media, url, user_id, quality)
        except Exception as e:
            logger.error(f"Xato ushlandi: {str(e)}")
            await status_msg.edit_text(f"❌ Yuklab olishda xato yuz berdi:\n\n{str(e)[:150]}")
            return

    if result is None:
        return

    await process_and_send_file(message, status_msg, result, quality)

async def download_and_send_existing(message, status_msg, result: dict) -> None:
    await process_and_send_file(message, status_msg, result, "720")

async def process_and_send_file(message, status_msg, result: dict, quality: str):
    filepath = result["path"]
    try:
        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        limit = get_max_filesize_mb(quality)

        if file_size_mb > limit:
            await status_msg.edit_text(f"❌ Fayl juda katta: {file_size_mb:.0f} MB (Limit: {limit} MB)")
            return

        caption = build_caption(result["title"], result.get("uploader"), result.get("duration"))
        await status_msg.edit_text(f"📤 Yuborilmoqda... ({file_size_mb:.1f} MB)")

        with open(filepath, "rb") as f:
            input_file = InputFile(f, filename=os.path.basename(filepath), read_file_handle=False)
            
            if result["type"] == "photo":
                await message.reply_chat_action("upload_photo")
                await message.reply_photo(photo=input_file, caption=caption)
            elif result["type"] == "audio":
                await message.reply_chat_action("upload_voice")
                await message.reply_audio(
                    audio=input_file, caption=caption, title=result["title"][:64],
                    performer=(result.get("uploader") or "")[:64], duration=int(result.get("duration") or 0) or None,
                    write_timeout=1800, read_timeout=1800
                )
            else:
                await message.reply_chat_action("upload_video")
                await message.reply_video(
                    video=input_file, caption=caption, supports_streaming=True,
                    write_timeout=1800, read_timeout=1800
                )
        await status_msg.delete()
    except Exception as e:
        logger.exception(f"Telegram upload xatosi: {e}")
        try:
            await status_msg.edit_text("❌ Yuborishda xatolik yuz berdi.")
        except Exception:
            pass
    finally:
        try:
            os.remove(filepath)
        except OSError:
            pass
        gc.collect()

# ============================================================
# HANDLERS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Salom! 👋 Men video/musiqa yuklab beruvchi botman.\n\n"
        "📎 Instagram, YouTube, X yoki TikTok havolasini yuboring.\n"
        "🎵 Qo'shiq nomini yozing — YouTube'dan topib beraman.\n"
        "🎧 Audio/video/voice yuboring — Shazam kabi aniqlayman."
    )
    await update.message.reply_text(text)

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = URL_PATTERN.search(update.message.text or "").group(0)
    user_id = str(update.effective_user.id)
    platform = detect_platform(url)

    await notify_admin_about_link(update, context, url, platform)

    if platform == "YouTube":
        PENDING_YOUTUBE[user_id] = {"url": url}
        await update.message.reply_text(
            "🎬 YouTube video topildi.\n📊 Qaysi sifatda yuklaymiz?",
            reply_markup=youtube_quality_keyboard()
        )
        return

    status_msg = await update.message.reply_text(f"⏳ {platform}'dan yuklab olinmoqda...")
    await update.message.reply_chat_action("upload_video")

    try:
        async with DOWNLOAD_SEMAPHORE:
            result = await asyncio.to_thread(download_media, url, user_id, "720")
    except Exception as e:
        logger.exception(f"{platform} yuklash xatosi")
        await status_msg.edit_text(f"❌ Yuklashda xato:\n{str(e)[:150]}")
        return

    await download_and_send_existing(update.message, status_msg, result)

async def youtube_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    if user_id not in PENDING_YOUTUBE:
        await query.edit_message_text("❌ Tanlov eskirgan. Linkni qayta yuboring.")
        return

    url = PENDING_YOUTUBE.pop(user_id)["url"]
    quality = query.data.split(":", 1)[1]
    
    await query.edit_message_text(f"⏳ {QUALITY_LABELS.get(quality, quality)} yuklanmoqda...")
    await download_and_send(query.message, query.message, url, user_id, quality)

async def handle_text_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query_text = (update.message.text or "").strip()
    if not query_text: return

    user_id = str(update.effective_user.id)
    status_msg = await update.message.reply_text(f"🔍 Qidirilmoqda: {query_text}")
    await update.message.reply_chat_action("upload_voice")

    async with DOWNLOAD_SEMAPHORE:
        try:
            result = await asyncio.to_thread(download_audio_by_query, query_text, user_id)
        except Exception as e:
            logger.error(f"Qidiruv xatosi: {e}")
            await status_msg.edit_text(f"❌ Topilmadi:\n{str(e)[:150]}")
            return

    await download_and_send_existing(update.message, status_msg, result)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id in PENDING_FEEDBACK:
        await handle_feedback(update, context)
        return
    text = update.message.text or ""
    if URL_PATTERN.search(text):
        await handle_link(update, context)
    else:
        await handle_text_search(update, context)

# ============================================================
# CLOUD DOWNLOADER (Media aniqlash)
# ============================================================

def cloud_download_file(file_id: str, dest_path: str) -> None:
    resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"): raise RuntimeError(f"Telegram getFile xatosi")

    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{data['result']['file_path']}"
    file_resp = requests.get(file_url, timeout=120)
    file_resp.raise_for_status()

    with open(dest_path, "wb") as f:
        f.write(file_resp.content)

async def handle_media_recognition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    file_id = msg.voice.file_id if msg.voice else msg.audio.file_id if msg.audio else msg.video_note.file_id if msg.video_note else msg.video.file_id if msg.video else (msg.document.file_id if msg.document and msg.document.mime_type and msg.document.mime_type.startswith(('audio/', 'video/')) else None)
    
    if not file_id: return

    user_id = str(update.effective_user.id)
    status_msg = await msg.reply_text("🎧 Musiqa aniqlanmoqda...")
    local_path = DOWNLOAD_DIR / f"rec_{user_id}_{file_id}.mp4"

    try:
        await asyncio.to_thread(cloud_download_file, file_id, str(local_path))
        clip_path = await asyncio.to_thread(extract_recognition_clip, str(local_path))
        result = await recognize_song(clip_path)
    except Exception as e:
        logger.exception("Shazam xatosi")
        await status_msg.edit_text("❌ Musiqani aniqlashda xato.")
        return
    finally:
        for p in {str(local_path), f"{local_path}_clip.mp3"}:
            try: os.remove(p)
            except: pass
        gc.collect()

    if not result:
        await status_msg.edit_text("😕 Musiqa aniqlanmadi.")
        return

    search_query = f"{result['artist']} - {result['title']}".strip(" -")
    await status_msg.edit_text(f"🎵 Topildi: {search_query}\n⏳ MP3 yuklanmoqda...")
    await msg.reply_chat_action("upload_voice")

    async with DOWNLOAD_SEMAPHORE:
        try:
            dl_res = await asyncio.to_thread(download_audio_by_query, search_query, user_id)
        except Exception:
            await status_msg.edit_text(f"🎵 Topildi: {search_query}\n❌ Lekin audioni yuklab bo'lmadi.")
            return

    await download_and_send_existing(msg, status_msg, dl_res)

# ============================================================
# MAIN / BUILDER
# ============================================================

def build_application() -> Application:
    check_system_dependencies()
    request = HTTPXRequest(connect_timeout=60, read_timeout=1800, write_timeout=1800, pool_timeout=60)
    builder = Application.builder().token(TELEGRAM_BOT_TOKEN).request(request)

    if LOCAL_API_HOST:
        builder = builder.base_url(f"http://{LOCAL_API_HOST}/bot").base_file_url(f"http://{LOCAL_API_HOST}/file/bot")
        logger.info(f"Local Bot API server: {LOCAL_API_HOST}")

    return builder.build()

def main() -> None:
    app = build_application()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CallbackQueryHandler(youtube_quality_callback, pattern=r"^ytq:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO | filters.VIDEO_NOTE | filters.Document.AUDIO | filters.Document.VIDEO, handle_media_recognition))

    logger.info("Bot ishga tushmoqda...")
    app.run_polling()

if __name__ == "__main__":
    main()
